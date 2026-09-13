import json
import time
import asyncio
import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.main import app
from src.core.client import CircuitBreaker, OpenAIClient
from src.core.flight_recorder import flight_recorder
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage, ClaudeTool, ClaudeTokenCountRequest
from src.conversion.request_converter import (
    convert_claude_to_openai,
    compact_message_history,
    is_text_only_model,
)
from src.conversion.response_converter import (
    convert_openai_to_claude_response,
    convert_openai_streaming_to_claude_with_cancellation,
)
from src.core.model_manager import model_manager


@pytest.fixture
def client():
    return TestClient(app)


def test_circuit_breaker_transitions():
    """Verify Circuit Breaker transitions from CLOSED to OPEN, then HALF_OPEN and back to CLOSED."""
    cb = CircuitBreaker(threshold=3, reset_timeout=0.1, enabled=True)
    assert cb.state == "CLOSED"
    assert cb.can_attempt_primary() is True

    # 1st failure
    cb.record_failure()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 1

    # 2nd failure
    cb.record_failure()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 2

    # 3rd failure trips breaker
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.can_attempt_primary() is False

    # Wait for reset timeout
    time.sleep(0.12)
    # Probing should transition to HALF_OPEN
    assert cb.can_attempt_primary() is True
    assert cb.state == "HALF_OPEN"

    # On success in HALF_OPEN, reset to CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0


def test_tool_choice_required_and_parallelism_mapping():
    """Verify that Anthropic 'any' maps to OpenAI 'required' and disable_parallel_tool_use maps to parallel_tool_calls: False."""
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[ClaudeMessage(role="user", content="Call any tool")],
        tools=[
            ClaudeTool(
                name="bash",
                description="Run command",
                input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
            )
        ],
        tool_choice={"type": "any", "disable_parallel_tool_use": True},
    )
    openai_req = convert_claude_to_openai(claude_req, model_manager)
    assert openai_req.get("tool_choice") == "required"
    assert openai_req.get("parallel_tool_calls") is False


def test_orphan_tool_call_reconciliation():
    """Verify that missing tool responses after an assistant tool_call are filled with synthetic tool results."""
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[
            ClaudeMessage(role="user", content="Execute actions"),
            ClaudeMessage(
                role="assistant",
                content=[
                    {"type": "text", "text": "I will run two tools"},
                    {"type": "tool_use", "id": "call_1", "name": "bash", "input": {"cmd": "ls"}},
                    {"type": "tool_use", "id": "call_2", "name": "view_file", "input": {"path": "a.txt"}},
                ],
            ),
            # Only tool result for call_1 was provided
            ClaudeMessage(
                role="user",
                content=[
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "file1.txt\nfile2.txt"}
                ],
            ),
        ],
    )
    openai_req = convert_claude_to_openai(claude_req, model_manager)
    messages = openai_req["messages"]

    # Check that call_2 was synthetically reconciled
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    tool_call_ids = [m.get("tool_call_id") for m in tool_messages]
    assert "call_1" in tool_call_ids
    assert "call_2" in tool_call_ids
    call_2_msg = next(m for m in tool_messages if m.get("tool_call_id") == "call_2")
    assert "skipped or interrupted" in call_2_msg["content"]


def test_mixed_user_message_preservation():
    """Verify that when a user message contains both tool_result and text, neither is dropped."""
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[
            ClaudeMessage(role="user", content="Start"),
            ClaudeMessage(
                role="assistant",
                content=[
                    {"type": "tool_use", "id": "call_x", "name": "bash", "input": {"cmd": "pwd"}}
                ],
            ),
            ClaudeMessage(
                role="user",
                content=[
                    {"type": "tool_result", "tool_use_id": "call_x", "content": "/home/user"},
                    {"type": "text", "text": "Please now cd to /tmp"},
                ],
            ),
        ],
    )
    openai_req = convert_claude_to_openai(claude_req, model_manager)
    messages = openai_req["messages"]

    # We should have a tool message for call_x AND a user message with "Please now cd to /tmp"
    tool_msg = next((m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == "call_x"), None)
    assert tool_msg is not None
    assert tool_msg["content"] == "/home/user"

    user_text_msg = next((m for m in messages if m.get("role") == "user" and "Please now cd to /tmp" in str(m.get("content"))), None)
    assert user_text_msg is not None


