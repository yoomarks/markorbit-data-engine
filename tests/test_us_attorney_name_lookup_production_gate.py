from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.us.attorney_name_lookup_activation as gate
from app.us.applicant_candidate_backfill_control import USApplicantServingEpoch


MAIN = "a" * 40
SOURCE = gate.SourceStats(
    active_rows=100,
    qualifying_rows=60,
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
                "distinct_names": 0,
                "max_source_rank": 0,
                "binding_sum": "0",
                "binding_xor": "0",
            },
            "ready_marker": None,
        },
        "capacity_contract": {
            "hot_us": {"name": "hot_us", "free_bytes": 1000, "total_bytes": 2000},
            "source_table_bytes": 100,
            "estimated_lookup_bytes_ceiling": 200,
            "minimum_free_ratio_after_estimate": 0.30,
            "projected_free_bytes": 800,
        },
        "target_schema": {
            "storage_policy": gate.TARGET_STORAGE_POLICY,
            "table_ddl_sha256": gate.hashlib.sha256(
                gate.target_table_ddl().encode("utf-8")
            ).hexdigest(),
            "mv_ddl_sha256": gate.hashlib.sha256(
                gate.target_mv_ddl().encode("utf-8")
            ).hexdigest(),
        },
        "acceptance": {
            "benchmark_runs": gate.BENCHMARK_RUNS,
            "entity_name_resolve_p95_ms": gate.SLO_P95_MS,
            "ready_version": gate.ATTORNEY_NAME_LOOKUP_READY_VERSION,
        },
        "mutation_scope": {
            "create_table": gate.US_ATTORNEY_NAME_LOOKUP_TABLE,
            "create_mv": f"markorbit_facts.{gate.MV_NAME}",
            "backfill_source": gate.SOURCE_TABLE,
            "final_marker_component": gate.READY_COMPONENT,
            "operation": "CREATE_INSERT_ONLY",
        },
    }


def _write_plan(path: Path) -> tuple[str, dict[str, object]]:
    plan = _plan()
    sha = gate._sha256(plan)
    path.write_text(
        json.dumps({"plan": plan, "plan_sha256": sha}),
        encoding="utf-8",
    )
    return sha, plan


def test_target_schema_is_hot_us_and_future_write_projected() -> None:
    table = gate.target_table_ddl()
    mv = gate.target_mv_ddl()
    assert "storage_policy = 'hot_us_only'" in table
    assert "ReplacingMergeTree(source_rank, is_deleted)" in table
    assert "ORDER BY (normalized_name, serial_number, correspondent_key)" in table
    assert f"TO {gate.US_ATTORNEY_NAME_LOOKUP_TABLE}" in mv
    assert f"FROM {gate.SOURCE_TABLE}" in mv
    assert "WHERE attorney_name != ''" in mv


def test_load_plan_rejects_mutation_scope_drift(tmp_path: Path) -> None:
    plan = _plan()
    plan["mutation_scope"] = {
        **dict(plan["mutation_scope"]),
        "operation": "ALTER_ALLOWED",
    }
    sha = gate._sha256(plan)
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"plan": plan, "plan_sha256": sha}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="mutation scope drifted"):
        gate.load_plan(path, sha)


def test_execute_requires_exact_authority_before_target_mutation(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    sha, _ = _write_plan(path)
    with pytest.raises(PermissionError, match="exact #767 authority"):
        gate.execute_plan(
            path,
            plan_sha=sha,
            authority="GO WRONG",
            receipt_path=tmp_path / "receipt.json",
            client=object(),
        )


class FakeTarget:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, sql: str) -> str:
        self.commands.append(sql)
        return ""


