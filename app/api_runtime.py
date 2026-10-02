from __future__ import annotations

from collections.abc import Mapping
from ipaddress import ip_address
import os

import uvicorn

from app.integration_security import AUTH_MODE_REQUIRED, MIN_API_KEY_LENGTH


DEFAULT_API_PUBLISH_ADDRESS = "127.0.0.1"
REMOTE_BIND_OPT_IN = "API_ALLOW_REMOTE_BIND"


def _enabled(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"false", "0", "no", "off", ""}:
        return False
    if normalized in {"true", "1", "yes", "on"}:
        return True
    raise RuntimeError(f"{REMOTE_BIND_OPT_IN} must be either true or false.")


def validate_api_publish_configuration(environ: Mapping[str, str]) -> str:
    address = environ.get("API_PUBLISH_ADDRESS", DEFAULT_API_PUBLISH_ADDRESS).strip()
    try:
        parsed_address = ip_address(address)
    except ValueError as exc:
        raise RuntimeError("API_PUBLISH_ADDRESS must be a literal IPv4 or IPv6 address.") from exc

    remote_bind_allowed = _enabled(environ.get(REMOTE_BIND_OPT_IN, "false"))
    if parsed_address.is_loopback:
        return address

    if not remote_bind_allowed:
        raise RuntimeError("Non-loopback API_PUBLISH_ADDRESS requires API_ALLOW_REMOTE_BIND=true.")

    auth_mode = environ.get("INTEGRATION_AUTH_MODE", "disabled").strip().lower()
    if auth_mode != AUTH_MODE_REQUIRED:
        raise RuntimeError("Non-loopback API publishing requires INTEGRATION_AUTH_MODE=required.")

    api_keys = tuple(
        key.strip() for key in environ.get("INTEGRATION_API_KEYS", "").split(",") if key.strip()
    )
    if not api_keys or any(len(key) < MIN_API_KEY_LENGTH for key in api_keys):
        raise RuntimeError(
            "Non-loopback API publishing requires INTEGRATION_API_KEYS with keys of at least "
            f"{MIN_API_KEY_LENGTH} characters."
        )

    return address


def main() -> None:
    publish_address = validate_api_publish_configuration(os.environ)
    print(f"Data Engine API host publish boundary validated: {publish_address}", flush=True)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
