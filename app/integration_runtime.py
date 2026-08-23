from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings


class IntegrationRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def check(self, request: Request) -> None:
        settings = get_settings()
        if not settings.integration_rate_limit_enabled:
            return
        window = max(int(settings.integration_rate_limit_window_seconds), 1)
        maximum = max(int(settings.integration_rate_limit_max_requests), 1)
        subject = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            start, count = self._windows.get(subject, (now, 0))
            if now - start >= window:
                start, count = now, 0
            count += 1
            self._windows[subject] = (start, count)
        if count <= maximum:
            return
        retry_after = max(int(window - (now - start)), 1)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "DATA_ENGINE_INTEGRATION_RATE_LIMITED",
                "message": "Data Engine integration rate limit exceeded.",
                "retryable": True,
            },
            headers={"Retry-After": str(retry_after)},
        )


rate_limiter = IntegrationRateLimiter()


def enforce_integration_rate_limit(request: Request) -> None:
    rate_limiter.check(request)


def _is_integration_path(path: str) -> bool:
    return path == "/api/v1" or path.startswith("/api/v1/")


def _error_payload(status_code: int, detail: Any) -> dict[str, Any]:
    retryable = status_code in {429, 500, 502, 503, 504}
    fact_state: str | None = None
    default_codes = {
        400: "DATA_ENGINE_INTEGRATION_INVALID_QUERY",
        401: "DATA_ENGINE_INTEGRATION_AUTH_REQUIRED",
        403: "DATA_ENGINE_INTEGRATION_FORBIDDEN",
        404: "DATA_ENGINE_INTEGRATION_NOT_FOUND",
        409: "DATA_ENGINE_INTEGRATION_CONTRACT_CONFLICT",
        429: "DATA_ENGINE_INTEGRATION_RATE_LIMITED",
        500: "DATA_ENGINE_INTEGRATION_INTERNAL_ERROR",
        502: "DATA_ENGINE_INTEGRATION_UPSTREAM_FAILURE",
        503: "DATA_ENGINE_INTEGRATION_SERVICE_UNAVAILABLE",
        504: "DATA_ENGINE_INTEGRATION_TIMEOUT",
    }
    code = default_codes.get(status_code, "DATA_ENGINE_INTEGRATION_ERROR")
    message = "Data Engine integration request failed."
    extra: Any | None = None
    if isinstance(detail, dict):
        code = str(detail.get("code") or code)
        message = str(detail.get("message") or detail.get("error") or message)
        retryable = bool(detail.get("retryable", retryable))
        extra = {k: v for k, v in detail.items() if k not in {"code", "message", "retryable"}} or None
    elif detail:
        message = str(detail)
    if status_code == 404:
        fact_state = "not_found"
    elif status_code >= 500:
        fact_state = "service_unavailable"
    payload: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if extra is not None:
        payload["detail"] = extra
    if fact_state is not None:
        payload["fact_state"] = fact_state
    return payload


def install_integration_runtime(app: Any) -> None:
    if getattr(app.state, "integration_runtime_installed", False):
        return

    @app.exception_handler(StarletteHTTPException)
    async def integration_http_exception_handler(request: Request, exc: StarletteHTTPException):
        if not _is_integration_path(request.url.path):
            return await http_exception_handler(request, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.status_code, exc.detail),
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(RequestValidationError)
    async def integration_validation_exception_handler(request: Request, exc: RequestValidationError):
        if not _is_integration_path(request.url.path):
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(
            status_code=400,
            content={
                "code": "DATA_ENGINE_INTEGRATION_INVALID_QUERY",
                "message": "Integration query validation failed.",
                "retryable": False,
                "detail": {"errors": exc.errors()},
            },
        )

    app.state.integration_runtime_installed = True
