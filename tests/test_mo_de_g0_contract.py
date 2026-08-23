from __future__ import annotations

import json
from pathlib import Path

from app.integration_contract import CONTRACT_VERSION
from app.integration_g0_contract import CORRELATION_ID_HEADER, g0_contract_descriptor
from app.integration_runtime import _error_payload
from app.integration_transport import response_headers


def test_mo_de_001_machine_contract_matches_runtime_descriptor() -> None:
    artifact = json.loads(
        Path("docs/integrations/markorbit/MARKORBIT_DATA_ENGINE_INTEGRATION_V1.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact == g0_contract_descriptor()
    assert artifact["contract_id"] == CONTRACT_VERSION
    paths = {resource["path"] for resource in artifact["query_contract"]["resources"]}
    assert "/api/v1/contract" in paths
    assert "/api/v1/us/changes" in paths


def test_mo_de_002_absence_and_unavailability_are_distinct() -> None:
    contract = g0_contract_descriptor()["fact_semantics"]
    assert contract["not_found"] != contract["service_unavailable"]
    assert {"not_covered", "no_observation", "tombstone"}.issubset(contract)
    assert _error_payload(404, "missing")["fact_state"] == "not_found"
    assert _error_payload(503, "down")["fact_state"] == "service_unavailable"


def test_mo_de_003_security_freeze_targets_required_auth_for_g1() -> None:
    security = g0_contract_descriptor()["security"]
    assert security["scheme"] == "BEARER_API_KEY"
    assert security["g1_target_mode"] == "required"
    assert security["minimum_key_length"] == 32
    assert security["environment_isolation"] is True


def test_mo_de_004_response_echoes_request_and_correlation_ids() -> None:
    headers = response_headers("/api/v1/health", "req-1", "corr-1")
    assert headers["X-Request-ID"] == "req-1"
    assert headers[CORRELATION_ID_HEADER] == "corr-1"
    assert headers["X-MarkOrbit-Contract-Version"] == CONTRACT_VERSION


def test_mo_de_005_error_and_retry_contract_is_stable() -> None:
    contract = g0_contract_descriptor()["runtime_errors"]
    assert contract["status_codes"]["429"]["retryable"] is True
    assert contract["status_codes"]["503"]["retryable"] is True
    assert _error_payload(400, "bad")["retryable"] is False
    assert _error_payload(
        429,
        {"code": "RATE", "message": "slow", "retryable": True},
    ) == {"code": "RATE", "message": "slow", "retryable": True}