def _success_patches(monkeypatch, target: FakeTarget) -> None:
    monkeypatch.setattr(gate, "_assert_plan_live", lambda *args, **kwargs: SOURCE)
    monkeypatch.setattr(gate, "_assert_source_unchanged", lambda *args, **kwargs: SOURCE)
    monkeypatch.setattr(
        gate,
        "verify_completeness",
        lambda *args, **kwargs: {"complete": True, "sample": {"mismatches": 0}},
    )
    monkeypatch.setattr(
        gate,
        "benchmark_lookup",
        lambda *args, **kwargs: {
            "passed": True,
            "normalized_name": "jane q. attorney",
            "p95_ms": 12.0,
        },
    )
    monkeypatch.setattr(gate, "accepted_us_target_read_client", lambda: object())
    monkeypatch.setattr(
        gate,
        "attorneys_by_name",
        lambda *args, **kwargs: {
            "normalized_name": "jane q. attorney",
            "candidate_count": 1,
            "match_count": 1,
            "semantics": "CURRENT_OFFICIAL_USPTO_ATTORNEY_NAME_FACTS_NO_IDENTITY_RESOLUTION",
        },
    )
    monkeypatch.setattr(
        gate,
        "ready_marker",
        lambda _client: (
            gate.ATTORNEY_NAME_LOOKUP_READY_VERSION
            if any("schema_version" in sql for sql in target.commands)
            else None
        ),
    )


def test_execute_fresh_path_orders_schema_backfill_benchmark_ready(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha, _ = _write_plan(path)
    target = FakeTarget()
    _success_patches(monkeypatch, target)

    def fake_lookup(_client):
        created = any(
            sql.startswith("CREATE TABLE") for sql in target.commands
        )
        return {
            "exists": created,
            "visible_rows": 0,
            "distinct_names": 0,
            "max_source_rank": 0,
            "binding_sum": "0",
            "binding_xor": "0",
        }

    monkeypatch.setattr(gate, "lookup_stats", fake_lookup)
    monkeypatch.setattr(
        gate,
        "_mv_exists",
        lambda _client: any(
            sql.startswith("CREATE MATERIALIZED VIEW") for sql in target.commands
        ),
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
    assert target.commands[0].startswith("CREATE TABLE")
    assert target.commands[1].startswith("CREATE MATERIALIZED VIEW")
    assert target.commands[2].startswith(
        f"INSERT INTO {gate.US_ATTORNEY_NAME_LOOKUP_TABLE}"
    )
    assert target.commands[3].startswith(
        "INSERT INTO markorbit_facts.schema_version"
    )


def test_execute_refuses_partial_visible_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "plan.json"
    sha, _ = _write_plan(path)
    target = FakeTarget()
    monkeypatch.setattr(gate, "_assert_plan_live", lambda *args, **kwargs: SOURCE)
    monkeypatch.setattr(gate, "_assert_source_unchanged", lambda *args, **kwargs: SOURCE)
    monkeypatch.setattr(gate, "ready_marker", lambda _client: None)
    monkeypatch.setattr(
        gate,
        "lookup_stats",
        lambda _client: {
            "exists": True,
            "visible_rows": 7,
            "distinct_names": 7,
            "max_source_rank": 100,
            "binding_sum": "1",
            "binding_xor": "2",
        },
    )
    monkeypatch.setattr(gate, "_mv_exists", lambda _client: True)

    with pytest.raises(RuntimeError, match="partially populated"):
        gate.execute_plan(
            path,
            plan_sha=sha,
            authority=gate.authority_token(sha),
            receipt_path=tmp_path / "receipt.json",
            client=target,
        )
    assert target.commands == []


def test_execute_complete_not_ready_skips_backfill(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "plan.json"
    sha, _ = _write_plan(path)
    target = FakeTarget()
    _success_patches(monkeypatch, target)
    monkeypatch.setattr(
        gate,
        "lookup_stats",
        lambda _client: {
            "exists": True,
            "visible_rows": SOURCE.qualifying_rows,
            "distinct_names": 25,
            "max_source_rank": SOURCE.max_source_rank,
            "binding_sum": SOURCE.binding_sum,
            "binding_xor": SOURCE.binding_xor,
        },
    )
    monkeypatch.setattr(gate, "_mv_exists", lambda _client: True)

    receipt = gate.execute_plan(
        path,
        plan_sha=sha,
        authority=gate.authority_token(sha),
        receipt_path=tmp_path / "receipt.json",
        client=target,
    )
    assert receipt["status"] == "SUCCESS"
    assert len(target.commands) == 1
    assert target.commands[0].startswith(
        "INSERT INTO markorbit_facts.schema_version"
    )

