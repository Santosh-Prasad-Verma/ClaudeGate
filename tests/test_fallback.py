import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.client import OpenAIClient
from openai._exceptions import APIError


def test_non_streaming_fallback_when_primary_fails():
    """Verify that non-streaming requests automatically fail over to fallback client when primary fails."""
    async def _run():
        # 1. Initialize client with both primary and fallback configurations
        client = OpenAIClient(
            api_key="sk-primary-key",
            base_url="https://primary.example.com/v1",
            fallback_base_url="https://fallback.example.com/v1",
            fallback_api_key="sk-fallback-key",
            fallback_model="fallback-backup-model",
        )
        
        # 2. Mock primary client to raise an upstream error (e.g. 500 Overload)
        mock_primary_create = AsyncMock(
            side_effect=Exception("Upstream error from primary: Service temporarily overloaded")
        )
        client.client.chat.completions.create = mock_primary_create
        
        # 3. Mock fallback client to return a valid response
        expected_response = MagicMock()
        expected_response.model_dump.return_value = {
            "id": "chatcmpl-fallback-123",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello from Fallback AI!"},
                    "finish_reason": "stop"
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        mock_fallback_create = AsyncMock(return_value=expected_response)
        client.fallback_client.chat.completions.create = mock_fallback_create
        
        # 4. Execute request
        request_payload = {
            "model": "primary-model",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        
        # We patch asyncio.sleep to 0 for instant test execution
        with patch("asyncio.sleep", return_value=None):
            result = await client.create_chat_completion(request_payload)
        
        # 5. Assertions
        assert mock_primary_create.called, "Primary client should have been attempted first"
        assert mock_fallback_create.called, "Fallback client should have been invoked when primary failed"
        
        # Check that fallback received the configured fallback_model
        fallback_call_args = mock_fallback_create.call_args[1]
        assert fallback_call_args["model"] == "fallback-backup-model"
        
        # Verify we received the fallback answer
        assert result["choices"][0]["message"]["content"] == "Hello from Fallback AI!"

    asyncio.run(_run())


def test_streaming_fallback_when_primary_fails():
    """Verify that streaming requests automatically fail over to fallback client when primary fails."""
    async def _run():
        # 1. Initialize client with fallback settings
        client = OpenAIClient(
            api_key="sk-primary-key",
            base_url="https://primary.example.com/v1",
            fallback_base_url="https://fallback.example.com/v1",
            fallback_api_key="sk-fallback-key",
            fallback_model="fallback-backup-model",
        )
        
        # 2. Mock primary stream creation to fail with 503 Service Unavailable
        mock_primary_create = AsyncMock(
            side_effect=Exception("Upstream 503: Service Unavailable")
        )
        client.client.chat.completions.create = mock_primary_create
        
        # 3. Create mock streaming chunks for fallback
        class MockChunk:
            def __init__(self, content):
                self.content = content
            def model_dump(self):
                return {
                    "choices": [{
                        "index": 0,
                        "delta": {"content": self.content},
                        "finish_reason": None
                    }]
                }
        
        async def mock_fallback_stream_generator():
            yield MockChunk("Streaming ")
            yield MockChunk("response ")
            yield MockChunk("from fallback!")
        
        mock_fallback_create = AsyncMock(return_value=mock_fallback_stream_generator())
        client.fallback_client.chat.completions.create = mock_fallback_create
        
        # 4. Consume stream generator
        request_payload = {
            "model": "primary-model",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        
        collected_chunks = []
        with patch("asyncio.sleep", return_value=None):
            async for chunk in client.create_chat_completion_stream(request_payload):
                collected_chunks.append(chunk)
        
        # 5. Assertions
        assert mock_primary_create.called, "Primary should have been attempted"
        assert mock_fallback_create.called, "Fallback client should have been invoked"
        assert any("Streaming" in c for c in collected_chunks)
        assert any("from fallback!" in c for c in collected_chunks)
        assert collected_chunks[-1] == "data: [DONE]"

    asyncio.run(_run())


def test_endpoint_messages_fallback():
    """Verify that sending a request to /v1/messages gracefully fails over to fallback provider."""
    async def _run():
        from fastapi.testclient import TestClient
        from src.main import app
        import src.api.endpoints as endpoints
        
        # 1. Setup mock fallback client on the active endpoints.openai_client
        mock_primary = AsyncMock(side_effect=Exception("500: Primary model is overloaded"))
        endpoints.openai_client.client.chat.completions.create = mock_primary
        
        expected_response = MagicMock()
        expected_response.model_dump.return_value = {
            "id": "chatcmpl-fb-999",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from fallback endpoint!"},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8}
        }
        mock_fallback = AsyncMock(return_value=expected_response)
        
        # Create fallback client if not initialized
        if not endpoints.openai_client.fallback_client:
            endpoints.openai_client.fallback_client = MagicMock()
        endpoints.openai_client.fallback_client.chat.completions.create = mock_fallback
        endpoints.openai_client.fallback_model = "deepseek-v4-pro"
        
        test_client = TestClient(app)
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Testing failover"}],
            "max_tokens": 500,
            "stream": False
        }
        
        with patch("asyncio.sleep", return_value=None):
            from src.core.config import config
            auth_header = config.anthropic_api_key if config.anthropic_api_key else "sk-claudegate-local"
            response = test_client.post(
                "/v1/messages",
                json=payload,
                headers={"x-api-key": auth_header}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "assistant"
        assert data["content"][0]["text"] == "Hello from fallback endpoint!"

    asyncio.run(_run())