def test_non_streaming_reasoning_and_thinking_extraction():
    """Verify that reasoning_content is converted to an Anthropic thinking block with signature."""
    raw_response = {
        "id": "chatcmpl-test-reasoning",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "reasoning_content": "Let me think through this problem step-by-step...",
                    "content": "Here is the final answer: 42.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 20},
        },
    }
    claude_req = ClaudeMessagesRequest(
        model="claude-3-7-sonnet-20250219",
        messages=[ClaudeMessage(role="user", content="Solve this")],
    )
    res = convert_openai_to_claude_response(raw_response, claude_req)

    # Content should have both thinking block and text block
    blocks = res["content"]
    assert len(blocks) == 2
    assert blocks[0]["type"] == "thinking"
    assert "step-by-step" in blocks[0]["thinking"]
    assert blocks[0].get("signature") is not None
    assert blocks[1]["type"] == "text"
    assert "42" in blocks[1]["text"]

    # Verify cached tokens
    assert res["usage"]["cache_read_input_tokens"] == 20


@pytest.mark.asyncio
async def test_streaming_direct_tool_arguments_stream_through():
    """Verify tool arguments are streamed immediately as input_json_delta without json.loads delay."""
    async def mock_stream_chunks():
        # First chunk: tool start with partial arguments
        chunk1 = {
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_stream_1",
                        "function": {"name": "bash", "arguments": '{"command":'}
                    }]
                },
                "finish_reason": None
            }]
        }
        yield f"data: {json.dumps(chunk1)}\n\n"

        # Second chunk: remaining argument snippet
        chunk2 = {
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": ' "uptime"}'}
                    }]
                },
                "finish_reason": "tool_calls"
            }]
        }
        yield f"data: {json.dumps(chunk2)}\n\n"
        yield "data: [DONE]\n\n"

    logger_mock = MagicMock()
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[ClaudeMessage(role="user", content="Run uptime")],
    )

    events = []
    async for event in convert_openai_streaming_to_claude_with_cancellation(
        mock_stream_chunks(), claude_req, logger_mock
    ):
        events.append(event)

    parsed_deltas = []
    for e in events:
        for line in e.split("\n"):
            if line.startswith("data: "):
                try:
                    payload = json.loads(line[6:])
                    if payload.get("type") == "content_block_delta":
                        delta_obj = payload.get("delta", {})
                        if delta_obj.get("type") == "input_json_delta":
                            parsed_deltas.append(delta_obj.get("partial_json"))
                except Exception:
                    pass

    # Assert content_block_start for tool_use
    assert any('content_block_start' in e and '"type": "tool_use"' in e for e in events)
    # Assert that partial_json '{"command":' was sent in a delta
    assert any('{"command":' in p for p in parsed_deltas)
    # Assert that partial_json ' "uptime"}' was sent in a subsequent delta
    assert any('"uptime"}' in p for p in parsed_deltas)
    # Assert content_block_stop
    assert any('content_block_stop' in e for e in events)
    # Assert message_stop
    assert any('message_stop' in e for e in events)


@pytest.mark.asyncio
async def test_streaming_reasoning_content_to_thinking_events():
    """Verify that delta.reasoning_content is translated into thinking_delta streaming events."""
    async def mock_stream_chunks():
        chunk1 = {
            "choices": [{
                "index": 0,
                "delta": {"reasoning_content": "Analyzing codebase structure..."},
                "finish_reason": None
            }]
        }
        yield f"data: {json.dumps(chunk1)}\n\n"

        chunk2 = {
            "choices": [{
                "index": 0,
                "delta": {"content": "Found 5 files."},
                "finish_reason": "stop"
            }]
        }
        yield f"data: {json.dumps(chunk2)}\n\n"
        yield "data: [DONE]\n\n"

    logger_mock = MagicMock()
    claude_req = ClaudeMessagesRequest(
        model="claude-3-7-sonnet-20250219",
        messages=[ClaudeMessage(role="user", content="Analyze")],
    )

    events = []
    async for event in convert_openai_streaming_to_claude_with_cancellation(
        mock_stream_chunks(), claude_req, logger_mock
    ):
        events.append(event)

    # Verify thinking block start
    assert any('content_block_start' in e and '"type": "thinking"' in e for e in events)
    # Verify thinking delta
    assert any('thinking_delta' in e and 'Analyzing codebase structure' in e for e in events)
    # Verify text delta
    assert any('text_delta' in e and 'Found 5 files.' in e for e in events)


