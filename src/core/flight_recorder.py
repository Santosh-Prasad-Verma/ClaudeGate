import time
import threading
from collections import deque
from datetime import datetime
from typing import Dict, Any, List, Optional


class FlightRecorder:
    """Thread-safe in-memory ring buffer recording recent requests, latency, and token throughput."""

    def __init__(self, maxlen: int = 100):
        self.records = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.total_requests = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cached_tokens = 0
        self.total_errors = 0
        self.start_time = time.time()

    def record_completion(
        self,
        request_id: str,
        claude_model: str,
        target_model: str,
        stream: bool,
        duration_ms: float,
        status_code: int = 200,
        ttft_ms: Optional[float] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        tools_called: Optional[List[str]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Add a sanitized request record to the ring buffer and update global stats."""
        record = {
            "id": request_id,
            "timestamp": datetime.now().isoformat(),
            "claude_model": claude_model,
            "target_model": target_model,
            "stream": stream,
            "duration_ms": round(duration_ms, 1),
            "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
            "status_code": status_code,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "tools_called": tools_called or [],
            "error": error,
        }

        with self._lock:
            self.records.appendleft(record)
            self.total_requests += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_cached_tokens += cached_tokens
            if status_code >= 400 or error:
                self.total_errors += 1

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent request records."""
        with self._lock:
            return list(self.records)[:limit]

    def get_summary(self) -> Dict[str, Any]:
        """Return aggregate runtime metrics."""
        with self._lock:
            uptime_seconds = max(1.0, time.time() - self.start_time)
            recent_latencies = [r["duration_ms"] for r in self.records if r.get("duration_ms")]
            avg_latency = round(sum(recent_latencies) / len(recent_latencies), 1) if recent_latencies else 0.0

            return {
                "uptime_seconds": round(uptime_seconds, 1),
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_cached_tokens": self.total_cached_tokens,
                "avg_latency_ms": avg_latency,
                "recent_count": len(self.records),
            }

    def clear(self) -> None:
        """Reset records and counters."""
        with self._lock:
            self.records.clear()
            self.total_requests = 0
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.total_cached_tokens = 0
            self.total_errors = 0


flight_recorder = FlightRecorder(maxlen=100)
