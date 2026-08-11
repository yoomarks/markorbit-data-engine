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


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def install_integration_transport(app: Any) -> None:
    if getattr(app.state, "integration_transport_installed", False):
        return

    @app.middleware("http")
    async def integration_transport(request: Request, call_next):
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        if request.url.path == "/api/v1" or request.url.path.startswith("/api/v1/"):
            response.headers[CONTRACT_VERSION_HEADER] = CONTRACT_VERSION
            response.headers[SOURCE_OWNER_HEADER] = SOURCE_OWNER
        return response

    app.state.integration_transport_installed = True
