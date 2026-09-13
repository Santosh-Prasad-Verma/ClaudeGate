import time
import uuid
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, Deque, Any
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, Response

from src.core.config import get_config

config = get_config()
from src.core.logging import logger
from src.core.client import OpenAIClient
from src.core.flight_recorder import flight_recorder
from src.models.claude import ClaudeMessagesRequest, ClaudeTokenCountRequest
from src.conversion.request_converter import (
    convert_claude_to_openai,
    reconcile_tool_calls_and_responses,
)
from src.conversion.schema_sanitizer import sanitize_tool_parameters
from src.conversion.response_converter import (
    convert_openai_to_claude_response,
    convert_openai_streaming_to_claude_with_cancellation,
)
from src.core.model_manager import model_manager

router = APIRouter()

# Get custom headers from config
custom_headers = config.get_custom_headers()

# Build per-tier client configuration
tier_endpoints = {
    config.big_model: {
        "base_url": config.big_model_base_url,
        "api_key": config.big_model_api_key,
    },
    config.middle_model: {
        "base_url": config.middle_model_base_url,
        "api_key": config.middle_model_api_key,
    },
    config.small_model: {
        "base_url": config.small_model_base_url,
        "api_key": config.small_model_api_key,
    },
}

openai_client = OpenAIClient(
    config.openai_api_key or "",
    config.openai_base_url,
    config.request_timeout,
    api_version=config.azure_api_version,
    custom_headers=custom_headers,
    fallback_base_url=config.fallback_base_url,
    fallback_api_key=config.fallback_api_key,
    fallback_model=config.fallback_model,
    max_retries=config.max_retries,
    circuit_breaker_enabled=config.circuit_breaker_enabled,
    circuit_breaker_threshold=config.circuit_breaker_threshold,
    circuit_breaker_reset_timeout=config.circuit_breaker_reset_timeout,
    tier_endpoints=tier_endpoints,
    api_keys=config.openai_api_keys,
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
            if not queue:
                self.requests.pop(key, None)
                queue = self.requests[key]
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
        start_time = time.time()
        mapped_model = "unknown"
        try:
            sanitized_model = str(request.model).replace("\r", "").replace("\n", "")
            sanitized_stream = str(request.stream).replace("\r", "").replace("\n", "")
            logger.debug(
                "Processing Claude request: model=%s, stream=%s",
                sanitized_model,
                sanitized_stream,
            )

            # Convert Claude request to OpenAI format
            openai_request = convert_claude_to_openai(request, model_manager)
            mapped_model = openai_request.get("model", request.model)

            # Check if client disconnected before processing
            if await http_request.is_disconnected():
                raise HTTPException(status_code=499, detail="Client disconnected")

            if request.stream:
                try:
                    openai_stream = openai_client.create_chat_completion_stream(
                        openai_request, request_id
                    )

                    # Wrap generator to record flight recorder event upon completion
                    async def _monitored_stream():
                        tools_used = []
                        tokens_in = 0
                        tokens_out = 0
                        cached_toks = 0
                        first_token_time = None
                        status = 200
                        err_msg = None

                        try:
                            async for chunk in convert_openai_streaming_to_claude_with_cancellation(
                                openai_stream,
                                request,
                                logger,
                                http_request,
                                openai_client,
                                request_id,
                            ):
                                if first_token_time is None:
                                    first_token_time = (time.time() - start_time) * 1000

                                # Parse metadata for flight recorder
                                if "content_block_start" in chunk and '"type": "tool_use"' in chunk:
                                    try:
                                        data_line = [l for l in chunk.split("\n") if l.startswith("data: ")][0][6:]
                                        block_data = json.loads(data_line).get("content_block", {})
                                        name = block_data.get("name")
                                        if name and name not in tools_used:
                                            tools_used.append(name)
                                    except Exception:
                                        pass

                                if "message_start" in chunk and "usage" in chunk:
                                    try:
                                        data_line = [l for l in chunk.split("\n") if l.startswith("data: ")][0][6:]
                                        usg = json.loads(data_line).get("message", {}).get("usage", {})
                                        if usg.get("input_tokens"):
                                            tokens_in = usg["input_tokens"]
                                        if usg.get("cache_read_input_tokens"):
                                            cached_toks = usg["cache_read_input_tokens"]
                                    except Exception:
                                        pass

                                if "message_delta" in chunk and "usage" in chunk:
                                    try:
                                        data_line = [l for l in chunk.split("\n") if l.startswith("data: ")][0][6:]
                                        usg = json.loads(data_line).get("usage", {})
                                        if usg.get("input_tokens"):
                                            tokens_in = usg["input_tokens"]
                                        if usg.get("output_tokens"):
                                            tokens_out = usg["output_tokens"]
                                        if usg.get("cache_read_input_tokens"):
                                            cached_toks = usg["cache_read_input_tokens"]
                                    except Exception:
                                        pass

                                if "text_delta" in chunk:
                                    # Track text output length for estimation if upstream emits 0 tokens
                                    try:
                                        data_line = [l for l in chunk.split("\n") if l.startswith("data: ")][0][6:]
                                        t_delta = json.loads(data_line).get("delta", {}).get("text", "")
                                        if t_delta:
                                            tokens_out += max(1, len(t_delta) // 4)
                                    except Exception:
                                        pass

                                if '"type": "error"' in chunk:
                                    status = 500
                                    err_msg = "Stream error"

                                yield chunk
                        finally:
                            duration = (time.time() - start_time) * 1000
                            # If upstream failed to provide token counts, calculate intelligent heuristic
                            if tokens_in == 0:
                                prompt_chars = sum(len(str(getattr(m, "content", ""))) for m in (request.messages or []))
                                if request.system:
                                    prompt_chars += len(str(request.system))
                                tokens_in = max(1, prompt_chars // 4)
                            if tokens_out == 0:
                                tokens_out = 1

                            flight_recorder.record_completion(
                                request_id=request_id,
                                claude_model=request.model,
                                target_model=mapped_model,
                                stream=True,
                                duration_ms=duration,
                                status_code=status,
                                ttft_ms=first_token_time,
                                input_tokens=tokens_in,
                                output_tokens=tokens_out,
                                cached_tokens=cached_toks,
                                tools_called=tools_used,
                                error=err_msg,
                            )

                    return StreamingResponse(
                        _monitored_stream(),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "Keep-Alive": "timeout=600, max=1000",
                        },
                    )
                except HTTPException as e:
                    if e.status_code == 499:
                        logger.info("Streaming request %s cancelled by client", request_id)
                        flight_recorder.record_completion(
                            request_id=request_id,
                            claude_model=request.model,
                            target_model=mapped_model,
                            stream=True,
                            duration_ms=(time.time() - start_time) * 1000,
                            status_code=499,
                            error="Cancelled by client",
                        )
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
                    flight_recorder.record_completion(
                        request_id=request_id,
                        claude_model=request.model,
                        target_model=mapped_model,
                        stream=True,
                        duration_ms=(time.time() - start_time) * 1000,
                        status_code=e.status_code,
                        error=error_message,
                    )
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

                # Record metrics
                duration = (time.time() - start_time) * 1000
                usg = claude_response.get("usage", {})
                tools_used = [
                    block.get("name")
                    for block in claude_response.get("content", [])
                    if block.get("type") == "tool_use" and block.get("name")
                ]
                flight_recorder.record_completion(
                    request_id=request_id,
                    claude_model=request.model,
                    target_model=mapped_model,
                    stream=False,
                    duration_ms=duration,
                    status_code=200,
                    input_tokens=usg.get("input_tokens", 0),
                    output_tokens=usg.get("output_tokens", 0),
                    cached_tokens=usg.get("cache_read_input_tokens", 0),
                    tools_called=tools_used,
                )
                return claude_response
        except HTTPException as e:
            if e.status_code == 499:
                logger.info("Request %s was cancelled by client", request_id)
                flight_recorder.record_completion(
                    request_id=request_id,
                    claude_model=request.model,
                    target_model=mapped_model,
                    stream=request.stream or False,
                    duration_ms=(time.time() - start_time) * 1000,
                    status_code=499,
                    error="Cancelled by client",
                )
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
            flight_recorder.record_completion(
                request_id=request_id,
                claude_model=request.model,
                target_model=mapped_model,
                stream=request.stream or False,
                duration_ms=(time.time() - start_time) * 1000,
                status_code=e.status_code,
                error=str(e.detail),
            )
            raise
        except asyncio.CancelledError:
            logger.info("Request %s cancelled due to client disconnect", request_id)
            flight_recorder.record_completion(
                request_id=request_id,
                claude_model=request.model,
                target_model=mapped_model,
                stream=request.stream or False,
                duration_ms=(time.time() - start_time) * 1000,
                status_code=499,
                error="Cancelled",
            )
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
            logger.error("Unexpected error processing request: %s", type(e).__name__)
            error_message = openai_client.classify_openai_error(type(e).__name__)
            flight_recorder.record_completion(
                request_id=request_id,
                claude_model=request.model,
                target_model=mapped_model,
                stream=request.stream or False,
                duration_ms=(time.time() - start_time) * 1000,
                status_code=500,
                error=error_message,
            )
            raise HTTPException(status_code=500, detail=error_message)


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: ClaudeTokenCountRequest,
    _: None = Depends(validate_api_key),
):
    """Tool-aware token estimation endpoint."""
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

        # Count tool schemas (JSON) so Claude Code context calculation is accurate
        if request.tools:
            for tool in request.tools:
                if hasattr(tool, "name") and tool.name:
                    total_chars += len(tool.name)
                if hasattr(tool, "description") and tool.description:
                    total_chars += len(tool.description)
                schema = getattr(tool, "input_schema", None)
                if schema:
                    try:
                        total_chars += len(json.dumps(schema, ensure_ascii=False))
                    except Exception:
                        pass

        estimated_tokens = max(1, total_chars // 4)
        return {"input_tokens": estimated_tokens}

    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        raise HTTPException(status_code=500, detail="Failed to calculate token count")


_cached_models: list = []
_cached_models_expiry: float = 0.0


@router.get("/v1/models")
@router.get("/models")
@router.head("/v1/models")
@router.head("/models")
async def list_models(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Return dynamic model catalog merging upstream provider models, configured tiers, and Claude compatibility aliases."""
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await validate_api_key(request, x_api_key=x_api_key, authorization=authorization)
    if request.method == "HEAD":
        return Response(status_code=200)
    global _cached_models, _cached_models_expiry
    now = time.time()

    # Query upstream provider's live models catalog with a 60s cache
    upstream_ids = []
    if now < _cached_models_expiry and _cached_models:
        upstream_ids = list(_cached_models)
    else:
        try:
            upstream_list = await asyncio.wait_for(openai_client.client.models.list(), timeout=3.0)
            upstream_ids = [m.id for m in getattr(upstream_list, "data", []) if hasattr(m, "id")]
            _cached_models = list(upstream_ids)
            _cached_models_expiry = now + 60.0
        except Exception as exc:
            logger.debug("Could not fetch upstream models list: %s", exc)

    # 1. Configured models in .env
    model_ids = []
    for mid in (config.big_model, config.middle_model, config.small_model, config.fallback_model):
        if mid and mid not in model_ids:
            model_ids.append(mid)

    # 2. Live upstream models returned by the provider (e.g. DeepSeek, OpenAI, Groq, Ollama)
    for mid in upstream_ids:
        if mid and mid not in model_ids:
            model_ids.append(mid)

    # 3. Standard Claude compatibility aliases (required by Claude Code CLI / Cline / Cursor pickers)
    claude_aliases = [
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]
    for mid in claude_aliases:
        if mid not in model_ids:
            model_ids.append(mid)

    data = [
        {
            "id": mid,
            "object": "model",
            "created": 1700000000,
            "owned_by": "anthropic" if mid.startswith("claude-") else "upstream",
            "permission": [],
            "root": mid,
            "parent": None,
        }
        for mid in model_ids
    ]
    return {"object": "list", "data": data}


@router.post("/v1/chat/completions")
@router.post("/chat/completions")
async def create_chat_completion(
    request: Request,
    _: None = Depends(validate_api_key),
):
    """OpenAI-compatible chat completions endpoint for OpenCode, Codex CLI, and CommandCode."""
    body = await request.json()
    model = body.get("model", config.big_model)
    stream = body.get("stream", False)
    request_id = str(uuid.uuid4())
    start_time = time.time()

    if stream:
        async def _stream_openai():
            first_token_time = None
            try:
                async for chunk_line in openai_client.create_chat_completion_stream(body, request_id):
                    if first_token_time is None and chunk_line.startswith("data: {"):
                        first_token_time = (time.time() - start_time) * 1000
                    yield f"{chunk_line}\n\n" if not chunk_line.endswith("\n\n") else chunk_line
            finally:
                duration = (time.time() - start_time) * 1000
                flight_recorder.record_completion(
                    request_id=request_id,
                    claude_model=model,
                    target_model=model,
                    stream=True,
                    duration_ms=duration,
                    status_code=200,
                    ttft_ms=first_token_time,
                )

        return StreamingResponse(
            _stream_openai(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    else:
        res = await openai_client.create_chat_completion(body, request_id)
        duration = (time.time() - start_time) * 1000
        usg = res.get("usage", {})
        flight_recorder.record_completion(
            request_id=request_id,
            claude_model=model,
            target_model=model,
            stream=False,
            duration_ms=duration,
            status_code=200,
            input_tokens=usg.get("prompt_tokens", 0) if isinstance(usg, dict) else 0,
            output_tokens=usg.get("completion_tokens", 0) if isinstance(usg, dict) else 0,
        )
        return JSONResponse(content=res)


@router.api_route("/v1/responses", methods=["POST", "HEAD"])
@router.api_route("/responses", methods=["POST", "HEAD"])
async def create_response(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """OpenAI Responses API endpoint for Codex CLI and compatible agents."""
    if request.method == "HEAD":
        return Response(status_code=200)
    await validate_api_key(request, x_api_key=x_api_key, authorization=authorization)
    body = await request.json()
    import json
    try:
        with open("/tmp/codex_req.json", "w") as f:
            json.dump(body, f, indent=2)
    except Exception:
        pass
    
    # Translate Responses API body to Chat Completions request
    messages = []
    
    # 1. System instructions
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})
        
    # 2. Input items
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "message")
            if item_type == "message":
                role = item.get("role", "user")
                if role == "developer":
                    role = "system"
                content = item.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") in ("input_text", "text"):
                                text_parts.append(part.get("text", ""))
                            elif "text" in part:
                                text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    messages.append({"role": role, "content": "".join(text_parts)})
                elif isinstance(content, str):
                    messages.append({"role": role, "content": content})
            elif item_type == "function_call":
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id") or item.get("id", "call_1"),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", "{}"),
                        }
                    }]
                })
            elif item_type == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or item.get("id", ""),
                    "content": item.get("output", "")
                })

    # 3. Tools conversion
    raw_tools = body.get("tools", [])
    converted_tools = []
    for t in raw_tools:
        if not isinstance(t, dict):
            continue
        if "function" in t and isinstance(t["function"], dict):
            fn = dict(t["function"])
            fn["parameters"] = sanitize_tool_parameters(fn.get("parameters", {}))
            converted_tools.append({"type": "function", "function": fn})
        elif t.get("type") == "function":
            converted_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": sanitize_tool_parameters(t.get("parameters", {})),
                }
            })

    # Ensure tool calls and tool responses are validly paired for OpenAI backend
    messages = reconcile_tool_calls_and_responses(messages)

    model = body.get("model", config.big_model)
    chat_req = {
        "model": model,
        "messages": messages,
    }
    if converted_tools:
        chat_req["tools"] = converted_tools
    if "temperature" in body:
        chat_req["temperature"] = body["temperature"]
    if "max_output_tokens" in body:
        chat_req["max_tokens"] = body["max_output_tokens"]
    elif "max_tokens" in body:
        chat_req["max_tokens"] = body["max_tokens"]

    stream = body.get("stream", False)
    request_id = str(uuid.uuid4())
    resp_id = f"resp_{request_id[:16]}"
    item_id = f"item_{str(uuid.uuid4())[:16]}"
    start_time = time.time()

    if stream:
        async def _stream_responses():
            yield f"event: response.created\ndata: {json.dumps({'type': 'response.created', 'response': {'id': resp_id, 'status': 'in_progress', 'model': model, 'output': []}})}\n\n"

            accumulated_text = []
            accumulated_tools: Dict[int, Dict[str, Any]] = {}
            current_message_added = False

            try:
                chat_req["stream"] = True
                async for chunk_line in openai_client.create_chat_completion_stream(chat_req, request_id):
                    if chunk_line.startswith("ERROR::"):
                        logger.error("Upstream error in responses stream: %s", chunk_line)
                        break
                    if not chunk_line.startswith("data: "):
                        continue
                    data_str = chunk_line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_obj = json.loads(data_str)
                        choices = chunk_obj.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content_delta = delta.get("content")
                            if content_delta:
                                if not current_message_added:
                                    yield f"event: response.output_item.added\ndata: {json.dumps({'type': 'response.output_item.added', 'output_index': 0, 'item': {'id': item_id, 'type': 'message', 'role': 'assistant', 'status': 'in_progress', 'content': []}})}\n\n"
                                    yield f"event: response.content_part.added\ndata: {json.dumps({'type': 'response.content_part.added', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"
                                    current_message_added = True
                                accumulated_text.append(content_delta)
                                yield f"event: response.output_text.delta\ndata: {json.dumps({'type': 'response.output_text.delta', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'delta': content_delta})}\n\n"

                            tool_deltas = delta.get("tool_calls")
                            if tool_deltas:
                                for td in tool_deltas:
                                    idx = td.get("index", 0)
                                    if idx not in accumulated_tools:
                                        tc_id = td.get("id") or f"call_{str(uuid.uuid4())[:8]}"
                                        fn_name = td.get("function", {}).get("name", "")
                                        accumulated_tools[idx] = {
                                            "id": tc_id,
                                            "name": fn_name,
                                            "arguments": "",
                                            "emitted_item": False,
                                        }
                                    if td.get("function", {}).get("name"):
                                        accumulated_tools[idx]["name"] = td["function"]["name"]
                                    arg_chunk = td.get("function", {}).get("arguments", "")
                                    if arg_chunk:
                                        accumulated_tools[idx]["arguments"] += arg_chunk
                                        out_idx = idx + (1 if current_message_added else 0)
                                        if not accumulated_tools[idx]["emitted_item"]:
                                            yield f"event: response.output_item.added\ndata: {json.dumps({'type': 'response.output_item.added', 'output_index': out_idx, 'item': {'id': accumulated_tools[idx]['id'], 'type': 'function_call', 'call_id': accumulated_tools[idx]['id'], 'name': accumulated_tools[idx]['name'], 'arguments': '', 'status': 'in_progress'}})}\n\n"
                                            accumulated_tools[idx]["emitted_item"] = True
                                        yield f"event: response.function_call_arguments.delta\ndata: {json.dumps({'type': 'response.function_call_arguments.delta', 'item_id': accumulated_tools[idx]['id'], 'output_index': out_idx, 'call_id': accumulated_tools[idx]['id'], 'delta': arg_chunk})}\n\n"
                    except Exception:
                        pass
            finally:
                output_items = []
                out_idx = 0
                if current_message_added:
                    full_text = "".join(accumulated_text)
                    yield f"event: response.output_text.done\ndata: {json.dumps({'type': 'response.output_text.done', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'text': full_text})}\n\n"
                    yield f"event: response.content_part.done\ndata: {json.dumps({'type': 'response.content_part.done', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'part': {'type': 'output_text', 'text': full_text}})}\n\n"
                    yield f"event: response.output_item.done\ndata: {json.dumps({'type': 'response.output_item.done', 'output_index': 0, 'item': {'id': item_id, 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': full_text}]}})}\n\n"
                    output_items.append({
                        "id": item_id,
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": full_text}],
                    })
                    out_idx += 1

                for idx in sorted(accumulated_tools.keys()):
                    t_info = accumulated_tools[idx]
                    tc_id = t_info["id"]
                    fn_name = t_info["name"]
                    args = t_info["arguments"]
                    if not t_info["emitted_item"]:
                        yield f"event: response.output_item.added\ndata: {json.dumps({'type': 'response.output_item.added', 'output_index': out_idx, 'item': {'id': tc_id, 'type': 'function_call', 'call_id': tc_id, 'name': fn_name, 'arguments': '', 'status': 'in_progress'}})}\n\n"
                    yield f"event: response.function_call_arguments.done\ndata: {json.dumps({'type': 'response.function_call_arguments.done', 'item_id': tc_id, 'output_index': out_idx, 'call_id': tc_id, 'arguments': args})}\n\n"
                    yield f"event: response.output_item.done\ndata: {json.dumps({'type': 'response.output_item.done', 'output_index': out_idx, 'item': {'id': tc_id, 'type': 'function_call', 'call_id': tc_id, 'name': fn_name, 'arguments': args, 'status': 'completed'}})}\n\n"
                    output_items.append({
                        "id": tc_id,
                        "type": "function_call",
                        "call_id": tc_id,
                        "name": fn_name,
                        "arguments": args,
                        "status": "completed",
                    })
                    out_idx += 1

                if not output_items:
                    yield f"event: response.output_item.added\ndata: {json.dumps({'type': 'response.output_item.added', 'output_index': 0, 'item': {'id': item_id, 'type': 'message', 'role': 'assistant', 'status': 'in_progress', 'content': []}})}\n\n"
                    yield f"event: response.content_part.added\ndata: {json.dumps({'type': 'response.content_part.added', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"
                    yield f"event: response.output_text.done\ndata: {json.dumps({'type': 'response.output_text.done', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'text': ''})}\n\n"
                    yield f"event: response.content_part.done\ndata: {json.dumps({'type': 'response.content_part.done', 'item_id': item_id, 'output_index': 0, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"
                    yield f"event: response.output_item.done\ndata: {json.dumps({'type': 'response.output_item.done', 'output_index': 0, 'item': {'id': item_id, 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': ''}]}})}\n\n"
                    output_items.append({
                        "id": item_id,
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": ""}],
                    })

                latency_ms = (time.time() - start_time) * 1000.0
                full_text = "".join(accumulated_text)
                in_toks = max(1, sum(len(str(m.get("content", "") or "")) for m in messages) // 4)
                out_toks = max(1, (len(full_text) + sum(len(t["arguments"]) for t in accumulated_tools.values())) // 4)
                flight_recorder.record_completion(
                    request_id=request_id,
                    claude_model=f"codex:{model}",
                    target_model=model,
                    stream=True,
                    duration_ms=latency_ms,
                    status_code=200,
                    input_tokens=in_toks,
                    output_tokens=out_toks,
                    tools_called=[t["name"] for t in accumulated_tools.values()] or None,
                )
                yield f"event: response.completed\ndata: {json.dumps({'type': 'response.completed', 'response': {'id': resp_id, 'status': 'completed', 'model': model, 'output': output_items, 'usage': {'input_tokens': in_toks, 'output_tokens': out_toks, 'total_tokens': in_toks + out_toks}}})}\n\n"

        return StreamingResponse(
            _stream_responses(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    else:
        chat_req["stream"] = False
        res = await openai_client.create_chat_completion(chat_req, request_id)
        choices = res.get("choices", [])
        text_content = ""
        tool_calls = []
        if choices:
            msg = choices[0].get("message", {})
            text_content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []

        output_items = []
        if text_content:
            output_items.append({
                "id": item_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "text", "text": text_content}]
            })
        for tc in tool_calls:
            output_items.append({
                "id": tc.get("id", f"call_{str(uuid.uuid4())[:8]}"),
                "type": "function_call",
                "call_id": tc.get("id"),
                "name": tc.get("function", {}).get("name"),
                "arguments": tc.get("function", {}).get("arguments", "{}"),
            })

        latency_ms = (time.time() - start_time) * 1000.0
        usg = res.get("usage", {})
        in_tokens = usg.get("prompt_tokens", 0) if isinstance(usg, dict) else 0
        out_tokens = usg.get("completion_tokens", 0) if isinstance(usg, dict) else 0
        if in_tokens == 0:
            in_tokens = max(1, sum(len(str(m.get("content", "") or "")) for m in messages) // 4)
        if out_tokens == 0:
            out_tokens = max(1, len(text_content) // 4)

        flight_recorder.record_completion(
            request_id=request_id,
            claude_model=f"codex:{model}",
            target_model=model,
            stream=False,
            duration_ms=latency_ms,
            status_code=200,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            tools_called=[tc.get("function", {}).get("name", "tool") for tc in tool_calls if isinstance(tc, dict)] or None,
        )

        responses_res = {
            "id": resp_id,
            "object": "response",
            "status": "completed",
            "model": model,
            "output": output_items,
            "usage": {
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "total_tokens": in_tokens + out_tokens,
            }
        }
        return JSONResponse(content=responses_res)



@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Favicon endpoint to prevent 404 console errors."""
    return Response(status_code=204)


@router.get("/api/flight-recorder")
async def get_flight_recorder(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """API endpoint providing recent request events and summary statistics."""
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await validate_api_key(request, x_api_key=x_api_key, authorization=authorization)

    return {
        "summary": flight_recorder.get_summary(),
        "recent_requests": flight_recorder.get_recent(50),
        "circuit_breaker": {
            "state": openai_client.circuit_breaker.state,
            "failure_count": openai_client.circuit_breaker.failure_count,
            "can_attempt_primary": openai_client.circuit_breaker.can_attempt_primary(),
        },
    }


@router.post("/api/flight-recorder/clear")
async def clear_flight_recorder_endpoint(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Clear recorded requests from memory."""
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await validate_api_key(request, x_api_key=x_api_key, authorization=authorization)
    flight_recorder.clear()
    return {"status": "ok", "message": "Flight recorder history cleared"}


@router.get("/api/circuit-breaker")
async def get_circuit_breaker_status(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Return circuit breaker health status."""
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await validate_api_key(request, x_api_key=x_api_key, authorization=authorization)
    return {
        "state": openai_client.circuit_breaker.state,
        "failure_count": openai_client.circuit_breaker.failure_count,
        "enabled": openai_client.circuit_breaker.enabled,
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Live web dashboard with real-time stats, provider health, and flight recorder."""
    summary = flight_recorder.get_summary()
    cb_state = openai_client.circuit_breaker.state
    cb_color = "#10b981" if cb_state == "CLOSED" else ("#ef4444" if cb_state == "OPEN" else "#f59e0b")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ClaudeGate Live Console</title>
  <style>
    :root {{
      --bg: #09090b;
      --card: #121215;
      --border: #27272a;
      --border-muted: #1f1f23;
      --text: #fafafa;
      --text-muted: #a1a1aa;
      --text-sub: #71717a;
      --primary: #38bdf8;
      --success: #22c55e;
      --error: #ef4444;
      --warn: #f59e0b;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 24px;
      min-height: 100vh;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{ max-width: 1360px; margin: 0 auto; }}

    /* Header Bar */
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 20px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 20px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .brand-icon {{
      width: 32px;
      height: 32px;
      background: #18181b;
      border: 1px solid var(--border);
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--primary);
    }}
    .brand-title {{
      font-size: 16px;
      font-weight: 600;
      color: var(--text);
      letter-spacing: -0.2px;
    }}
    .brand-sub {{
      font-size: 12px;
      color: var(--text-sub);
    }}
    .header-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .status-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      border: 1px solid var(--border);
      background: #18181b;
    }}
    .status-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
    }}
    .btn {{
      background: #18181b;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .btn:hover {{
      background: #27272a;
      border-color: #3f3f46;
    }}
    .btn-danger {{
      color: var(--error);
    }}
    .btn-danger:hover {{
      background: rgba(239, 68, 68, 0.1);
      border-color: var(--error);
    }}

    /* Stat Cards Grid */
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}
    .stat-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px 18px;
    }}
    .stat-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
    }}
    .stat-label {{
      font-size: 12px;
      font-weight: 500;
      color: var(--text-muted);
    }}
    .stat-icon {{
      color: var(--text-sub);
      display: flex;
      align-items: center;
    }}
    .stat-val {{
      font-size: 24px;
      font-weight: 600;
      color: var(--text);
      line-height: 1.2;
      margin-bottom: 4px;
      font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    }}
    .stat-sub {{
      font-size: 11px;
      color: var(--text-sub);
    }}

    /* Table Container */
    .table-panel {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px 20px;
    }}
    .table-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .table-title {{
      font-size: 15px;
      font-weight: 600;
      color: var(--text);
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .search-input {{
      background: #18181b;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 10px;
      border-radius: 6px;
      font-size: 12px;
      width: 210px;
      outline: none;
    }}
    .search-input:focus {{
      border-color: var(--primary);
    }}
    .chip {{
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      background: #18181b;
      border: 1px solid var(--border);
      color: var(--text-muted);
      cursor: pointer;
    }}
    .chip:hover {{
      background: #27272a;
      color: var(--text);
    }}
    .chip.active {{
      background: var(--border);
      color: var(--text);
      border-color: #52525b;
    }}

    /* Table */
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 12px;
    }}
    th {{
      color: var(--text-sub);
      font-weight: 500;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
    }}
    td {{
      padding: 11px 12px;
      border-bottom: 1px solid var(--border-muted);
      vertical-align: middle;
      color: var(--text-muted);
    }}
    tr:hover td {{
      background: #18181b;
      color: var(--text);
    }}
    .mono {{
      font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 500;
      border: 1px solid transparent;
    }}
    .badge-ok {{
      background: rgba(34, 197, 94, 0.1);
      color: var(--success);
      border-color: rgba(34, 197, 94, 0.25);
    }}
    .badge-err {{
      background: rgba(239, 68, 68, 0.1);
      color: var(--error);
      border-color: rgba(239, 68, 68, 0.25);
    }}
    .badge-model {{
      background: #18181b;
      color: var(--text);
      border-color: var(--border);
    }}
    .badge-target {{
      background: rgba(56, 189, 248, 0.08);
      color: var(--primary);
      border-color: rgba(56, 189, 248, 0.2);
    }}
    .tool-badge {{
      background: #18181b;
      color: #cbd5e1;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      border: 1px solid var(--border);
      font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
      margin-right: 4px;
      display: inline-block;
    }}
    .empty-state {{
      text-align: center;
      padding: 40px 16px;
      color: var(--text-sub);
    }}
    .empty-icon {{
      margin-bottom: 8px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <div class="header">
      <div class="brand">
        <div class="brand-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>
        </div>
        <div>
          <div class="brand-title">ClaudeGate Live Console</div>
          <div class="brand-sub">Universal AI Gateway · Local Port {config.port}</div>
        </div>
      </div>
      <div class="header-actions">
        <div class="status-badge">
          <span class="status-dot" style="background: {cb_color};"></span>
          <span>Circuit: {cb_state}</span>
        </div>
        <button class="btn" id="btn-ping" onclick="pingUpstream()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
          <span>Ping Test</span>
        </button>
        <button class="btn" id="btn-refresh-toggle" onclick="toggleAutoRefresh()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
          <span id="refresh-label">Live (3s)</span>
        </button>
        <button class="btn btn-danger" onclick="clearHistory()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
          <span>Clear</span>
        </button>
      </div>
    </div>

    <!-- Stat Cards -->
    <div class="grid">
      <div class="stat-card">
        <div class="stat-header">
          <span class="stat-label">Total Requests</span>
          <span class="stat-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
          </span>
        </div>
        <div class="stat-val" id="stat-reqs">{summary['total_requests']}</div>
        <div class="stat-sub">Lifetime processed requests</div>
      </div>

      <div class="stat-card">
        <div class="stat-header">
          <span class="stat-label">Total Errors</span>
          <span class="stat-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
          </span>
        </div>
        <div class="stat-val" id="stat-errs" style="color: {'var(--error)' if summary['total_errors'] > 0 else 'var(--text)'};">{summary['total_errors']}</div>
        <div class="stat-sub" id="stat-health">100% gateway success rate</div>
      </div>

      <div class="stat-card">
        <div class="stat-header">
          <span class="stat-label">Average Latency</span>
          <span class="stat-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
          </span>
        </div>
        <div class="stat-val" id="stat-lat">{summary['avg_latency_ms']} ms</div>
        <div class="stat-sub">Upstream: {config.openai_base_url}</div>
      </div>

      <div class="stat-card">
        <div class="stat-header">
          <span class="stat-label">Tokens (In / Out)</span>
          <span class="stat-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>
          </span>
        </div>
        <div class="stat-val" id="stat-toks">{summary['total_input_tokens']} / {summary['total_output_tokens']}</div>
        <div class="stat-sub" id="stat-cached">Cached: {summary.get('total_cached_tokens', 0)} tokens</div>
      </div>
    </div>

    <!-- Table Container -->
    <div class="table-panel">
      <div class="table-top">
        <div class="table-title">Recent Flight Records</div>
        <div class="toolbar">
          <input type="text" class="search-input" id="search-input" placeholder="Filter requests..." oninput="filterRecords()">
          <button class="chip active" onclick="setFilter('all', this)">All</button>
          <button class="chip" onclick="setFilter('claude', this)">Claude</button>
          <button class="chip" onclick="setFilter('codex', this)">Codex</button>
          <button class="chip" onclick="setFilter('openai', this)">OpenAI</button>
          <button class="chip" onclick="setFilter('errors', this)">Errors</button>
        </div>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Request ID</th>
              <th>Inbound Model</th>
              <th>Routed Target</th>
              <th>Latency</th>
              <th>Tokens (In/Out)</th>
              <th>Status</th>
              <th>Tools</th>
            </tr>
          </thead>
          <tbody id="records-body">
            <tr>
              <td colspan="8" class="empty-state">
                <div class="empty-icon">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#71717a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
                </div>
                <div>Loading records...</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    let rawRecords = [];
    let activeFilter = 'all';
    let autoRefresh = true;
    let refreshTimer = null;

    const SVG_CHECK = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    const SVG_X = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';

    async function fetchTelemetry() {{
      try {{
        const res = await fetch('/api/flight-recorder');
        if (!res.ok) return;
        const data = await res.json();
        
        document.getElementById('stat-reqs').textContent = (data.summary.total_requests || 0).toLocaleString();
        const errs = data.summary.total_errors || 0;
        const reqs = data.summary.total_requests || 0;
        const errEl = document.getElementById('stat-errs');
        errEl.textContent = errs.toLocaleString();
        errEl.style.color = errs > 0 ? 'var(--error)' : 'var(--text)';
        
        const rate = reqs > 0 ? (((reqs - errs) / reqs) * 100).toFixed(1) : '100.0';
        document.getElementById('stat-health').textContent = rate + '% gateway success rate';
        document.getElementById('stat-lat').textContent = (data.summary.avg_latency_ms || 0) + ' ms';
        document.getElementById('stat-toks').textContent = (data.summary.total_input_tokens || 0).toLocaleString() + ' / ' + (data.summary.total_output_tokens || 0).toLocaleString();
        document.getElementById('stat-cached').textContent = 'Cached: ' + (data.summary.total_cached_tokens || 0).toLocaleString() + ' tokens';
        
        rawRecords = data.recent_requests || [];
        renderRecords();
      }} catch (e) {{
        console.error("Telemetry fetch error:", e);
      }}
    }}

    function setFilter(type, el) {{
      activeFilter = type;
      document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      renderRecords();
    }}

    function filterRecords() {{
      renderRecords();
    }}

    function renderRecords() {{
      const query = document.getElementById('search-input').value.toLowerCase();
      const tbody = document.getElementById('records-body');
      
      let filtered = rawRecords.filter(r => {{
        if (activeFilter === 'errors' && !(r.status_code >= 400 || r.error)) return false;
        if (activeFilter === 'claude' && !(r.claude_model && r.claude_model.startsWith('claude-'))) return false;
        if (activeFilter === 'codex' && !(r.target_model && (r.target_model.includes('codex') || r.target_model.includes('deepseek')) && !r.claude_model.startsWith('claude-'))) return false;
        
        if (query) {{
          const str = (r.id + ' ' + r.claude_model + ' ' + r.target_model + ' ' + r.status_code).toLowerCase();
          if (!str.includes(query)) return false;
        }}
        return true;
      }});

      if (filtered.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">' +
          '<div class="empty-icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#71717a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg></div>' +
          '<div>No records matching filter.</div>' +
        '</td></tr>';
        return;
      }}

      tbody.innerHTML = filtered.map(r => {{
        const time = r.timestamp ? r.timestamp.split('T')[1].split('.')[0] : '--:--:--';
        const isErr = r.status_code >= 400 || r.error;
        const statusBadge = isErr 
          ? '<span class="badge badge-err">' + SVG_X + ' ' + r.status_code + '</span>' 
          : '<span class="badge badge-ok">' + SVG_CHECK + ' ' + r.status_code + '</span>';
        
        const tools = (r.tools_called && r.tools_called.length > 0)
          ? r.tools_called.map(t => '<span class="tool-badge">' + t + '</span>').join('')
          : '<span style="color: var(--text-sub);">-</span>';

        return '<tr>' +
          '<td class="mono" style="color: var(--text-sub);">' + time + '</td>' +
          '<td class="mono" style="color: var(--primary);">' + r.id.substring(0, 8) + '...</td>' +
          '<td><span class="badge badge-model">' + (r.claude_model || 'unknown') + '</span></td>' +
          '<td><span class="badge badge-target">' + (r.target_model || 'upstream') + '</span></td>' +
          '<td><strong style="color: var(--text);">' + r.duration_ms + '</strong> <span style="font-size: 11px; color: var(--text-sub);">ms</span></td>' +
          '<td class="mono">' + r.input_tokens + ' / ' + r.output_tokens + '</td>' +
          '<td>' + statusBadge + '</td>' +
          '<td>' + tools + '</td>' +
        '</tr>';
      }}).join('');
    }}

    async function pingUpstream() {{
      const btn = document.getElementById('btn-ping');
      const label = btn.querySelector('span');
      label.textContent = 'Pinging...';
      const start = performance.now();
      try {{
        const res = await fetch('/test-connection');
        const lat = Math.round(performance.now() - start);
        if (res.ok) {{
          label.textContent = lat + 'ms OK';
        }} else {{
          label.textContent = 'Failed';
        }}
      }} catch (e) {{
        label.textContent = 'Offline';
      }}
      setTimeout(() => {{
        label.textContent = 'Ping Test';
      }}, 3000);
    }}

    async function clearHistory() {{
      if (!confirm('Clear logged flight records?')) return;
      try {{
        await fetch('/api/flight-recorder/clear', {{ method: 'POST' }});
        fetchTelemetry();
      }} catch (e) {{
        console.error(e);
      }}
    }}

    function toggleAutoRefresh() {{
      autoRefresh = !autoRefresh;
      const label = document.getElementById('refresh-label');
      if (autoRefresh) {{
        label.textContent = 'Live (3s)';
        refreshTimer = setInterval(fetchTelemetry, 3000);
      }} else {{
        label.textContent = 'Paused';
        clearInterval(refreshTimer);
      }}
    }}

    fetchTelemetry();
    refreshTimer = setInterval(fetchTelemetry, 3000);
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/health")
async def health_check():
    """Sanitized health check endpoint."""
    return {
        "status": "healthy",
        "service": "claudegate",
        "timestamp": datetime.now().isoformat(),
        "circuit_breaker": openai_client.circuit_breaker.state,
    }


@router.get("/test-connection")
async def test_connection(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """API connectivity probe to upstream provider."""
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await validate_api_key(request, x_api_key=x_api_key, authorization=authorization)
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
        logger.error("API connectivity test failed: %s", type(e).__name__)
        error_message = openai_client.classify_openai_error(type(e).__name__)
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
        "circuit_breaker": openai_client.circuit_breaker.state,
        "endpoints": {
            "messages": "/v1/messages",
            "count_tokens": "/v1/messages/count_tokens",
            "dashboard": "/dashboard",
            "flight_recorder": "/api/flight-recorder",
            "health": "/health",
            "hello": "/api/hello",
            "test_connection": "/test-connection",
        },
    }
