from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.integration_api as integration_api
import app.integration_security as integration_security


API_KEY = "control-plane-test-key-000000000000000000000000"
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}
CONTROL_PATH = "/api/v1/data-engine/control-plane"


def _client(monkeypatch) -> TestClient:
    settings = SimpleNamespace(
        integration_auth_mode="required",
        integration_api_keys=API_KEY,
    )
    monkeypatch.setattr(integration_security, "get_settings", lambda: settings)
    app = FastAPI()
    app.dependency_overrides[integration_api.enforce_integration_rate_limit] = lambda: None
    app.include_router(integration_api.router)
    return TestClient(app)


def _install_owner_snapshots(
    monkeypatch,
    *,
    health: dict | None = None,
    summary_overrides: dict[str, int] | None = None,
) -> None:
    summary = {
        "active_human_actions": 0,
        "failed_human_actions": 0,
        "active_admin_tasks": 0,
        "admin_domains_with_errors": 0,
        "failed_operational_jobs": 0,
        "domain_runs_with_readiness_failures": 0,
        "replay_lanes_with_readiness_failures": 0,
        "FORBIDDEN_INTERNAL_COUNTER": 999,
    }
    summary.update(summary_overrides or {})
    monkeypatch.setattr(
        integration_api,
        "health",
        lambda: health
        or {
            "api": "ok",
            "postgres": "ok",
            "clickhouse": "ok",
            "owner_components": {"raw_error": "LEAK_HEALTH_DETAIL"},
        },
    )
    monkeypatch.setattr(
        integration_api,
        "operations_snapshot",
        lambda: {
            "version": "MARKORBIT_OPERATIONS_V2",
            "action_authority": "MARKORBIT_ADMIN_ACTIONS_V1",
            "summary": summary,
            "actions": [
                {
                    "package_id": "LEAK_PACKAGE_ID",
                    "file_name": "apc-secret.zip",
                    "error": "LEAK_OPERATION_ERROR",
                    "absolute_path": r"D:\secret\raw.zip",
                }
            ],
            "replay": {"eta_seconds": 123, "secret": "LEAK_REPLAY_DETAIL"},
        },
    )
    monkeypatch.setattr(
        integration_api,
        "domain_progress_snapshot",
        lambda: {
            "version": "MARKORBIT_ADMIN_PROGRESS_V2",
            "active_count": 0,
            "items": [
                {
                    "package_id": "LEAK_ADMIN_PACKAGE",
                    "current_subtask": "LEAK_SUBTASK",
                    "current_group": {"eta_seconds": 321},
                    "last_error": "LEAK_ADMIN_ERROR",
                }
            ],
        },
    )


def test_control_plane_authenticated_success_is_explicitly_bounded(monkeypatch) -> None:
    _install_owner_snapshots(monkeypatch)
    response = _client(monkeypatch).get(CONTROL_PATH, headers=AUTH_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["authority"] == "DATA_ENGINE_FACT_READ_MODEL"
    assert payload["read_only"] is True
    assert payload["health"] == "ok"
    assert payload["operations"] == {
        "version": "MARKORBIT_OPERATIONS_V2",
        "action_authority": "MARKORBIT_ADMIN_ACTIONS_V1",
        "summary": {
            "active_human_actions": 0,
            "failed_human_actions": 0,
            "active_admin_tasks": 0,
            "admin_domains_with_errors": 0,
            "failed_operational_jobs": 0,
            "domain_runs_with_readiness_failures": 0,
            "replay_lanes_with_readiness_failures": 0,
        },
    }
    assert payload["admin_progress"] == {
        "version": "MARKORBIT_ADMIN_PROGRESS_V2",
        "active_count": 0,
    }
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "LEAK_HEALTH_DETAIL",
        "LEAK_PACKAGE_ID",
        "apc-secret.zip",
        "LEAK_OPERATION_ERROR",
        r"D:\\secret\\raw.zip",
        "LEAK_REPLAY_DETAIL",
        "LEAK_ADMIN_PACKAGE",
        "LEAK_SUBTASK",
        "LEAK_ADMIN_ERROR",
        "FORBIDDEN_INTERNAL_COUNTER",
        "eta_seconds",
    ):
        assert forbidden not in serialized


def test_control_plane_requires_existing_integration_auth(monkeypatch) -> None:
    _install_owner_snapshots(monkeypatch)
    response = _client(monkeypatch).get(CONTROL_PATH)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "DATA_ENGINE_INTEGRATION_AUTH_REQUIRED"


def test_control_plane_active_work_alone_does_not_degrade(monkeypatch) -> None:
    _install_owner_snapshots(
        monkeypatch,
        summary_overrides={"active_human_actions": 2, "active_admin_tasks": 1},
    )
    response = _client(monkeypatch).get(CONTROL_PATH, headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["health"] == "ok"


def test_control_plane_owner_health_degradation_is_coarsened(monkeypatch) -> None:
    _install_owner_snapshots(
        monkeypatch,
        health={"api": "ok", "postgres": "error: LEAK_DB_ERROR", "clickhouse": "ok"},
    )
    response = _client(monkeypatch).get(CONTROL_PATH, headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["health"] == "degraded"
    assert "LEAK_DB_ERROR" not in response.text


def test_control_plane_failure_counter_degrades(monkeypatch) -> None:
    _install_owner_snapshots(
        monkeypatch,
        summary_overrides={"domain_runs_with_readiness_failures": 1},
    )
    response = _client(monkeypatch).get(CONTROL_PATH, headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["health"] == "degraded"


def test_control_plane_owner_exception_returns_coarse_503(monkeypatch) -> None:
    _install_owner_snapshots(monkeypatch)

    def fail_owner() -> dict:
        raise RuntimeError("LEAK_OWNER_SECRET_PATH_D:/raw")

    monkeypatch.setattr(integration_api, "operations_snapshot", fail_owner)
    response = _client(monkeypatch).get(CONTROL_PATH, headers=AUTH_HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Data Engine owner summary is unavailable"}
    assert "LEAK_OWNER_SECRET_PATH" not in response.text


def test_control_plane_malformed_owner_payload_returns_coarse_503(monkeypatch) -> None:
    _install_owner_snapshots(monkeypatch)
    monkeypatch.setattr(
        integration_api,
        "domain_progress_snapshot",
        lambda: {"version": "MARKORBIT_ADMIN_PROGRESS_V2", "active_count": -1},
    )
    response = _client(monkeypatch).get(CONTROL_PATH, headers=AUTH_HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Data Engine owner summary is unavailable"}


def test_control_plane_route_is_get_only(monkeypatch) -> None:
    _install_owner_snapshots(monkeypatch)
    client = _client(monkeypatch)
    for method in ("post", "put", "delete"):
        response = getattr(client, method)(CONTROL_PATH, headers=AUTH_HEADERS)
        assert response.status_code == 405


def test_integration_health_existing_endpoint_still_works(monkeypatch) -> None:
    _install_owner_snapshots(monkeypatch)
    response = _client(monkeypatch).get("/api/v1/health", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_integration_layer_has_no_direct_storage_client_for_control_plane() -> None:
    text = Path("app/integration_api.py").read_text(encoding="utf-8")
    for forbidden in (
        "from app.db import",
        "postgres_conn",
        "clickhouse_client",
        "raw_data_root",
    ):
        assert forbidden not in text
    for owner_function in (
        "operations_snapshot()",
        "domain_progress_snapshot()",
        "health()",
    ):
        assert owner_function in text
