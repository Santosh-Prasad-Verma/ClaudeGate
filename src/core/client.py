import asyncio
import json
import time
import logging
from fastapi import HTTPException
from typing import Optional, AsyncGenerator, Dict, Any
from openai import AsyncOpenAI, AsyncAzureOpenAI
from openai._exceptions import APIError, RateLimitError, AuthenticationError, BadRequestError

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """3-State Circuit Breaker (CLOSED -> OPEN -> HALF_OPEN) for upstream LLM providers."""

    def __init__(self, threshold: int = 3, reset_timeout: float = 60.0, enabled: bool = True):
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.enabled = enabled
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def record_success(self) -> None:
        if not self.enabled:
            return
        if self.state in ("HALF_OPEN", "OPEN"):
            logger.info("Circuit breaker recovered: State is now CLOSED")
        self.state = "CLOSED"
        self.failure_count = 0

    def record_failure(self) -> None:
        if not self.enabled:
            return
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.threshold:
            if self.state != "OPEN":
                logger.warning(
                    "Circuit breaker TRIPPED to OPEN after %d consecutive failures. Fast-failing to backup for %.0fs.",
                    self.failure_count,
                    self.reset_timeout,
                )
            self.state = "OPEN"

    def can_attempt_primary(self) -> bool:
        if not self.enabled:
            return True
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if time.time() - self.last_failure_time >= self.reset_timeout:
                self.state = "HALF_OPEN"
                logger.info("Circuit breaker test probe: State is now HALF_OPEN")
                return True
            return False
        if self.state == "HALF_OPEN":
            return True
        return True


