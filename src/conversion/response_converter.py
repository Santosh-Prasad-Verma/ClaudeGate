import asyncio
import json
import re
import uuid
from typing import Optional, Any, Dict, List
from fastapi import HTTPException, Request
from src.core.constants import Constants
from src.models.claude import ClaudeMessagesRequest


def convert_openai_to_claude_response(
    openai_response: dict, original_request: ClaudeMessagesRequest
) -> dict:
    """Convert OpenAI response to Claude format with reasoning and cache support."""

    choices = openai_response.get("choices", [])
    if not choices:
        raise HTTPException(status_code=500, detail="No choices in OpenAI response")

    choice = choices[0]
    message = choice.get("message", {})

    content_blocks = []

    # 1. Extract reasoning/thinking content
    reasoning_content = message.get("reasoning_content") or message.get("reasoning")
    text_content = message.get("content")

    # In-band <think> tag extraction if content contains thoughts
    if text_content and "<think>" in text_content and "</think>" in text_content:
        think_match = re.search(r"<think>([\s\S]*?)</think>", text_content)
        if think_match:
            if not reasoning_content:
                reasoning_content = think_match.group(1).strip()
            text_content = re.sub(r"<think>[\s\S]*?</think>", "", text_content).strip()

    if reasoning_content:
        content_blocks.append(
            {
                "type": Constants.CONTENT_THINKING,
                "thinking": reasoning_content,
                "signature": f"sig_{uuid.uuid4().hex[:16]}",
            }
        )

    # 2. Add tool calls
    tool_calls = message.get("tool_calls", []) or []
    for tool_call in tool_calls:
        if tool_call.get("type") == Constants.TOOL_FUNCTION:
            function_data = tool_call.get(Constants.TOOL_FUNCTION, {})
            try:
                arguments = json.loads(function_data.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {"raw_arguments": function_data.get("arguments", "")}

            content_blocks.append(
                {
                    "type": Constants.CONTENT_TOOL_USE,
                    "id": tool_call.get("id", f"tool_{uuid.uuid4()}"),
                    "name": function_data.get("name", ""),
                    "input": arguments,
                }
            )

    # 3. Add text content
    if text_content is not None and (text_content != "" or not tool_calls):
        content_blocks.append({"type": Constants.CONTENT_TEXT, "text": text_content})

    # Ensure at least one content block
    if not content_blocks:
        content_blocks.append({"type": Constants.CONTENT_TEXT, "text": ""})

    # Map finish reason
    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = {
        "stop": Constants.STOP_END_TURN,
        "length": Constants.STOP_MAX_TOKENS,
        "tool_calls": Constants.STOP_TOOL_USE,
        "function_call": Constants.STOP_TOOL_USE,
    }.get(finish_reason, Constants.STOP_END_TURN)

    # Build Claude response with usage and cached token metrics
    usage = openai_response.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    cache_read_tokens = 0
    prompt_tokens_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_tokens_details, dict):
        cache_read_tokens = prompt_tokens_details.get("cached_tokens", 0)

    claude_response = {
        "id": openai_response.get("id", f"msg_{uuid.uuid4()}"),
        "type": "message",
        "role": Constants.ROLE_ASSISTANT,
        "model": original_request.model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_read_input_tokens": cache_read_tokens,
        },
    }

    return claude_response


