import time
import uuid
import asyncio
from datetime import datetime
from typing import Optional, Dict, Deque
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.config import config
from src.core.logging import logger
from src.core.client import OpenAIClient
from src.models.claude import ClaudeMessagesRequest, ClaudeTokenCountRequest
from src.conversion.request_converter import convert_claude_to_openai
from src.conversion.response_converter import (
    convert_openai_to_claude_response,
    convert_openai_streaming_to_claude_with_cancellation,
)
from src.core.model_manager import model_manager

router = APIRouter()

# Get custom headers from config
custom_headers = config.get_custom_headers()

openai_client = OpenAIClient(
    config.openai_api_key,
    config.openai_base_url,
    config.request_timeout,
    api_version=config.azure_api_version,
    custom_headers=custom_headers,
    fallback_base_url=config.fallback_base_url,
    fallback_api_key=config.fallback_api_key,
    fallback_model=config.fallback_model,
)

# Sliding Window Rate Limiter
class SlidingWindowRateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self.requests: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def is_allowed(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.time()
        async with self._lock:
            queue = self.requests[key]
            while queue and queue[0] <= now - 60.0:
                queue.popleft()
            if len(queue) >= self.limit:
                return False
            queue.append(now)
            return True

rate_limiter = SlidingWindowRateLimiter(config.rate_limit_per_minute)
concurrency_semaphore = asyncio.Semaphore(config.max_concurrent_requests)


async def validate_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Validate the client's API key and enforce rate limits."""
    client_ip = request.client.host if request.client else "unknown"
    client_api_key = None

    # Extract API key from headers
    if x_api_key:
        client_api_key = x_api_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        client_api_key = authorization[7:].strip()

    # Validate client API key if configured
    if config.anthropic_api_key:
        if not client_api_key or not config.validate_client_api_key(client_api_key):
            logger.warning(f"Unauthorized access attempt from {client_ip}")
            raise HTTPException(
                status_code=401,
                detail="Invalid API key. Please provide a valid Anthropic API key.",
            )
    else:
        # If ANTHROPIC_API_KEY is not set, fail closed unless anonymous access is explicitly permitted
        if not config.allow_anonymous_access:
            logger.error(
                f"Rejected unauthenticated request from {client_ip}: ANTHROPIC_API_KEY is not set and anonymous access is disabled."
            )
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Gateway API key is not configured.",
            )

    # Rate limiting check per client
    rate_key = client_api_key or client_ip
    if not await rate_limiter.is_allowed(rate_key):
        logger.warning("Rate limit exceeded for client IP: %s", client_ip)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please slow down your requests.",
        )


