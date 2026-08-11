from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import integration_security


PRIMARY_KEY = "a" * integration_security.MIN_API_KEY_LENGTH
ROTATION_KEY = "b" * integration_security.MIN_API_KEY_LENGTH


def _settings(mode: str, keys: str) -> SimpleNamespace:
    return SimpleNamespace(
        integration_auth_mode=mode,
        integration_api_keys=keys,
    )


def test_authentication_is_disabled_by_default_compatibility(monkeypatch):
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings("disabled", ""),
    )

    assert integration_security.require_integration_auth(authorization=None) is None


def test_required_authentication_accepts_valid_bearer_key(monkeypatch):
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings("required", PRIMARY_KEY),
    )

    assert (
        integration_security.require_integration_auth(
            authorization=f"Bearer {PRIMARY_KEY}",
        )
        is None
    )


def test_required_authentication_supports_overlap_key_rotation(monkeypatch):
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings("required", f"{PRIMARY_KEY}, {ROTATION_KEY}"),
    )

    assert (
        integration_security.require_integration_auth(
            authorization=f"Bearer {ROTATION_KEY}",
        )
        is None
    )


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic abc", "Bearer wrong-key"],
)
def test_required_authentication_rejects_missing_or_invalid_credentials(
    monkeypatch,
    authorization,
):
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings("required", PRIMARY_KEY),
    )

    with pytest.raises(HTTPException) as exc_info:
        integration_security.require_integration_auth(authorization=authorization)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "DATA_ENGINE_INTEGRATION_AUTH_REQUIRED"
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize(
    ("mode", "keys"),
    [
        ("required", ""),
        ("required", "too-short"),
        ("unexpected", PRIMARY_KEY),
    ],
)
def test_required_authentication_fails_closed_on_invalid_configuration(
    monkeypatch,
    mode,
    keys,
):
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings(mode, keys),
    )

    with pytest.raises(HTTPException) as exc_info:
        integration_security.require_integration_auth(authorization=f"Bearer {PRIMARY_KEY}")

    assert exc_info.value.status_code == 503
    assert (
        exc_info.value.detail["code"]
        == "DATA_ENGINE_INTEGRATION_AUTH_CONFIGURATION_INVALID"
    )


def test_security_contract_never_exposes_configured_key_material(monkeypatch):
    monkeypatch.setattr(
        integration_security,
        "get_settings",
        lambda: _settings("required", f"{PRIMARY_KEY},{ROTATION_KEY}"),
    )

    contract = integration_security.integration_security_contract()

    assert contract["scheme"] == "BEARER_API_KEY"
    assert contract["auth_mode"] == "required"
    assert contract["multi_key_rotation"] is True
    assert PRIMARY_KEY not in str(contract)
    assert ROTATION_KEY not in str(contract)