async def convert_openai_streaming_to_claude_with_cancellation(
    openai_stream,
    original_request: ClaudeMessagesRequest,
    logger,
    http_request: Optional[Request] = None,
    openai_client: Optional[Any] = None,
    request_id: Optional[str] = None,
    heartbeat_interval: Optional[float] = None,
):
    """Convert OpenAI streaming response to Claude streaming format with direct tool-streaming, extended thinking, and keep-alive pings."""

    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    # Resolve heartbeat interval
    if heartbeat_interval is None:
        try:
            from src.core.config import config
            heartbeat_interval = float(getattr(config, "heartbeat_interval", 15.0))
        except Exception:
            heartbeat_interval = 15.0

    # Send initial SSE events
    yield f"event: {Constants.EVENT_MESSAGE_START}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_START, 'message': {'id': message_id, 'type': 'message', 'role': Constants.ROLE_ASSISTANT, 'model': original_request.model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}}, ensure_ascii=False)}\n\n"
    yield f"event: {Constants.EVENT_PING}\ndata: {json.dumps({'type': Constants.EVENT_PING}, ensure_ascii=False)}\n\n"

    current_block_index = 0

    thinking_started = False
    thinking_index = None
    thinking_closed = False

    text_started = False
    text_index = None
    text_closed = False

    current_tool_calls = {}  # tc_index -> {id, name, claude_index, started, arguments_sent}
    final_stop_reason = Constants.STOP_END_TURN
    usage_data = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

    # In-band <think> tag state machine
    in_think_tag = False
    tag_buffer = ""

    # Safe queue-based stream consumer that supports non-destructive timeout pings
    stream_queue = asyncio.Queue()
    stream_done_sentinel = object()

    async def _stream_producer():
        try:
            async for item in openai_stream:
                await stream_queue.put(item)
        except Exception as exc:
            await stream_queue.put(exc)
        finally:
            await stream_queue.put(stream_done_sentinel)

    producer_task = asyncio.create_task(_stream_producer())

    try:
        while True:
            # Check client disconnect
            if http_request and await http_request.is_disconnected():
                logger.info("Client disconnected, cancelling request %s", request_id)
                if openai_client and request_id:
                    openai_client.cancel_request(request_id)
                break

            try:
                if heartbeat_interval and heartbeat_interval > 0:
                    item = await asyncio.wait_for(stream_queue.get(), timeout=heartbeat_interval)
                else:
                    item = await stream_queue.get()
            except asyncio.TimeoutError:
                # Upstream LLM is in deep thinking or slow generation: send keep-alive ping to prevent client ECONNRESET
                yield f"event: {Constants.EVENT_PING}\ndata: {json.dumps({'type': Constants.EVENT_PING}, ensure_ascii=False)}\n\n"
                continue

            if item is stream_done_sentinel:
                break
            if isinstance(item, Exception):
                raise item

            line = item

            if not line.strip():
                continue

            # Handle error markers from client.py
            if line.startswith("ERROR::"):
                parts = line.split("::", 2)
                error_code = parts[1] if len(parts) > 1 else "500"
                error_msg = parts[2] if len(parts) > 2 else "Unknown upstream error"
                logger.error("Upstream stream failed with status %s: %s", error_code, error_msg)

                error_type = "overloaded_error" if error_code in ("503", "502", "504", "429") else "api_error"
                error_event = {"type": "error", "error": {"type": error_type, "message": error_msg}}
                yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"

                # Close any active block
                if thinking_started and not thinking_closed:
                    yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': thinking_index}, ensure_ascii=False)}\n\n"
                if text_started and not text_closed:
                    yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': text_index}, ensure_ascii=False)}\n\n"

                yield f"event: {Constants.EVENT_MESSAGE_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_DELTA, 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': usage_data}, ensure_ascii=False)}\n\n"
                yield f"event: {Constants.EVENT_MESSAGE_STOP}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_STOP}, ensure_ascii=False)}\n\n"
                return

            if line.startswith("data: "):
                chunk_data = line[6:]
                if chunk_data.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(chunk_data)
                except json.JSONDecodeError as e:
                    logger.warning("Failed to parse chunk: %s, error: %s", chunk_data, e)
                    continue

                usage = chunk.get("usage", None)
                if usage:
                    cache_read_input_tokens = 0
                    prompt_tokens_details = usage.get("prompt_tokens_details", {})
                    if prompt_tokens_details:
                        cache_read_input_tokens = prompt_tokens_details.get("cached_tokens", 0)
                    usage_data = {
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                        "cache_read_input_tokens": cache_read_input_tokens,
                    }

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                # 1. Handle upstream reasoning (DeepSeek reasoning_content, Groq reasoning)
                reasoning_chunk = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning_chunk:
                    if not thinking_started:
                        thinking_started = True
                        thinking_index = current_block_index
                        current_block_index += 1
                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': thinking_index, 'content_block': {'type': Constants.CONTENT_THINKING, 'thinking': ''}}, ensure_ascii=False)}\n\n"

                    yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': thinking_index, 'delta': {'type': Constants.DELTA_THINKING, 'thinking': reasoning_chunk}}, ensure_ascii=False)}\n\n"

                # 2. Handle text content (with in-band <think> tag detection)
                content_chunk = delta.get("content")
                if content_chunk is not None:
                    # In-band thinking tags handler
                    if "<think>" in content_chunk and not in_think_tag:
                        in_think_tag = True
                        parts = content_chunk.split("<think>", 1)
                        content_chunk = parts[0]
                        tag_buffer = parts[1]
                        if not thinking_started:
                            thinking_started = True
                            thinking_index = current_block_index
                            current_block_index += 1
                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': thinking_index, 'content_block': {'type': Constants.CONTENT_THINKING, 'thinking': ''}}, ensure_ascii=False)}\n\n"

                    if in_think_tag:
                        full_think = tag_buffer + content_chunk if tag_buffer else content_chunk
                        tag_buffer = ""
                        if "</think>" in full_think:
                            in_think_tag = False
                            think_part, rem_content = full_think.split("</think>", 1)
                            if think_part:
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': thinking_index, 'delta': {'type': Constants.DELTA_THINKING, 'thinking': think_part}}, ensure_ascii=False)}\n\n"
                            # Close thinking block immediately
                            thinking_closed = True
                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': thinking_index}, ensure_ascii=False)}\n\n"
                            content_chunk = rem_content
                        else:
                            if full_think:
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': thinking_index, 'delta': {'type': Constants.DELTA_THINKING, 'thinking': full_think}}, ensure_ascii=False)}\n\n"
                            content_chunk = None

                    if content_chunk:
                        # Close thinking if it hasn't been closed
                        if thinking_started and not thinking_closed:
                            thinking_closed = True
                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': thinking_index}, ensure_ascii=False)}\n\n"

                        if not text_started:
                            text_started = True
                            text_index = current_block_index
                            current_block_index += 1
                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': text_index, 'content_block': {'type': Constants.CONTENT_TEXT, 'text': ''}}, ensure_ascii=False)}\n\n"

                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': text_index, 'delta': {'type': Constants.DELTA_TEXT, 'text': content_chunk}}, ensure_ascii=False)}\n\n"

                # 3. Handle tool call deltas with immediate stream-through (NO json.loads gating)
                if "tool_calls" in delta and delta["tool_calls"]:
                    # Close thinking block if still open
                    if thinking_started and not thinking_closed:
                        thinking_closed = True
                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': thinking_index}, ensure_ascii=False)}\n\n"

                    # Close text block if still open
                    if text_started and not text_closed:
                        text_closed = True
                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': text_index}, ensure_ascii=False)}\n\n"

                    for tc_delta in delta["tool_calls"]:
                        tc_index = tc_delta.get("index", 0)

                        if tc_index not in current_tool_calls:
                            current_tool_calls[tc_index] = {
                                "id": tc_delta.get("id") or f"tool_{uuid.uuid4().hex[:12]}",
                                "name": tc_delta.get(Constants.TOOL_FUNCTION, {}).get("name", ""),
                                "claude_index": None,
                                "started": False,
                                "arguments_sent": False,
                            }

                        tool_call = current_tool_calls[tc_index]

                        if tc_delta.get("id"):
                            tool_call["id"] = tc_delta["id"]

                        fn_data = tc_delta.get(Constants.TOOL_FUNCTION, {})
                        if fn_data.get("name"):
                            tool_call["name"] = fn_data["name"]

                        # Start content block once name or id is available
                        if not tool_call["started"] and tool_call["name"]:
                            tool_call["claude_index"] = current_block_index
                            current_block_index += 1
                            tool_call["started"] = True

                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': tool_call['claude_index'], 'content_block': {'type': Constants.CONTENT_TOOL_USE, 'id': tool_call['id'], 'name': tool_call['name'], 'input': {}}}, ensure_ascii=False)}\n\n"

                        # Stream arguments chunk IMMEDIATELY without waiting or buffering
                        if fn_data.get("arguments"):
                            args_snippet = fn_data["arguments"]
                            if not tool_call["started"]:
                                # Tool had no name in previous packet, start it now
                                tool_call["claude_index"] = current_block_index
                                current_block_index += 1
                                tool_call["started"] = True
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': tool_call['claude_index'], 'content_block': {'type': Constants.CONTENT_TOOL_USE, 'id': tool_call['id'], 'name': tool_call['name'] or 'tool', 'input': {}}}, ensure_ascii=False)}\n\n"

                            tool_call["arguments_sent"] = True
                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': tool_call['claude_index'], 'delta': {'type': Constants.DELTA_INPUT_JSON, 'partial_json': args_snippet}}, ensure_ascii=False)}\n\n"

                # 4. Handle finish reason
                if finish_reason:
                    if finish_reason == "length":
                        final_stop_reason = Constants.STOP_MAX_TOKENS
                    elif finish_reason in ("tool_calls", "function_call"):
                        final_stop_reason = Constants.STOP_TOOL_USE
                    elif finish_reason == "stop":
                        final_stop_reason = Constants.STOP_END_TURN
                    else:
                        final_stop_reason = Constants.STOP_END_TURN

    except HTTPException as e:
        if e.status_code == 499:
            logger.info("Request %s was cancelled", request_id)
            error_event = {
                "type": "error",
                "error": {
                    "type": "cancelled",
                    "message": "Request was cancelled by client",
                },
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            return
        raise
    except asyncio.CancelledError:
        logger.info("Streaming request %s cancelled (client disconnected)", request_id)
        if openai_client and request_id:
            openai_client.cancel_request(request_id)
        return
    except Exception as e:
        logger.error("Streaming error: %s", type(e).__name__)
        error_event = {
            "type": "error",
            "error": {"type": "api_error", "message": "Streaming error from upstream provider"},
        }
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        return

    try:
        # Final stream closure: close any open blocks
        if thinking_started and not thinking_closed:
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': thinking_index}, ensure_ascii=False)}\n\n"

        if text_started and not text_closed:
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': text_index}, ensure_ascii=False)}\n\n"

        # Close active tool calls
        for tool_data in current_tool_calls.values():
            if tool_data.get("started") and tool_data.get("claude_index") is not None:
                if not tool_data.get("arguments_sent"):
                    yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': tool_data['claude_index'], 'delta': {'type': Constants.DELTA_INPUT_JSON, 'partial_json': '{}'}}, ensure_ascii=False)}\n\n"
                yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': tool_data['claude_index']}, ensure_ascii=False)}\n\n"

        # If no content block was ever opened, ensure at least one empty text block was emitted
        if not thinking_started and not text_started and not current_tool_calls:
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': 0, 'content_block': {'type': Constants.CONTENT_TEXT, 'text': ''}}, ensure_ascii=False)}\n\n"
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': 0}, ensure_ascii=False)}\n\n"

        yield f"event: {Constants.EVENT_MESSAGE_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_DELTA, 'delta': {'stop_reason': final_stop_reason, 'stop_sequence': None}, 'usage': usage_data}, ensure_ascii=False)}\n\n"
        yield f"event: {Constants.EVENT_MESSAGE_STOP}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_STOP}, ensure_ascii=False)}\n\n"
    finally:
        if not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):
                pass


async def convert_openai_streaming_to_claude(
    openai_stream, original_request: ClaudeMessagesRequest, logger
):
    """Backwards-compatible wrapper delegating to streaming converter with cancellation."""
    async for item in convert_openai_streaming_to_claude_with_cancellation(
        openai_stream, original_request, logger
    ):
        yield item
