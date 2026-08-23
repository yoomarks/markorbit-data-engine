from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import Request

from app.integration_contract import (
    CONTRACT_VERSION,
    CONTRACT_VERSION_HEADER,
    REQUEST_ID_HEADER,
    SOURCE_OWNER,
    SOURCE_OWNER_HEADER,
)
from app.integration_g0_contract import CORRELATION_ID_HEADER


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def response_headers(path: str, request_id: str, correlation_id: str | None = None) -> dict[str, str]:
    headers = {REQUEST_ID_HEADER: request_id}
    if path == "/api/v1" or path.startswith("/api/v1/"):
        correlation = normalize_request_id(correlation_id) if correlation_id else request_id
        headers[CORRELATION_ID_HEADER] = correlation
        headers[CONTRACT_VERSION_HEADER] = CONTRACT_VERSION
        headers[SOURCE_OWNER_HEADER] = SOURCE_OWNER
    return headers


def install_integration_transport(app: Any) -> None:
    if getattr(app.state, "integration_transport_installed", False):
        return

    @app.middleware("http")
    async def integration_transport(request: Request, call_next):
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        correlation_id = (
            normalize_request_id(request.headers.get(CORRELATION_ID_HEADER))
            if request.headers.get(CORRELATION_ID_HEADER)
            else request_id
        )
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        for name, value in response_headers(request.url.path, request_id, correlation_id).items():
            response.headers[name] = value
        return response

    app.state.integration_transport_installed = True
