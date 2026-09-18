from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.fact_admission_security as security


def test_fact_admission_auth_requires_dedicated_key(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(fact_admission_api_keys="x" * 32),
    )
    security.require_fact_admission_auth("Bearer " + "x" * 32)

    with pytest.raises(HTTPException) as error:
        security.require_fact_admission_auth("Bearer wrong")
    assert error.value.status_code == 401


def test_fact_admission_auth_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(fact_admission_api_keys=""),
    )
    with pytest.raises(HTTPException) as error:
        security.require_fact_admission_auth(None)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "DATA_ENGINE_FACT_ADMISSION_AUTH_NOT_CONFIGURED"
