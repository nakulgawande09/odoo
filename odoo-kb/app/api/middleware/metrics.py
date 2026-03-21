"""Lightweight Prometheus-compatible metrics without external dependencies.

Exposes ``GET /metrics`` in Prometheus text exposition format.
No dependency on prometheus_client — we emit the text format directly.
"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import PlainTextResponse, Response


class Metrics:
    """Thread-safe in-memory metrics collector."""

    def __init__(self) -> None:
        self._lock = Lock()
        # request counters: (method, path, status) -> count
        self._request_counts: dict[tuple, int] = defaultdict(int)
        # latency buckets: (method, path) -> list of durations
        self._request_durations: dict[tuple, list[float]] = defaultdict(list)
        # Simple counters
        self._counters: dict[str, float] = defaultdict(float)
        # Simple gauges
        self._gauges: dict[str, float] = {}
        # Max durations to keep per endpoint (rolling window)
        self._max_samples = 1000

    def record_request(self, method: str, path: str, status: int, duration_s: float) -> None:
        """Record an HTTP request."""
        # Normalize path: strip IDs to reduce cardinality
        normalized = self._normalize_path(path)
        with self._lock:
            self._request_counts[(method, normalized, status)] += 1
            bucket = self._request_durations[(method, normalized)]
            bucket.append(duration_s)
            if len(bucket) > self._max_samples:
                self._request_durations[(method, normalized)] = bucket[-self._max_samples:]

    def inc_counter(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def _normalize_path(self, path: str) -> str:
        """Replace UUID-like segments with {id} to reduce cardinality."""
        import re
        return re.sub(
            r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            '/{id}', path,
        )

    def render(self) -> str:
        """Render metrics in Prometheus text exposition format."""
        lines: list[str] = []

        with self._lock:
            # Request counter
            lines.append("# HELP kb_http_requests_total Total HTTP requests")
            lines.append("# TYPE kb_http_requests_total counter")
            for (method, path, status), count in sorted(self._request_counts.items()):
                lines.append(
                    f'kb_http_requests_total{{method="{method}",path="{path}",'
                    f'status="{status}"}} {count}'
                )

            # Request duration summary
            lines.append("")
            lines.append("# HELP kb_http_request_duration_seconds HTTP request duration")
            lines.append("# TYPE kb_http_request_duration_seconds summary")
            for (method, path), durations in sorted(self._request_durations.items()):
                if not durations:
                    continue
                sorted_d = sorted(durations)
                count = len(sorted_d)
                total = sum(sorted_d)
                p50 = sorted_d[int(count * 0.5)] if count else 0
                p95 = sorted_d[int(count * 0.95)] if count else 0
                p99 = sorted_d[int(count * 0.99)] if count else 0
                labels = f'method="{method}",path="{path}"'
                lines.append(f'kb_http_request_duration_seconds{{{labels},quantile="0.5"}} {p50:.6f}')
                lines.append(f'kb_http_request_duration_seconds{{{labels},quantile="0.95"}} {p95:.6f}')
                lines.append(f'kb_http_request_duration_seconds{{{labels},quantile="0.99"}} {p99:.6f}')
                lines.append(f'kb_http_request_duration_seconds_sum{{{labels}}} {total:.6f}')
                lines.append(f'kb_http_request_duration_seconds_count{{{labels}}} {count}')

            # Custom counters
            if self._counters:
                lines.append("")
                for name, value in sorted(self._counters.items()):
                    lines.append(f"# TYPE {name} counter")
                    lines.append(f"{name} {value}")

            # Custom gauges
            if self._gauges:
                lines.append("")
                for name, value in sorted(self._gauges.items()):
                    lines.append(f"# TYPE {name} gauge")
                    lines.append(f"{name} {value}")

        lines.append("")
        return "\n".join(lines)


# Global metrics instance
metrics = Metrics()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware that records request metrics."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        # Don't track metrics endpoint itself
        if request.url.path != "/metrics":
            metrics.record_request(
                request.method, request.url.path,
                response.status_code, duration,
            )
        return response


def register_metrics_endpoint(app: FastAPI) -> None:
    """Add the /metrics endpoint to the app."""

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