@router.post("/v1/messages")
async def create_message(
    request: ClaudeMessagesRequest,
    http_request: Request,
    _: None = Depends(validate_api_key),
):
    async with concurrency_semaphore:
        request_id = str(uuid.uuid4())
        try:
            logger.debug(
                "Processing Claude request: model=%s, stream=%s",
                request.model,
                request.stream,
            )

            # Convert Claude request to OpenAI format
            openai_request = convert_claude_to_openai(request, model_manager)

            # Check if client disconnected before processing
            if await http_request.is_disconnected():
                raise HTTPException(status_code=499, detail="Client disconnected")

            if request.stream:
                # Streaming response
                try:
                    openai_stream = openai_client.create_chat_completion_stream(
                        openai_request, request_id
                    )
                    return StreamingResponse(
                        convert_openai_streaming_to_claude_with_cancellation(
                            openai_stream,
                            request,
                            logger,
                            http_request,
                            openai_client,
                            request_id,
                        ),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "Keep-Alive": "timeout=600, max=1000",
                        },
                    )
                except HTTPException as e:
                    if e.status_code == 499:
                        logger.info(f"Streaming request {request_id} cancelled by client")
                        return JSONResponse(
                            status_code=499,
                            content={
                                "type": "error",
                                "error": {
                                    "type": "cancelled",
                                    "message": "Request cancelled by client",
                                },
                            },
                        )
                    logger.error(f"Streaming error: {e.detail}")
                    error_message = openai_client.classify_openai_error(e.detail)
                    error_response = {
                        "type": "error",
                        "error": {"type": "api_error", "message": error_message},
                    }
                    return JSONResponse(status_code=e.status_code, content=error_response)
            else:
                # Non-streaming response
                openai_response = await openai_client.create_chat_completion(
                    openai_request, request_id
                )
                claude_response = convert_openai_to_claude_response(
                    openai_response, request
                )
                return claude_response
        except HTTPException as e:
            if e.status_code == 499:
                logger.info(f"Request {request_id} was cancelled by client")
                return JSONResponse(
                    status_code=499,
                    content={
                        "type": "error",
                        "error": {
                            "type": "cancelled",
                            "message": "Request cancelled by client",
                        },
                    },
                )
            raise
        except asyncio.CancelledError:
            logger.info(f"Request {request_id} cancelled due to client disconnect")
            return JSONResponse(
                status_code=499,
                content={
                    "type": "error",
                    "error": {
                        "type": "cancelled",
                        "message": "Request cancelled by client",
                    },
                },
            )
        except Exception as e:
            logger.error(f"Unexpected error processing request: {e}")
            error_message = openai_client.classify_openai_error(str(e))
            raise HTTPException(status_code=500, detail=error_message)


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: ClaudeTokenCountRequest,
    _: None = Depends(validate_api_key),
):
    try:
        total_chars = 0

        # Count system message characters
        if request.system:
            if isinstance(request.system, str):
                total_chars += len(request.system)
            elif isinstance(request.system, list):
                for block in request.system:
                    if hasattr(block, "text") and block.text:
                        total_chars += len(block.text)
                    elif isinstance(block, dict) and "text" in block:
                        total_chars += len(str(block["text"]))

        # Count message characters
        for msg in request.messages:
            if msg.content is None:
                continue
            elif isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if hasattr(block, "text") and block.text is not None:
                        total_chars += len(str(block.text))
                    elif isinstance(block, dict) and "text" in block:
                        total_chars += len(str(block["text"]))

        # Rough estimation: 4 characters per token
        estimated_tokens = max(1, total_chars // 4)

        return {"input_tokens": estimated_tokens}

    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        raise HTTPException(status_code=500, detail="Failed to calculate token count")


@router.get("/health")
async def health_check():
    """Sanitized health check endpoint."""
    return {
        "status": "healthy",
        "service": "claudegate",
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/test-connection")
async def test_connection(_: None = Depends(validate_api_key)):
    """Authenticated API connectivity probe to upstream provider."""
    try:
        test_response = await openai_client.create_chat_completion(
            {
                "model": config.small_model,
                "messages": [{"role": "user", "content": "Ping"}],
                "max_tokens": 5,
            }
        )

        return {
            "status": "success",
            "message": "Successfully connected to upstream API",
            "timestamp": datetime.now().isoformat(),
            "response_id": test_response.get("id", "unknown"),
        }

    except Exception as e:
        logger.error(f"API connectivity test failed: {e}")
        error_message = openai_client.classify_openai_error(str(e))
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "error_type": "UpstreamConnectionError",
                "message": error_message,
                "timestamp": datetime.now().isoformat(),
            },
        )


@router.api_route("/api/hello", methods=["GET", "HEAD"])
@router.api_route("/hello", methods=["GET", "HEAD"])
async def hello():
    """Lightweight health probe endpoint for Claude Code CLI."""
    return {"status": "ok", "message": "ClaudeGate is online"}


@router.get("/")
async def root():
    """Sanitized root gateway info."""
    return {
        "service": "ClaudeGate",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "messages": "/v1/messages",
            "count_tokens": "/v1/messages/count_tokens",
            "health": "/health",
            "hello": "/api/hello",
            "test_connection": "/test-connection",
        },
    }
