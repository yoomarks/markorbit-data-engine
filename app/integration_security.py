from __future__ import annotations

from hmac import compare_digest
from typing import Any

from fastapi import Header, HTTPException, status

from app.config import get_settings


AUTH_MODE_DISABLED = "disabled"
AUTH_MODE_REQUIRED = "required"
MIN_API_KEY_LENGTH = 32


def _configured_api_keys(raw: str) -> tuple[str, ...]:
    return tuple(key.strip() for key in raw.split(",") if key.strip())


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = value.strip()
    return token or None


def integration_security_contract() -> dict[str, Any]:
    settings = get_settings()
    mode = settings.integration_auth_mode.strip().lower()
    return {
        "scheme": "BEARER_API_KEY",
        "authorization_header": "Authorization: Bearer <key>",
        "auth_mode": mode,
        "default_mode": AUTH_MODE_DISABLED,
        "required_mode": AUTH_MODE_REQUIRED,
        "minimum_key_length": MIN_API_KEY_LENGTH,
        "multi_key_rotation": True,
        "fail_closed_when_required": True,
    }


def _configuration_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "DATA_ENGINE_INTEGRATION_AUTH_CONFIGURATION_INVALID",
            "message": message,
        },
    )


def require_integration_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    settings = get_settings()
    mode = settings.integration_auth_mode.strip().lower()
    if mode == AUTH_MODE_DISABLED:
        return
    if mode != AUTH_MODE_REQUIRED:
        raise _configuration_error(
            "INTEGRATION_AUTH_MODE must be either 'disabled' or 'required'."
        )

    keys = _configured_api_keys(settings.integration_api_keys)
    if not keys:
        raise _configuration_error(
            "INTEGRATION_API_KEYS must contain at least one key when authentication is required."
        )
    if any(len(key) < MIN_API_KEY_LENGTH for key in keys):
        raise _configuration_error(
            f"Every integration API key must be at least {MIN_API_KEY_LENGTH} characters."
        )

    token = _bearer_token(authorization)
    if token is None or not any(compare_digest(token, key) for key in keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "DATA_ENGINE_INTEGRATION_AUTH_REQUIRED",
                "message": "A valid Data Engine integration bearer key is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