def test_token_counter_with_tools_included(client):
    """Verify /v1/messages/count_tokens counts tool schemas in addition to message content."""
    from src.core.config import config
    auth_header = config.anthropic_api_key if config.anthropic_api_key else "sk-claudegate-local"
    headers = {"x-api-key": auth_header}

    payload_without_tools = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hi"}],
    }
    res1 = client.post("/v1/messages/count_tokens", json=payload_without_tools, headers=headers)
    assert res1.status_code == 200
    count_without = res1.json()["input_tokens"]

    payload_with_tools = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hi"}],
        "tools": [
            {
                "name": "edit_file",
                "description": "Perform non-contiguous edits across multiple line blocks in an existing file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "TargetFile": {"type": "string"},
                        "ReplacementChunks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "StartLine": {"type": "integer"},
                                    "EndLine": {"type": "integer"},
                                    "TargetContent": {"type": "string"},
                                    "ReplacementContent": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["TargetFile", "ReplacementChunks"],
                },
            }
        ],
    }
    res2 = client.post("/v1/messages/count_tokens", json=payload_with_tools, headers=headers)
    assert res2.status_code == 200
    count_with = res2.json()["input_tokens"]

    # Tool schema should noticeably increase token count
    assert count_with > count_without
    assert count_with >= count_without + 50


def test_dashboard_and_flight_recorder_endpoints(client):
    """Verify /dashboard returns HTML and /api/flight-recorder returns JSON metrics."""
    from src.core.config import config
    auth_header = config.anthropic_api_key if config.anthropic_api_key else "sk-claudegate-local"
    headers = {"x-api-key": auth_header}

    # Test dashboard HTML
    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    assert "text/html" in res_dash.headers["content-type"]
    assert "ClaudeGate Live Console" in res_dash.text
    assert "Circuit:" in res_dash.text

    # Record a test event
    flight_recorder.record_completion(
        request_id="test-req-xyz",
        claude_model="claude-3-5-sonnet-20241022",
        target_model="deepseek-v4-flash",
        stream=True,
        duration_ms=250.5,
        status_code=200,
        input_tokens=100,
        output_tokens=50,
        cached_tokens=25,
        tools_called=["bash"],
    )

    # Test JSON API
    res_api = client.get("/api/flight-recorder", headers=headers)
    assert res_api.status_code == 200
    data = res_api.json()
    assert "summary" in data
    assert "recent_requests" in data
    assert "circuit_breaker" in data
    assert data["summary"]["total_requests"] >= 1
    assert any(r["id"] == "test-req-xyz" for r in data["recent_requests"])


@pytest.mark.asyncio
async def test_streaming_heartbeat_pings():
    """Verify that delays longer than heartbeat_interval trigger SSE ping events without corrupting stream."""
    async def delayed_generator():
        yield "data: {\"choices\": [{\"delta\": {\"content\": \"Hello\"}}]}\n\n"
        await asyncio.sleep(0.25)
        yield "data: {\"choices\": [{\"delta\": {\"content\": \" world\"}}]}\n\n"
        yield "data: [DONE]\n\n"

    logger_mock = MagicMock()
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[ClaudeMessage(role="user", content="Hi")],
    )

    events = []
    async for evt in convert_openai_streaming_to_claude_with_cancellation(
        delayed_generator(),
        claude_req,
        logger_mock,
        heartbeat_interval=0.1,
    ):
        events.append(evt)

    # Verify that at least one heartbeat ping was emitted in addition to initial connection ping
    ping_events = [e for e in events if "event: ping" in e]
    assert len(ping_events) >= 2
    # Verify stream content delivered cleanly
    assert any("Hello" in e for e in events)
    assert any("world" in e for e in events)


