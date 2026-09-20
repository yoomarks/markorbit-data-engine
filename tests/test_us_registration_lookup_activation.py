from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.us.registration_lookup_activation as gate
from app.us.applicant_candidate_backfill_control import USApplicantServingEpoch

MAIN = "a" * 40
SOURCE = gate.SourceStats(
    active_rows=100,
    qualifying_rows=80,
    max_source_rank=123,
    binding_sum="456",
    binding_xor="789",
)


def _epoch() -> USApplicantServingEpoch:
    return USApplicantServingEpoch(
        bulk_run_id="run-1",
        plan_sha256="b" * 64,
        checkpoint_sequence=7,
        final_audit_version="v1",
    )
def _plan() -> dict[str, object]:
    return {
        "version": gate.PLAN_VERSION,
        "expected_main": MAIN,
        "implementation_sha": MAIN,
        "source_epoch": _epoch().to_dict(),
        "source_stats": SOURCE.to_dict(),
        "target_precondition": {
            "lookup": {
                "exists": False,
                "visible_rows": 0,
                "distinct_registrations": 0,
                "max_source_rank": 0,
                "binding_sum": "0",
                "binding_xor": "0",
            },
            "ready_marker": None,
        },
        "capacity_contract": {
            "hot_us_free_bytes": 1000,
            "hot_us_total_bytes": 2000,
            "source_table_bytes": 100,
            "estimated_lookup_bytes_ceiling": 200,
            "projected_free_bytes": 800,
            "minimum_free_ratio_after_estimate": 0.30,
        },
        "target_schema": {
            "storage_policy": gate.TARGET_STORAGE_POLICY,
            "table_ddl_sha256": gate.hashlib.sha256(
                gate.target_table_ddl().encode("utf-8")
            ).hexdigest(),
        },
        "acceptance": {
            "benchmark_runs": gate.BENCHMARK_RUNS,
            "exact_registration_p95_ms": gate.SLO_P95_MS,
            "ready_version": gate.REGISTRATION_LOOKUP_READY_VERSION,
        },
        "mutation_scope": {
            "create_table": gate.US_REGISTRATION_LOOKUP_TABLE,
            "backfill_source": gate.SOURCE_TABLE,
            "final_marker_component": gate.READY_COMPONENT,
            "operation": "CREATE_INSERT_ONLY",
        },
    }


def _write_plan(path: Path) -> str:
    plan = _plan()
    sha = gate._sha256(plan)
    path.write_text(
        json.dumps({"plan": plan, "plan_sha256": sha}),
        encoding="utf-8",
    )
    return sha
class FakeTarget:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, sql: str) -> str:
        self.commands.append(sql)
        return ""


def test_target_schema_is_hot_us_and_insert_only() -> None:
    ddl = gate.target_table_ddl()
    assert "storage_policy = 'hot_us_only'" in ddl
    assert "ReplacingMergeTree(source_rank)" in ddl
    assert "ORDER BY (registration_number, serial_number)" in ddl
    assert "MATERIALIZED VIEW" not in ddl
    assert "ALTER " not in ddl
    assert "DROP " not in ddl
    assert "DELETE " not in ddl


def test_execute_requires_exact_authority_before_mutation(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    with pytest.raises(PermissionError, match="exact #765 authority"):
        gate.execute_plan(
            path,
            plan_sha=sha,
            authority="GO WRONG",
            receipt_path=tmp_path / "receipt.json",
            client=target,
        )
    assert target.commands == []
def _patch_success(monkeypatch, target: FakeTarget) -> None:
    monkeypatch.setattr(
        gate,
        "_assert_plan_live",
        lambda *args, **kwargs: SOURCE,
    )
    monkeypatch.setattr(
        gate,
        "verify_completeness",
        lambda *args, **kwargs: {
            "complete": True,
            "sample": {"checked": 200, "mismatches": 0},
        },
    )
    monkeypatch.setattr(
        gate,
        "benchmark_lookup",
        lambda *args, **kwargs: {
            "passed": True,
            "registration_number": "7265548",
            "p95_ms": 12.0,
        },
    )
    monkeypatch.setattr(gate, "accepted_us_target_read_client", lambda: object())
    monkeypatch.setattr(
        gate,
        "lookup_registration",
        lambda *args, **kwargs: {
            "registration_number": "7265548",
            "candidate_count": 1,
            "match_count": 1,
            "semantics": "CURRENT_USPTO_CASE_FACTS_NOT_LEGAL_STATUS_CONCLUSION",
        },
    )
    monkeypatch.setattr(
        gate,
        "ready_marker",
        lambda _client: (
            gate.REGISTRATION_LOOKUP_READY_VERSION
            if any(gate.READY_COMPONENT in sql for sql in target.commands)
            else None
        ),
    )


def test_partial_lookup_refuses_automatic_resume(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    monkeypatch.setattr(
        gate,
        "_assert_plan_live",
        lambda *args, **kwargs: SOURCE,
    )
    monkeypatch.setattr(gate, "ready_marker", lambda _client: None)
    monkeypatch.setattr(
        gate,
        "lookup_stats",
        lambda _client: {
            "exists": True,
            "visible_rows": 10,
            "distinct_registrations": 10,
            "max_source_rank": 100,
            "binding_sum": "x",
            "binding_xor": "y",
        },
    )
    with pytest.raises(RuntimeError, match="partially populated"):
        gate.execute_plan(
            path,
            plan_sha=sha,
            authority=gate.authority_token(sha),
            receipt_path=tmp_path / "receipt.json",
            client=target,
        )
    assert target.commands == []


def test_complete_not_ready_skips_backfill_and_writes_ready_last(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    _patch_success(monkeypatch, target)
    monkeypatch.setattr(
        gate,
        "lookup_stats",
        lambda _client: {
            "exists": True,
            "visible_rows": SOURCE.qualifying_rows,
            "distinct_registrations": 77,
            "max_source_rank": SOURCE.max_source_rank,
            "binding_sum": SOURCE.binding_sum,
            "binding_xor": SOURCE.binding_xor,
        },
    )
    receipt = gate.execute_plan(
        path,
        plan_sha=sha,
        authority=gate.authority_token(sha),
        receipt_path=tmp_path / "receipt.json",
        client=target,
    )
    assert receipt["status"] == "SUCCESS"
    assert receipt["replayed"] is False
    assert all("us_registration_candidate_lookup)" not in sql for sql in target.commands)
    assert len(target.commands) == 1
    assert gate.READY_COMPONENT in target.commands[0]


def test_fresh_path_creates_backfills_then_ready(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    _patch_success(monkeypatch, target)

    def fake_lookup(_client):
        created = any(sql.startswith("CREATE TABLE") for sql in target.commands)
        return {
            "exists": created,
            "visible_rows": 0,
            "distinct_registrations": 0,
            "max_source_rank": 0,
            "binding_sum": "0",
            "binding_xor": "0",
        }

    monkeypatch.setattr(gate, "lookup_stats", fake_lookup)
    receipt = gate.execute_plan(
        path,
        plan_sha=sha,
        authority=gate.authority_token(sha),
        receipt_path=tmp_path / "receipt.json",
        client=target,
    )
    assert receipt["status"] == "SUCCESS"
    assert target.commands[0].startswith("CREATE TABLE")
    assert target.commands[1].startswith(
        f"INSERT INTO {gate.US_REGISTRATION_LOOKUP_TABLE}"
    )
    assert gate.READY_COMPONENT in target.commands[-1]
