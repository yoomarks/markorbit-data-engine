from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import app.cn.agent_name_lookup_acceptance as acceptance


SOURCE = acceptance.ProjectionStats(
    rows=59_204,
    distinct_agent_codes=59_204,
    max_source_rank=2_000_202_608_002_993,
    max_ingested_at="2026-09-17T03:24:29.148Z",
    binding_sum="123",
    binding_xor="456",
)


def test_projection_audit_requires_exact_source_lookup_binding(monkeypatch) -> None:
    monkeypatch.setattr(acceptance, "_source_stats", lambda _client: SOURCE)
    monkeypatch.setattr(acceptance, "_lookup_stats", lambda _client: SOURCE)

    result = acceptance.projection_audit(object())

    assert result["complete"] is True
    assert result["source"]["rows"] == 59_204
    assert result["lookup"]["rows"] == 59_204
    assert all(result["checks"].values())

    drifted = acceptance.ProjectionStats(
        **{**acceptance.asdict(SOURCE), "binding_xor": "999"}
    )
    monkeypatch.setattr(acceptance, "_lookup_stats", lambda _client: drifted)
    result = acceptance.projection_audit(object())
    assert result["complete"] is False
    assert result["checks"]["binding_xor_match"] is False


def test_benchmark_requires_seven_stable_bounded_reads(monkeypatch) -> None:
    times = iter(
        [
            0.000,
            0.050,
            1.000,
            1.052,
            2.000,
            2.051,
            3.000,
            3.053,
            4.000,
            4.054,
            5.000,
            5.055,
            6.000,
            6.056,
        ]
    )
    monkeypatch.setattr(
        acceptance,
        "agents_by_name",
        lambda _client, name: {
            "normalized_name": name,
            "candidate_count": 1,
            "match_count": 1,
            "matches": [{"agent_code": "A100"}],
            "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_NAME_FACTS_NO_IDENTITY_RESOLUTION",
        },
    )

    result = acceptance.benchmark_lookup(
        object(),
        "示例代理事务所",
        clock=lambda: next(times),
    )

    assert result["runs"] == 7
    assert result["stable_candidate_count"] is True
    assert result["stable_match_count"] is True
    assert result["p95_ms"] == 56.0
    assert result["passed"] is True

    with pytest.raises(ValueError, match="exactly 7"):
        acceptance.benchmark_lookup(object(), "示例代理事务所", runs=6)


def test_run_acceptance_is_read_only_and_redacts_http_key(
    tmp_path: Path, monkeypatch
) -> None:
    receipt_path = tmp_path / "receipt.json"
    secret = "x" * 40
    monkeypatch.setenv(acceptance.DEFAULT_API_KEY_ENV, secret)
    monkeypatch.setattr(
        acceptance,
        "projection_audit",
        lambda _client: {
            "source": acceptance.asdict(SOURCE),
            "lookup": acceptance.asdict(SOURCE),
            "checks": {"binding_sum_match": True},
            "complete": True,
        },
    )
    monkeypatch.setattr(
        acceptance,
        "ready_provenance",
        lambda _client: {
            "component": acceptance.READY_COMPONENT,
            "version": acceptance.AGENT_NAME_LOOKUP_READY_VERSION,
            "applied_at": "2026-09-17T19:22:25.375Z",
        },
    )
    monkeypatch.setattr(
        acceptance,
        "sample_name",
        lambda _client: {
            "normalized_name": "示例代理事务所",
            "agent_code": "A100",
        },
    )
    monkeypatch.setattr(
        acceptance,
        "benchmark_lookup",
        lambda _client, name: {
            "runs": 7,
            "normalized_name": name,
            "p50_ms": 50.0,
            "p95_ms": 56.0,
            "max_ms": 56.0,
            "slo_p95_ms": 300.0,
            "candidate_count": 1,
            "match_count": 1,
            "stable_candidate_count": True,
            "stable_match_count": True,
            "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_NAME_FACTS_NO_IDENTITY_RESOLUTION",
            "passed": True,
        },
    )
    monkeypatch.setattr(
        acceptance,
        "exact_agent_currentness",
        lambda _client, code, name: {
            "agent_code": code,
            "normalized_name": name,
            "source_reference": {
                "owner": "DATA_ENGINE",
                "kind": "CN_AGENT",
                "id": code,
                "version": str(SOURCE.max_source_rank),
                "fingerprintSha256": "a" * 64,
                "observedAt": SOURCE.max_ingested_at,
            },
            "currentness": {
                "state": "CURRENT_SOURCE_FACT",
                "source_rank": SOURCE.max_source_rank,
                "observed_at": SOURCE.max_ingested_at,
                "is_deleted": False,
            },
            "legal_identity_verified": False,
            "customer_relationship_established": False,
            "professional_appointment_established": False,
            "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_CODE_FACT_NO_IDENTITY_RESOLUTION",
        },
    )

    def fake_http(base_url: str, name: str, *, api_key: str | None):
        assert base_url == "http://127.0.0.1:8080"
        assert name == "示例代理事务所"
        assert api_key == secret
        return {
            "status_code": 200,
            "resource_kind": "AGENT_NAME_FACT_MATCHES",
            "fact_state": "observed",
            "normalized_name": name,
            "candidate_count": 1,
            "match_count": 1,
            "semantics": "CURRENT_OFFICIAL_CNIPA_AGENT_NAME_FACTS_NO_IDENTITY_RESOLUTION",
            "auth_header_sent": True,
        }

    result = acceptance.run_acceptance(
        receipt_path=receipt_path,
        client=object(),
        main_sha_getter=lambda: "b" * 40,
        http_runner=fake_http,
    )

    assert result["status"] == "SUCCESS"
    assert result["mutation_performed"] is False
    assert result["receipt_path"] == str(receipt_path)
    assert result["receipt_sha256"] == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    persisted = receipt_path.read_text(encoding="utf-8")
    assert secret not in persisted
    assert json.loads(persisted)["http_smoke"]["auth_header_sent"] is True


def test_run_acceptance_stops_before_benchmark_on_incomplete_projection(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        acceptance,
        "projection_audit",
        lambda _client: {
            "source": {},
            "lookup": {},
            "checks": {"row_count_match": False},
            "complete": False,
        },
    )
    benchmark_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal benchmark_called
        benchmark_called = True
        raise AssertionError("benchmark must not run after failed completeness")

    monkeypatch.setattr(acceptance, "benchmark_lookup", fail_if_called)

    with pytest.raises(RuntimeError, match="completeness verification failed"):
        acceptance.run_acceptance(
            receipt_path=tmp_path / "receipt.json",
            client=object(),
            main_sha_getter=lambda: "c" * 40,
        )

    assert benchmark_called is False


def test_acceptance_module_contains_no_mutation_surface() -> None:
    source = Path("app/cn/agent_name_lookup_acceptance.py").read_text(encoding="utf-8")
    forbidden = (
        ".command(",
        "INSERT INTO",
        "CREATE TABLE",
        "CREATE MATERIALIZED VIEW",
        "ALTER TABLE",
        "DROP TABLE",
        "TRUNCATE TABLE",
        "OPTIMIZE TABLE",
    )
    assert all(token not in source for token in forbidden)
