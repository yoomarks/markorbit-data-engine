from __future__ import annotations

from hmac import compare_digest

from fastapi import Header, HTTPException, status

from app.config import get_settings


MIN_FACT_ADMISSION_KEY_LENGTH = 32


def _keys(raw: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def require_fact_admission_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    keys = _keys(get_settings().fact_admission_api_keys)
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_FACT_ADMISSION_AUTH_NOT_CONFIGURED",
                "message": "Fact admission credentials are not configured.",
            },
        )
    if any(len(key) < MIN_FACT_ADMISSION_KEY_LENGTH for key in keys):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_FACT_ADMISSION_AUTH_CONFIGURATION_INVALID",
                "message": (
                    "Every fact admission API key must be at least "
                    f"{MIN_FACT_ADMISSION_KEY_LENGTH} characters."
                ),
            },
        )
    scheme, _, value = (authorization or "").partition(" ")
    token = value.strip() if scheme.lower() == "bearer" else ""
    if not token or not any(compare_digest(token, key) for key in keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "DATA_ENGINE_FACT_ADMISSION_AUTH_REQUIRED",
                "message": "A valid fact admission bearer key is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
