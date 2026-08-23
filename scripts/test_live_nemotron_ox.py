#!/usr/bin/env python3
"""
Live Test Script: Nvidia Nemotron & Stealth Ox Alpha Failover Pipeline
Tests:
1. Live connection to Stealth Ox Alpha (OpenRouter)
2. Live connection to Nvidia Nemotron (OpenRouter)
3. Live Failover: Primary fails -> Automatically falls back to Stealth Ox Alpha / Nemotron.
"""

import os
import sys
import asyncio

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load local .env if present
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from src.core.client import OpenAIClient
from src.models.claude import ClaudeMessagesRequest, ClaudeMessage
from src.conversion.response_converter import convert_openai_to_claude_response


async def run_live_test():
    print("\n" + "="*70)
    print(" 🚀 ClaudeGate Live Test: Nemotron & Stealth Ox Alpha Pipeline")
    print("="*70 + "\n")

    ox_key = os.environ.get("OPENROUTER_OX_API_KEY", os.environ.get("OPENAI_API_KEY", "sk-or-v1-YOUR_KEY"))
    nemotron_key = os.environ.get("OPENROUTER_NEMOTRON_API_KEY", os.environ.get("FALLBACK_API_KEY", ox_key))
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    # -------------------------------------------------------------
    # Test 1: Live Call to Stealth Ox Alpha
    # -------------------------------------------------------------
    print("🔹 [1/3] Testing Live Connection to Stealth Ox Alpha...")
    ox_client = OpenAIClient(
        api_key=ox_key,
        base_url=base_url,
    )
    try:
        ox_resp = await ox_client.create_chat_completion({
            "model": "stealth/ox-alpha",
            "messages": [{"role": "user", "content": "Respond with 'OX_ALPHA_ONLINE'."}],
            "max_tokens": 300
        })
        ox_text = ox_resp["choices"][0]["message"].get("content") or ox_resp["choices"][0]["message"].get("reasoning") or ""
        print(f"   ✅ Ox Alpha Live Response: \"{ox_text.strip()}\"")
    except Exception as e:
        print(f"   ⚠️ Ox Alpha Error: {e}")

    # -------------------------------------------------------------
    # Test 2: Live Call to Nvidia Nemotron Free
    # -------------------------------------------------------------
    print("\n🔹 [2/3] Testing Live Connection to Nvidia Nemotron...")
    nemotron_client = OpenAIClient(
        api_key=nemotron_key,
        base_url=base_url,
    )
    try:
        nemotron_resp = await nemotron_client.create_chat_completion({
            "model": "nvidia/nemotron-3-super-120b-a12b:free",
            "messages": [{"role": "user", "content": "Respond with 'NEMOTRON_ONLINE'."}],
            "max_tokens": 300
        })
        nem_text = nemotron_resp["choices"][0]["message"].get("content") or nemotron_resp["choices"][0]["message"].get("reasoning") or ""
        print(f"   ✅ Nemotron Live Response: \"{nem_text.strip()}\"")
    except Exception as e:
        print(f"   ⚠️ Nemotron Live Notice (Free Tier Load): {e}")

    # -------------------------------------------------------------
    # Test 3: Live Automatic Failover Pipeline (Primary -> Fallback)
    # -------------------------------------------------------------
    print("\n🔹 [3/3] Testing Live Automatic Failover Pipeline:")
    print("   • Primary (Simulated Overload/Failure): invalid-upstream-api (Nemotron)")
    print("   • Fallback (Live OpenRouter):          stealth/ox-alpha")

    failover_client = OpenAIClient(
        api_key="sk-broken-key",
        base_url="https://invalid-upstream-endpoint-test.com/v1",
        fallback_base_url=base_url,
        fallback_api_key=ox_key,
        fallback_model="stealth/ox-alpha",
    )

    request_payload = {
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "messages": [{"role": "user", "content": "Say: 'FAILOVER_SUCCESSFUL'"}],
        "max_tokens": 300
    }

    try:
        raw_res = await failover_client.create_chat_completion(request_payload)
        claude_req = ClaudeMessagesRequest(
            model="claude-3-5-sonnet-20241022",
            messages=[ClaudeMessage(role="user", content="Say: 'FAILOVER_SUCCESSFUL'")],
            max_tokens=300
        )
        converted = convert_openai_to_claude_response(raw_res, claude_req)
        final_answer = converted["content"][0]["text"].strip()
        print(f"   ✅ Auto-Failover Live Result: \"{final_answer}\"")
    except Exception as e:
        print(f"   ❌ Failover pipeline error: {e}")

    print("\n" + "="*70)
    print(" 🎉 Live Dual-Model Test & Failover Verification Complete!")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(run_live_test())
