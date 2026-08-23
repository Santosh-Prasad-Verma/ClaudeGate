#!/usr/bin/env python3
"""
ClaudeGate Automatic Fallback / Failover Verification Script
Simulates a primary provider failure (HTTP 500 / Overload) and verifies
that ClaudeGate seamlessly routes the request to the backup/fallback model.
"""

import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.client import OpenAIClient
from src.conversion.response_converter import convert_openai_to_claude_response


async def run_failover_simulation():
    print("\n" + "="*70)
    print(" 🛡️  ClaudeGate Fallback & Failover Verification Suite")
    print("="*70 + "\n")

    # 1. Initialize client with Primary & Fallback providers
    print("🔹 [1/3] Initializing OpenAIClient with Dual-Provider Config:")
    print("   • Primary Provider:  https://primary-overloaded-api.com/v1 (Model: stealth/ox-alpha)")
    print("   • Fallback Provider: https://backup-failover-api.com/v1   (Model: deepseek-v4-pro)")
    
    client = OpenAIClient(
        api_key="sk-primary-mock-key",
        base_url="https://primary-overloaded-api.com/v1",
        fallback_base_url="https://backup-failover-api.com/v1",
        fallback_api_key="sk-fallback-mock-key",
        fallback_model="deepseek-v4-pro",
    )

    # 2. Simulate failure on Primary Client (e.g. 500 Overloaded / Concurrency Limit Exceeded)
    print("\n🔹 [2/3] Simulating upstream failure on primary provider...")
    mock_primary_failure = AsyncMock(
        side_effect=Exception("500 Internal Server Error: Upstream service overloaded (concurrency limit reached)")
    )
    client.client.chat.completions.create = mock_primary_failure

    # 3. Simulate success on Fallback Client
    expected_fallback_output = {
        "id": "chatcmpl-fallback-test-999",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "✅ Fallback Successful: Received prompt from Claude Code and generated code via DeepSeek V4-Pro!"
            },
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 24, "completion_tokens": 18, "total_tokens": 42}
    }
    mock_fallback_success = AsyncMock()
    mock_obj = MagicMock()
    mock_obj.model_dump.return_value = expected_fallback_output
    mock_fallback_success.return_value = mock_obj
    client.fallback_client.chat.completions.create = mock_fallback_success

    # 4. Execute request with immediate sleep
    request_data = {
        "model": "stealth/ox-alpha",
        "messages": [{"role": "user", "content": "Write a python function."}],
        "temperature": 0.7,
        "max_tokens": 1000
    }

    with patch("asyncio.sleep", return_value=None):
        raw_response = await client.create_chat_completion(request_data)

    from src.models.claude import ClaudeMessagesRequest, ClaudeMessage
    claude_req = ClaudeMessagesRequest(
        model="claude-3-5-sonnet-20241022",
        messages=[ClaudeMessage(role="user", content="Write a python function.")],
        max_tokens=1000
    )
    claude_response = convert_openai_to_claude_response(raw_response, claude_req)

    response_text = claude_response["content"][0]["text"]

    print("\n🔹 [3/3] Inspecting failover results:")
    print(f"   • Primary Provider Called:  {mock_primary_failure.called} (Failed as expected)")
    print(f"   • Fallback Provider Called: {mock_fallback_success.called} (Triggered automatically)")
    print(f"   • Fallback Model Used:      {mock_fallback_success.call_args[1].get('model')}")
    print(f"   • Response Delivered:       \"{response_text}\"")

    assert mock_fallback_success.called, "Fallback was not called!"
    assert response_text.startswith("✅ Fallback Successful")

    print("\n" + "="*70)
    print(" 🎉 ALL CHECKS PASSED: Automatic Failover is working 100% properly!")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(run_failover_simulation())
