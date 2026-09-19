from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.cn.trademark_gazette_admission_api as api
from app.cn.trademark_gazette_admission import GazetteAdmissionError
from app.fact_admission_security import require_fact_admission_auth


class Receipt:
    def __init__(self, outcome: str):
        self.outcome = outcome

    def as_dict(self):
        return {"outcome": self.outcome}


def test_gazette_admission_router_is_admin_v2_fact_admission_only():
    paths = {route.path for route in api.router.routes}

    assert paths == {
        "/api/admin/v2/fact-admissions/cn/trademark-gazette/chunks",
        "/api/admin/v2/fact-admissions/cn/trademark-gazette/finalize",
    }
    assert all(not path.startswith("/api/v1") for path in paths)
    assert len(api.router.dependencies) == 1
    assert api.router.dependencies[0].dependency is require_fact_admission_auth


def test_chunk_api_returns_receipt(monkeypatch):
    client = object()
    monkeypatch.setattr(api, "clickhouse_client", lambda: client)
    monkeypatch.setattr(
        api,
        "admit_cn_trademark_gazette_chunk",
        lambda package, *, client: Receipt("CHUNK_ADMITTED"),
    )

    result = api.admit_cn_trademark_gazette_chunk_api({"contract_version": "fixture"})

    assert result == {"outcome": "CHUNK_ADMITTED"}


def test_finalize_api_returns_receipt(monkeypatch):
    client = object()
    monkeypatch.setattr(api, "clickhouse_client", lambda: client)
    monkeypatch.setattr(
        api,
        "finalize_cn_trademark_gazette_chunks",
        lambda package, *, client: Receipt("ADMITTED"),
    )

    result = api.finalize_cn_trademark_gazette_chunks_api(
        {"contract_version": "fixture"}
    )

    assert result == {"outcome": "ADMITTED"}


@pytest.mark.parametrize(
    "handler_name",
    [
        "admit_cn_trademark_gazette_chunk",
        "finalize_cn_trademark_gazette_chunks",
    ],
)
def test_validation_error_maps_to_non_retryable_400(monkeypatch, handler_name):
    monkeypatch.setattr(api, "clickhouse_client", lambda: object())

    def reject(package, *, client):
        raise GazetteAdmissionError("bad Gazette package")

    monkeypatch.setattr(api, handler_name, reject)

    call = (
        api.admit_cn_trademark_gazette_chunk_api
        if handler_name == "admit_cn_trademark_gazette_chunk"
        else api.finalize_cn_trademark_gazette_chunks_api
    )
    with pytest.raises(HTTPException) as error:
        call({"contract_version": "fixture"})

    assert error.value.status_code == 400
    assert error.value.detail == {
        "code": "DATA_ENGINE_FACT_ADMISSION_REJECTED",
        "message": "bad Gazette package",
        "retryable": False,
    }


@pytest.mark.parametrize(
    "handler_name",
    [
        "admit_cn_trademark_gazette_chunk",
        "finalize_cn_trademark_gazette_chunks",
    ],
)
def test_infrastructure_error_maps_to_retryable_503(monkeypatch, handler_name):
    monkeypatch.setattr(api, "clickhouse_client", lambda: object())

    def unavailable(package, *, client):
        raise RuntimeError("clickhouse unavailable")

    monkeypatch.setattr(api, handler_name, unavailable)

    call = (
        api.admit_cn_trademark_gazette_chunk_api
        if handler_name == "admit_cn_trademark_gazette_chunk"
        else api.finalize_cn_trademark_gazette_chunks_api
    )
    with pytest.raises(HTTPException) as error:
        call({"contract_version": "fixture"})

    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "DATA_ENGINE_FACT_ADMISSION_UNAVAILABLE",
        "message": "clickhouse unavailable",
        "retryable": True,
    }


def test_main_registers_gazette_admission_router():
    main_text = open("app/main.py", encoding="utf-8").read()

    assert (
        "from app.cn.trademark_gazette_admission_api import router as "
        "trademark_gazette_admission_router"
    ) in main_text
    assert (
        "_core.app.include_router(trademark_gazette_admission_router)" in main_text
    )