def test_context_overflow_guard_compaction():
    """Verify that ancient verbose tool outputs are compacted when exceeding max_context_tokens."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are a helpful coding agent."},
        {"role": "user", "content": "Start task"},
    ]
    # Add 15 turns of tool execution with massive 10,000 char outputs
    for i in range(15):
        messages.append({
            "role": "assistant",
            "content": f"Running tool {i}",
            "tool_calls": [{"id": f"tc_{i}", "function": {"name": "bash", "arguments": "{}"}}],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"tc_{i}",
            "content": "A" * 5000 + f" important result {i} " + "B" * 5000,
        })

    # With max_context_tokens=1000 (very small threshold to trigger guard)
    compacted = compact_message_history(messages, max_context_tokens=1000)

    assert len(compacted) == len(messages)
    assert compacted[0]["content"] == "You are a helpful coding agent."

    # Ancient tool outputs (e.g. index 3) should have been compacted
    ancient_tool = compacted[3]
    assert ancient_tool["role"] == "tool"
    assert ancient_tool["tool_call_id"] == "tc_0"
    assert "ClaudeGate Context Guard" in ancient_tool["content"]
    assert len(ancient_tool["content"]) < 1500

    # Recent tool outputs (within last 10 messages) must NOT be compacted
    recent_tool = compacted[-1]
    assert recent_tool["role"] == "tool"
    assert "ClaudeGate Context Guard" not in recent_tool["content"]
    assert len(recent_tool["content"]) > 10000


def test_o1_o3_reasoning_effort_and_tokens_mapping():
    """Verify that o1/o3 models map max_tokens -> max_completion_tokens, omit temperature, and translate reasoning effort."""
    # Test low reasoning effort (< 2048)
    req_low = ClaudeMessagesRequest(
        model="claude-3-7-sonnet-20250219",
        messages=[ClaudeMessage(role="user", content="Solve logic puzzle")],
        thinking={"type": "enabled", "budget_tokens": 1024},
        max_tokens=4000,
        temperature=0.7,
    )
    with patch.object(model_manager, "map_claude_model_to_openai", return_value="o3-mini"):
        res_low = convert_claude_to_openai(req_low, model_manager)
        assert res_low["model"] == "o3-mini"
        assert "max_completion_tokens" in res_low
        assert "max_tokens" not in res_low
        assert "temperature" not in res_low
        assert res_low.get("reasoning_effort") == "low"

    # Test medium reasoning effort (2048-8192)
    req_med = ClaudeMessagesRequest(
        model="claude-3-7-sonnet-20250219",
        messages=[ClaudeMessage(role="user", content="Solve deep math")],
        thinking={"type": "enabled", "budget_tokens": 4096},
    )
    with patch.object(model_manager, "map_claude_model_to_openai", return_value="o1-preview"):
        res_med = convert_claude_to_openai(req_med, model_manager)
        assert res_med["reasoning_effort"] == "medium"

    # Test high reasoning effort (> 8192)
    req_high = ClaudeMessagesRequest(
        model="claude-3-7-sonnet-20250219",
        messages=[ClaudeMessage(role="user", content="Complex proof")],
        thinking={"type": "enabled", "budget_tokens": 16000},
    )
    with patch.object(model_manager, "map_claude_model_to_openai", return_value="o1"):
        res_high = convert_claude_to_openai(req_high, model_manager)
        assert res_high["reasoning_effort"] == "high"


def test_vision_fallback_and_graceful_degradation():
    """Verify that requests with images auto-fallback or gracefully degrade on text-only models."""
    image_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[
            ClaudeMessage(
                role="user",
                content=[
                    {"type": "text", "text": "What is in this image?"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
                    },
                ],
            )
        ],
    )

    # Case 1: Vision fallback model configured
    with patch.object(model_manager, "map_claude_model_to_openai", return_value="deepseek-coder"), \
         patch("src.conversion.request_converter.config.vision_fallback_model", "gpt-4o-mini"):
        res = convert_claude_to_openai(image_req, model_manager)
        # Should auto-route model to gpt-4o-mini
        assert res["model"] == "gpt-4o-mini"
        user_msg = res["messages"][0]
        assert isinstance(user_msg["content"], list)
        assert any(b.get("type") == "image_url" for b in user_msg["content"])

    # Case 2: No vision fallback model configured (or fallback model disabled/text-only)
    with patch.object(model_manager, "map_claude_model_to_openai", return_value="deepseek-coder"), \
         patch("src.conversion.request_converter.config.vision_fallback_model", None):
        res = convert_claude_to_openai(image_req, model_manager)
        # Should keep model but gracefully degrade image to text block
        assert res["model"] == "deepseek-coder"
        user_msg = res["messages"][0]
        assert isinstance(user_msg["content"], list)
        assert not any(b.get("type") == "image_url" for b in user_msg["content"])
        text_parts = [b.get("text", "") for b in user_msg["content"] if b.get("type") == "text"]
        assert any("Attached image" in t and "image/png" in t for t in text_parts)


@pytest.mark.asyncio
async def test_multi_key_round_robin_and_429_failover():
    """Verify multi-key pool rotates round-robin and fails over upon 429 rate limit."""
    keys = ["sk-key-alpha", "sk-key-beta", "sk-key-gamma"]
    test_client = OpenAIClient(
        api_key=keys[0],
        base_url="https://api.openai.com/v1",
        api_keys=keys,
        circuit_breaker_enabled=False,
    )

    # 1. Round-robin verification
    _, k1 = test_client.get_client_and_key_for_model("gpt-4o")
    _, k2 = test_client.get_client_and_key_for_model("gpt-4o")
    _, k3 = test_client.get_client_and_key_for_model("gpt-4o")
    assert [k1, k2, k3] == ["sk-key-alpha", "sk-key-beta", "sk-key-gamma"]

    # 2. Cooldown skipping
    test_client.record_key_rate_limit("sk-key-beta", cooldown_seconds=30.0)
    _, k_next = test_client.get_client_and_key_for_model("gpt-4o")
    assert k_next != "sk-key-beta"


def test_models_catalog_endpoint(client):
    """Verify /v1/models returns catalog for OpenCode, Codex, Aider, and Cline discovery."""
    from src.core.config import config
    auth_header = config.anthropic_api_key if config.anthropic_api_key else "sk-claudegate-local"
    headers = {"x-api-key": auth_header}

    res = client.get("/v1/models", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["object"] == "list"
    model_ids = [m["id"] for m in data["data"]]
    assert "claude-3-5-sonnet-20241022" in model_ids
    assert config.big_model in model_ids


def test_openai_chat_completions_endpoint(client):
    """Verify /v1/chat/completions works for CLI agents speaking OpenAI protocol (Codex CLI, CommandCode)."""
    from src.core.config import config
    auth_header = config.anthropic_api_key if config.anthropic_api_key else "sk-claudegate-local"
    headers = {"x-api-key": auth_header}

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False,
    }

    mock_response = {
        "id": "chatcmpl-test-cli",
        "choices": [{"message": {"role": "assistant", "content": "Hello from ClaudeGate"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }

    with patch("src.api.endpoints.openai_client.create_chat_completion", new_callable=AsyncMock) as mock_cc:
        mock_cc.return_value = mock_response
        res = client.post("/v1/chat/completions", json=payload, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["choices"][0]["message"]["content"] == "Hello from ClaudeGate"


def test_openai_responses_endpoint(client):
    """Verify /v1/responses works for Codex CLI with OpenAI Responses API wire schema."""
    from src.core.config import config
    auth_header = config.anthropic_api_key if config.anthropic_api_key else "sk-claudegate-local"
    headers = {"Authorization": f"Bearer {auth_header}"}

    payload = {
        "model": "deepseek-v4-flash",
        "instructions": "You are a coding assistant.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hello world"}]
            }
        ],
        "tools": [
            {
                "type": "function",
                "name": "run_terminal",
                "description": "Run shell command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}
            }
        ],
        "stream": False,
    }

    mock_chat_response = {
        "id": "chatcmpl-test-resp",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello back from Responses API",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "run_terminal", "arguments": "{\"cmd\": \"ls\"}"}
                        }
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }

    with patch("src.api.endpoints.openai_client.create_chat_completion", new_callable=AsyncMock) as mock_cc:
        mock_cc.return_value = mock_chat_response
        res = client.post("/v1/responses", json=payload, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["object"] == "response"
        assert data["status"] == "completed"
        assert len(data["output"]) == 2
        assert data["output"][0]["type"] == "message"
        assert data["output"][0]["content"][0]["text"] == "Hello back from Responses API"
        assert data["output"][1]["type"] == "function_call"
        assert data["output"][1]["name"] == "run_terminal"



