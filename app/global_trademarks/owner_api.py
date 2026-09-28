"""Bounded Global Hot owner API: same routes/truth, independent of CN serving cutover.

This ASGI entrypoint does not create schema, start a collector, or bypass the
shared CN application's own startup guard. Production deployment is separate.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.config import get_settings
from app.db import clickhouse_client
from app.fact_admission_security import MIN_FACT_ADMISSION_KEY_LENGTH
from app.global_trademarks.hot_global_admission import (
    CONTRACT_VERSION,
    require_hot_global_ready,
)
from app.global_trademarks.hot_global_api import admission_router, read_router
from app.integration_security import AUTH_MODE_REQUIRED, MIN_API_KEY_LENGTH


def _keys(value: str) -> tuple[str, ...]:
    return tuple(key.strip() for key in value.split(",") if key.strip())


def require_global_hot_owner_auth_ready() -> None:
    settings = get_settings()
    if settings.integration_auth_mode.strip().lower() != AUTH_MODE_REQUIRED:
        raise RuntimeError("Global Hot owner requires integration auth mode 'required'")

    admission_keys = _keys(settings.fact_admission_api_keys)
    read_keys = _keys(settings.integration_api_keys)
    if not admission_keys or any(
        len(key) < MIN_FACT_ADMISSION_KEY_LENGTH for key in admission_keys
    ):
        raise RuntimeError("Global Hot owner requires valid fact-admission credentials")
    if not read_keys or any(len(key) < MIN_API_KEY_LENGTH for key in read_keys):
        raise RuntimeError("Global Hot owner requires valid read API credentials")
    if set(admission_keys) & set(read_keys):
        raise RuntimeError("Global Hot admission and read credentials must be distinct")


def require_global_hot_owner_ready() -> None:
    require_global_hot_owner_auth_ready()
    client = clickhouse_client()
    try:
        require_hot_global_ready(client)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


@asynccontextmanager
async def _owner_lifespan(_app: FastAPI):
    require_global_hot_owner_ready()
    yield


def create_global_hot_owner_app() -> FastAPI:
    """Expose only the existing Global Hot owner routers and scoped readiness."""
    owner = FastAPI(
        title="MarkOrbit Data Engine Global Hot Owner",
        version=CONTRACT_VERSION,
        lifespan=_owner_lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    owner.include_router(admission_router)
    owner.include_router(read_router)

    @owner.get("/api/v1/health/global-hot", include_in_schema=False)
    def global_hot_health() -> dict[str, str]:
        try:
            require_global_hot_owner_ready()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "GLOBAL_HOT_OWNER_NOT_READY",
                    "message": "The Global Hot owner runtime is not ready.",
                },
            ) from exc
        return {
            "status": "ready",
            "owner": "GLOBAL_HOT",
            "contract_version": CONTRACT_VERSION,
            "storage_placement": "hot_global",
        }

    return owner


app = create_global_hot_owner_app()