class OpenAIClient:
    """Async OpenAI client with cancellation, circuit breaking, multi-key rotation, and multi-tier routing support."""

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
        circuit_breaker_enabled: bool = True,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_reset_timeout: float = 60.0,
        tier_endpoints: Optional[Dict[str, Dict[str, str]]] = None,
        api_keys: Optional[list] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.custom_headers = custom_headers or {}
        self.fallback_base_url = fallback_base_url
        self.fallback_api_key = fallback_api_key
        self.fallback_model = fallback_model
        self.max_retries = max(0, max_retries)

        # Multi-key pool & round-robin rotation
        raw_keys = api_keys if api_keys else ([api_key] if api_key else [])
        self.api_keys = [k.strip() for k in raw_keys if k and k.strip()]
        if not self.api_keys and api_key:
            self.api_keys = [api_key]
        self.primary_api_key = self.api_keys[0] if self.api_keys else api_key
        self.key_cooldowns: Dict[str, float] = {}
        self._key_index = 0

        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(
            threshold=circuit_breaker_threshold,
            reset_timeout=circuit_breaker_reset_timeout,
            enabled=circuit_breaker_enabled,
        )

        # Prepare default headers
        default_headers = {
            "Content-Type": "application/json",
            "User-Agent": "claudegate/1.0.0",
        }
        all_headers = {**default_headers, **self.custom_headers}

        # Initialize clients for all configured keys
        self.key_clients: Dict[str, AsyncOpenAI] = {}
        for k in self.api_keys:
            if api_version:
                self.key_clients[k] = AsyncAzureOpenAI(
                    api_key=k,
                    azure_endpoint=base_url,
                    api_version=api_version,
                    timeout=timeout,
                    default_headers=all_headers,
                )
            else:
                self.key_clients[k] = AsyncOpenAI(
                    api_key=k,
                    base_url=base_url,
                    timeout=timeout,
                    default_headers=all_headers,
                )

        # Default primary client
        if self.primary_api_key in self.key_clients:
            self.client = self.key_clients[self.primary_api_key]
        elif api_version:
            self.client = AsyncAzureOpenAI(
                api_key=api_key,
                azure_endpoint=base_url,
                api_version=api_version,
                timeout=timeout,
                default_headers=all_headers,
            )
        else:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                default_headers=all_headers,
            )

        # Dedicated per-tier clients (if custom base URLs or keys configured)
        self.tier_clients: Dict[str, AsyncOpenAI] = {}
        if tier_endpoints:
            for model_id, ep_info in tier_endpoints.items():
                if ep_info.get("base_url") and ep_info.get("api_key"):
                    if ep_info.get("base_url") != base_url or ep_info.get("api_key") != api_key:
                        self.tier_clients[model_id] = AsyncOpenAI(
                            api_key=ep_info["api_key"],
                            base_url=ep_info["base_url"],
                            timeout=timeout,
                            default_headers=all_headers,
                        )

        # Fallback client if configured
        self.fallback_client = None
        if self.fallback_base_url and self.fallback_api_key:
            self.fallback_client = AsyncOpenAI(
                api_key=self.fallback_api_key,
                base_url=self.fallback_base_url,
                timeout=timeout,
                default_headers=all_headers,
            )

        self.active_requests: Dict[str, asyncio.Event] = {}

    def get_client_and_key_for_model(self, model: str) -> tuple[AsyncOpenAI, Optional[str]]:
        """Return (client, api_key) for the target model using tier routing or round-robin key pool."""
        if model in self.tier_clients:
            return self.tier_clients[model], None

        if not self.api_keys:
            return self.client, None

        now = time.time()
        n = len(self.api_keys)
        # Find next available key not in cooldown
        for i in range(n):
            idx = (self._key_index + i) % n
            candidate = self.api_keys[idx]
            if self.key_cooldowns.get(candidate, 0.0) <= now:
                self._key_index = (idx + 1) % n
                return self.key_clients[candidate], candidate

        # If all keys are in cooldown, select the one recovering soonest
        soonest_key = min(self.api_keys, key=lambda k: self.key_cooldowns.get(k, 0.0))
        self._key_index = (self.api_keys.index(soonest_key) + 1) % n
        return self.key_clients[soonest_key], soonest_key

    def get_client_for_model(self, model: str) -> AsyncOpenAI:
        """Return the tier-specific client for model or default primary client."""
        client, _ = self.get_client_and_key_for_model(model)
        return client

    def record_key_rate_limit(self, key: Optional[str], cooldown_seconds: float = 60.0) -> None:
        """Mark a specific key as cooling down due to 429 RateLimitError."""
        if key and key in self.key_clients:
            masked = f"...{key[-4:]}" if len(key) >= 4 else key
            logger.warning("API key %s hit 429 Rate Limit. Cooling down for %.0fs.", masked, cooldown_seconds)
            self.key_cooldowns[key] = time.time() + cooldown_seconds

    async def create_chat_completion(
        self, request: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send chat completion to OpenAI API with circuit breaking, cancellation, and automatic failover."""

        cancel_event = None
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

        target_model = request.get("model", "")
        active_client, current_key = self.get_client_and_key_for_model(target_model)

        completion_task = None
        cancel_task = None
        try:
            # Check if circuit breaker is tripped on primary
            if (
                active_client == self.client
                and not self.circuit_breaker.can_attempt_primary()
                and self.fallback_client
            ):
                logger.warning(
                    "Circuit breaker is OPEN. Routing request directly to fallback without retry delay."
                )
                fallback_req = dict(request)
                if self.fallback_model:
                    fallback_req["model"] = self.fallback_model
                completion = await self.fallback_client.chat.completions.create(**fallback_req)
                return completion.model_dump()

            async def _create_with_retry():
                nonlocal active_client, current_key
                last_err = None
                for attempt in range(self.max_retries + 1):
                    try:
                        res = await active_client.chat.completions.create(**request)
                        self.circuit_breaker.record_success()
                        return res
                    except Exception as err:
                        last_err = err
                        status = getattr(err, "status_code", None)
                        err_str = str(err).lower()
                        is_429 = status == 429 or "rate_limit" in err_str or "quota" in err_str or "429" in err_str

                        # Multi-key automatic failover
                        if is_429 and current_key:
                            self.record_key_rate_limit(current_key)
                            if len(self.api_keys) > 1:
                                active_client, current_key = self.get_client_and_key_for_model(target_model)
                                logger.info("Switched to next API key after 429 rate limit")
                                continue

                        retryable = (
                            is_429
                            or status in (500, 502, 503, 504)
                            or "overload" in err_str
                            or "concurrency" in err_str
                            or any(code in err_str for code in ("500", "502", "503", "504"))
                        )

                        if retryable and attempt < self.max_retries:
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue

                        if retryable:
                            self.circuit_breaker.record_failure()

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

            if request_id and cancel_event:
                cancel_task = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    [completion_task, cancel_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.debug("Ignoring exception while awaiting cancelled pending task: %s", exc)

                if cancel_task in done:
                    completion_task.cancel()
                    try:
                        await completion_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.debug("Ignoring exception while awaiting cancelled completion task: %s", exc)
                    raise HTTPException(status_code=499, detail="Request cancelled by client")

                completion = await completion_task
            else:
                completion = await completion_task

            return completion.model_dump()

        except HTTPException:
            raise
        except asyncio.CancelledError:
            if completion_task and not completion_task.done():
                completion_task.cancel()
                try:
                    await completion_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug("Ignoring exception while awaiting cancelled completion task: %s", exc)
            if cancel_task and not cancel_task.done():
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass
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
            status_code = getattr(e, "status_code", 500)
            raise HTTPException(status_code=status_code, detail=self.classify_openai_error(str(e)))
        except Exception:
            raise HTTPException(status_code=500, detail="Unexpected upstream error")

        finally:
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]

    async def create_chat_completion_stream(
        self, request: Dict[str, Any], request_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Send streaming chat completion with circuit breaking, cancellation, and automatic failover."""

        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

        target_model = request.get("model", "")
        active_client, current_key = self.get_client_and_key_for_model(target_model)

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
            request = dict(request)
            request["stream"] = True
            request["stream_options"] = dict(request.get("stream_options") or {})
            request["stream_options"]["include_usage"] = True

            last_error = None

            # Check if circuit breaker is tripped on primary
            if (
                active_client == self.client
                and not self.circuit_breaker.can_attempt_primary()
                and self.fallback_client
            ):
                logger.warning(
                    "Circuit breaker is OPEN. Routing stream directly to fallback without retry delay."
                )
                fallback_req = dict(request)
                if self.fallback_model:
                    fallback_req["model"] = self.fallback_model
                try:
                    streaming_completion = await self.fallback_client.chat.completions.create(**fallback_req)
                except Exception as fb_err:
                    last_error = fb_err
            else:
                for attempt in range(self.max_retries + 1):
                    try:
                        streaming_completion = await active_client.chat.completions.create(**request)
                        self.circuit_breaker.record_success()
                        last_error = None
                        break
                    except Exception as err:
                        last_error = err
                        status = getattr(err, "status_code", None)
                        err_str = str(err).lower()
                        is_429 = status == 429 or "rate_limit" in err_str or "quota" in err_str or "429" in err_str

                        # Multi-key automatic failover for streaming
                        if is_429 and current_key:
                            self.record_key_rate_limit(current_key)
                            if len(self.api_keys) > 1:
                                active_client, current_key = self.get_client_and_key_for_model(target_model)
                                logger.info("Switched to next API key for streaming after 429 rate limit")
                                continue

                        if (
                            is_429
                            or status in (500, 502, 503, 504)
                            or "overload" in err_str
                            or "concurrency" in err_str
                            or "bad_response" in err_str
                        ):
                            if attempt < self.max_retries:
                                await asyncio.sleep(1.5 * (attempt + 1))
                                continue
                        break

                primary_status = getattr(last_error, "status_code", None)
                primary_error = str(last_error).lower() if last_error else ""
                primary_retryable = (
                    primary_status in (429, 500, 502, 503, 504)
                    or "overload" in primary_error
                    or "concurrency" in primary_error
                    or "bad_response" in primary_error
                    or any(code in primary_error for code in ("429", "500", "502", "503", "504"))
                )

                if primary_retryable:
                    self.circuit_breaker.record_failure()

                if streaming_completion is None and self.fallback_client and primary_retryable:
                    fallback_req = dict(request)
                    if self.fallback_model:
                        fallback_req["model"] = self.fallback_model
                    try:
                        streaming_completion = await self.fallback_client.chat.completions.create(**fallback_req)
                        last_error = None
                    except Exception as fb_err:
                        last_error = fb_err

            if streaming_completion is None:
                err_msg = self.classify_openai_error(last_error) if last_error else "All upstream attempts exhausted"
                status = getattr(last_error, "status_code", 503) if last_error else 503
                yield f"ERROR::{status}::{err_msg}"
                return

            async for chunk in streaming_completion:
                if request_id and request_id in self.active_requests:
                    if self.active_requests[request_id].is_set():
                        yield "ERROR::499::Request cancelled by client"
                        return

                chunk_dict = chunk.model_dump()
                chunk_json = json.dumps(chunk_dict, ensure_ascii=False)
                yield f"data: {chunk_json}"

            yield "data: [DONE]"

        except asyncio.CancelledError:
            raise
        except Exception as e:
            status = getattr(e, "status_code", 500)
            yield f"ERROR::{status}::{self.classify_openai_error(e)}"

        finally:
            await close_stream_once()
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]

    def classify_openai_error(self, error_detail: Any) -> str:
        """Provide specific error guidance for common OpenAI API issues."""
        error_str = str(error_detail).lower()

        if "unsupported_country_region_territory" in error_str or "country, region, or territory not supported" in error_str:
            return "OpenAI API is not available in your region. Consider using a VPN or Azure OpenAI service."
        if "invalid_api_key" in error_str or "unauthorized" in error_str:
            return "Invalid API key. Please check your OPENAI_API_KEY configuration."
        if "rate_limit" in error_str or "quota" in error_str:
            return "Rate limit exceeded. Please wait and try again, or upgrade your API plan."
        if "model" in error_str and ("not found" in error_str or "does not exist" in error_str):
            return "Model not found. Please check your BIG_MODEL and SMALL_MODEL configuration."
        if "billing" in error_str or "payment" in error_str:
            return "Billing issue. Please check your OpenAI account billing status."
        if "connect" in error_str or "timeout" in error_str or "unreachable" in error_str:
            return "Unable to reach upstream provider. Please check network connectivity and base URL."

        return "Upstream provider request failed"

    def cancel_request(self, request_id: str) -> bool:
        """Cancel an active request by request_id."""
        if request_id in self.active_requests:
            self.active_requests[request_id].set()
            return True
        return False