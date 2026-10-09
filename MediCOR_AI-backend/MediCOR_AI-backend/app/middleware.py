"""
Cross-cutting HTTP middleware — Phase 8.

- RequestContextMiddleware: assigns a short request id, logs method/path/
  status/duration for every request (structured, one line per request),
  and echoes the request id back as a response header so a client-side
  error report can be correlated with a server-side log line.
- SecurityHeadersMiddleware: adds standard defensive response headers.
  These are safe additions for any client (dashboard, voice agent, API
  consumers) and don't change response bodies or status codes.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                f"req={request_id} {request.method} {request.url.path} "
                f"-> UNHANDLED after {duration_ms:.1f}ms"
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            f"req={request_id} {request.method} {request.url.path} "
            f"-> {response.status_code} in {duration_ms:.1f}ms"
        )
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # Only meaningful over HTTPS; harmless no-op over plain HTTP (e.g. local dev).
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
        return response