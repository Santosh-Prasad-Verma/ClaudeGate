import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from src.core.client import OpenAIClient
from src.main import app


def test_non_streaming_explicit_cancellation():
    """Verify that cancel_request properly cancels an active non-streaming request with 499."""
    async def _run():
        client = OpenAIClient(
            api_key="sk-test-key",
            base_url="https://api.example.com/v1"
        )

        async def slow_mock_create(**kwargs):
            await asyncio.sleep(5)
            return MagicMock()

        client.client.chat.completions.create = slow_mock_create

        request_id = "req-cancel-123"
        task = asyncio.create_task(
            client.create_chat_completion(
                {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                request_id=request_id
            )
        )

        # Allow task to start and register cancel_event
        await asyncio.sleep(0.05)
        assert request_id in client.active_requests

        # Cancel the request
        cancelled = client.cancel_request(request_id)
        assert cancelled is True

        with pytest.raises(HTTPException) as exc_info:
            await task

        assert exc_info.value.status_code == 499
        assert "Request cancelled by client" in exc_info.value.detail
        assert request_id not in client.active_requests

    asyncio.run(_run())


def test_non_streaming_external_task_cancellation():
    """Verify that external task cancellation (e.g. client disconnect) cleans up tasks properly."""
    async def _run():
        client = OpenAIClient(
            api_key="sk-test-key",
            base_url="https://api.example.com/v1"
        )

        async def slow_mock_create(**kwargs):
            await asyncio.sleep(10)
            return MagicMock()

        client.client.chat.completions.create = slow_mock_create

        request_id = "req-disconnect-123"
        task = asyncio.create_task(
            client.create_chat_completion(
                {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                request_id=request_id
            )
        )

        await asyncio.sleep(0.05)
        assert request_id in client.active_requests

        # Cancel the outer task (simulating ASGI client disconnect cancellation)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Ensure active_requests cleaned up
        assert request_id not in client.active_requests

    asyncio.run(_run())


def test_streaming_cancellation():
    """Verify that streaming generators handle cancellation cleanly."""
    async def _run():
        client = OpenAIClient(
            api_key="sk-test-key",
            base_url="https://api.example.com/v1"
        )

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

        class MockStream:
            def __init__(self):
                self._chunks = [MockChunk("chunk1"), MockChunk("chunk2")]
                self._idx = 0
                self.close = AsyncMock()

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._chunks):
                    raise StopAsyncIteration
                if self._idx == 1:
                    await asyncio.sleep(5)
                chunk = self._chunks[self._idx]
                self._idx += 1
                return chunk

        mock_stream_obj = MockStream()
        client.client.chat.completions.create = AsyncMock(return_value=mock_stream_obj)

        request_id = "stream-cancel-123"
        generator = client.create_chat_completion_stream(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
            request_id=request_id
        )

        # Get first chunk
        first = await generator.__anext__()
        assert "chunk1" in first

        # Cancel request
        client.cancel_request(request_id)

        # Next chunk should return error marker with 499
        second = await generator.__anext__()
        assert "ERROR::499" in second

        # Next iteration exhausts generator and triggers cleanup
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()

        assert request_id not in client.active_requests

    asyncio.run(_run())


def test_endpoint_messages_cancellation_response():
    """Verify that cancelled requests to /v1/messages return clean 499 status response."""
    async def _run():
        import src.api.endpoints as endpoints

        # Mock openai_client to raise HTTPException(499)
        mock_chat_completion = AsyncMock(
            side_effect=HTTPException(status_code=499, detail="Request cancelled by client")
        )
        with patch.object(endpoints.openai_client, "create_chat_completion", mock_chat_completion):
            test_client = TestClient(app)
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 100,
                "stream": False
            }
            from src.core.config import config
            auth_header = config.anthropic_api_key if config.anthropic_api_key else "sk-claudegate-local"
            response = test_client.post(
                "/v1/messages",
                json=payload,
                headers={"x-api-key": auth_header}
            )
            assert response.status_code == 499
            data = response.json()
            assert data["type"] == "error"
            assert data["error"]["type"] == "cancelled"

    asyncio.run(_run())
