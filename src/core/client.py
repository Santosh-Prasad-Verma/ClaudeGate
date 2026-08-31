import asyncio
import json
import logging
from fastapi import HTTPException
from typing import Optional, AsyncGenerator, Dict, Any
from openai import AsyncOpenAI, AsyncAzureOpenAI
from openai._exceptions import APIError, RateLimitError, AuthenticationError, BadRequestError

logger = logging.getLogger(__name__)

class OpenAIClient:
    """Async OpenAI client with cancellation support."""
    
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int = 90,
        api_version: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        fallback_base_url: Optional[str] = None,
        fallback_api_key: Optional[str] = None,
        fallback_model: Optional[str] = None,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.custom_headers = custom_headers or {}
        self.fallback_base_url = fallback_base_url
        self.fallback_api_key = fallback_api_key
        self.fallback_model = fallback_model
        self.max_retries = max(0, max_retries)

        # Prepare default headers
        default_headers = {
            "Content-Type": "application/json",
            "User-Agent": "claudegate/1.0.0"
        }
        
        # Merge custom headers with default headers
        all_headers = {**default_headers, **self.custom_headers}
        
        # Detect if using Azure and instantiate the appropriate client
        if api_version:
            self.client = AsyncAzureOpenAI(
                api_key=api_key,
                azure_endpoint=base_url,
                api_version=api_version,
                timeout=timeout,
                default_headers=all_headers
            )
        else:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                default_headers=all_headers
            )

        # Fallback client if configured
        self.fallback_client = None
        if self.fallback_base_url and self.fallback_api_key:
            self.fallback_client = AsyncOpenAI(
                api_key=self.fallback_api_key,
                base_url=self.fallback_base_url,
                timeout=timeout,
                default_headers=all_headers
            )

        self.active_requests: Dict[str, asyncio.Event] = {}
    
    async def create_chat_completion(self, request: Dict[str, Any], request_id: Optional[str] = None) -> Dict[str, Any]:
        """Send chat completion to OpenAI API with cancellation and automatic failover support."""
        
        # Create cancellation token if request_id provided
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event
        
        completion_task = None
        cancel_task = None
        try:
            # Create task that can be cancelled with retry
            async def _create_with_retry():
                last_err = None
                for attempt in range(self.max_retries + 1):
                    try:
                        return await self.client.chat.completions.create(**request)
                    except Exception as err:
                        last_err = err
                        status = getattr(err, 'status_code', None)
                        retryable = (
                            status in (429, 500, 502, 503, 504)
                            or 'overload' in str(err).lower()
                            or 'concurrency' in str(err).lower()
                            or any(code in str(err).lower() for code in ("429", "500", "502", "503", "504"))
                        )
                        if retryable and attempt < self.max_retries:
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                        if self.fallback_client and retryable:
                            fallback_req = dict(request)
                            if self.fallback_model:
                                fallback_req["model"] = self.fallback_model
                            try:
                                return await self.fallback_client.chat.completions.create(**fallback_req)
                            except Exception as fb_err:
                                logger.debug("Fallback attempt failed: %s", type(fb_err).__name__)
                        raise
                if last_err:
                    raise last_err
                raise RuntimeError("Failed to complete request after retries")

            completion_task = asyncio.create_task(_create_with_retry())
            
            if request_id:
                # Wait for either completion or cancellation
                cancel_task = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    [completion_task, cancel_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel pending tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass  # Expected when task is cancelled
                    except Exception as exc:
                        logger.debug("Ignoring exception while awaiting cancelled pending task: %s", exc)
                
                # Check if request was cancelled
                if cancel_task in done:
                    completion_task.cancel()
                    try:
                        await completion_task
                    except asyncio.CancelledError:
                        pass  # Expected when task is cancelled
                    except Exception as exc:
                        logger.debug("Ignoring exception while awaiting cancelled completion task: %s", exc)
                    raise HTTPException(status_code=499, detail="Request cancelled by client")
                
                completion = await completion_task
            else:
                completion = await completion_task
            
            # Convert to dict format that matches the original interface
            return completion.model_dump()
        
        except HTTPException:
            raise
        except asyncio.CancelledError:
            if completion_task and not completion_task.done():
                completion_task.cancel()
                try:
                    await completion_task
                except asyncio.CancelledError:
                    pass  # Task cancellation expected
                except Exception as exc:
                    logger.debug("Ignoring exception while awaiting cancelled completion task: %s", exc)
            if cancel_task and not cancel_task.done():
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass  # Cancel task cancellation expected
                except Exception as exc:
                    logger.debug("Ignoring exception while awaiting cancelled task: %s", exc)
            raise
        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=self.classify_openai_error(str(e)))
        except RateLimitError as e:
            raise HTTPException(status_code=429, detail=self.classify_openai_error(str(e)))
        except BadRequestError as e:
            raise HTTPException(status_code=400, detail=self.classify_openai_error(str(e)))
        except APIError as e:
            status_code = getattr(e, 'status_code', 500)
            raise HTTPException(status_code=status_code, detail=self.classify_openai_error(str(e)))
        except Exception:
            raise HTTPException(status_code=500, detail="Unexpected upstream error")
        
        finally:
            # Clean up active request tracking
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]
    
    async def create_chat_completion_stream(self, request: Dict[str, Any], request_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """Send streaming chat completion with cancellation and automatic failover support."""
        
        # Create cancellation token if request_id provided
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event
        
        streaming_completion = None
        stream_closed = False

        async def close_stream_once() -> None:
            nonlocal stream_closed
            if streaming_completion is None or stream_closed:
                return
            stream_closed = True
            if hasattr(streaming_completion, "aclose"):
                try:
                    await streaming_completion.aclose()
                except Exception as e:
                    logger.debug("Failed closing stream: %s", type(e).__name__)
            elif hasattr(streaming_completion, "close"):
                try:
                    result = streaming_completion.close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.debug("Failed closing stream: %s", type(e).__name__)

        try:
            # Ensure stream is enabled
            request = dict(request)
            request["stream"] = True
            request["stream_options"] = dict(request.get("stream_options") or {})
            request["stream_options"]["include_usage"] = True
            
            # Create the streaming completion with automatic retry and fallback
            last_error = None
            for attempt in range(self.max_retries + 1):
                try:
                    streaming_completion = await self.client.chat.completions.create(**request)
                    last_error = None
                    break
                except Exception as err:
                    last_error = err
                    status = getattr(err, 'status_code', None)
                    err_str = str(err).lower()
                    if status in (429, 500, 502, 503, 504) or 'overload' in err_str or 'concurrency' in err_str or 'bad_response' in err_str:
                        if attempt < self.max_retries:
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                    break
            
            # Fallback is only appropriate for transient primary failures.
            primary_status = getattr(last_error, "status_code", None)
            primary_error = str(last_error).lower() if last_error else ""
            primary_retryable = (
                primary_status in (429, 500, 502, 503, 504)
                or "overload" in primary_error
                or "concurrency" in primary_error
                or "bad_response" in primary_error
                or any(code in primary_error for code in ("429", "500", "502", "503", "504"))
            )
            if streaming_completion is None and self.fallback_client and primary_retryable:
                fallback_req = dict(request)
                if self.fallback_model:
                    fallback_req["model"] = self.fallback_model
                try:
                    streaming_completion = await self.fallback_client.chat.completions.create(**fallback_req)
                    last_error = None
                except Exception as fb_err:
                    last_error = fb_err

            # If all attempts exhausted
            if streaming_completion is None:
                err_msg = self.classify_openai_error(last_error) if last_error else "All upstream attempts exhausted"
                status = getattr(last_error, "status_code", 503) if last_error else 503
                yield f"ERROR::{status}::{err_msg}"
                return
            
            async for chunk in streaming_completion:
                # Check for cancellation before yielding each chunk
                if request_id and request_id in self.active_requests:
                    if self.active_requests[request_id].is_set():
                        yield "ERROR::499::Request cancelled by client"
                        return
                
                # Convert chunk to SSE format matching original HTTP client format
                chunk_dict = chunk.model_dump()
                chunk_json = json.dumps(chunk_dict, ensure_ascii=False)
                yield f"data: {chunk_json}"
            
            # Signal end of stream
            yield "data: [DONE]"
                
        except asyncio.CancelledError:
            # Cleanup is centralized in finally so it runs exactly once.
            raise
        except Exception as e:
            # NEVER raise from inside an async generator used by StreamingResponse.
            # Yield an error marker that response_converter will handle gracefully.
            status = getattr(e, 'status_code', 500)
            yield f"ERROR::{status}::{self.classify_openai_error(e)}"
        
        finally:
            await close_stream_once()
            # Clean up active request tracking
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]

    def classify_openai_error(self, error_detail: Any) -> str:
        """Provide specific error guidance for common OpenAI API issues."""
        error_str = str(error_detail).lower()
        
        # Region/country restrictions
        if "unsupported_country_region_territory" in error_str or "country, region, or territory not supported" in error_str:
            return "OpenAI API is not available in your region. Consider using a VPN or Azure OpenAI service."
        
        # API key issues
        if "invalid_api_key" in error_str or "unauthorized" in error_str:
            return "Invalid API key. Please check your OPENAI_API_KEY configuration."
        
        # Rate limiting
        if "rate_limit" in error_str or "quota" in error_str:
            return "Rate limit exceeded. Please wait and try again, or upgrade your API plan."
        
        # Model not found
        if "model" in error_str and ("not found" in error_str or "does not exist" in error_str):
            return "Model not found. Please check your BIG_MODEL and SMALL_MODEL configuration."
        
        # Billing issues
        if "billing" in error_str or "payment" in error_str:
            return "Billing issue. Please check your OpenAI account billing status."
        
        # Connection and network issues
        if "connect" in error_str or "timeout" in error_str or "unreachable" in error_str:
            return "Unable to reach upstream provider. Please check network connectivity and base URL."

        # Never expose raw provider errors, URLs, or credentials to clients.
        return "Upstream provider request failed"
    
    def cancel_request(self, request_id: str) -> bool:
        """Cancel an active request by request_id."""
        if request_id in self.active_requests:
            self.active_requests[request_id].set()
            return True
        return False