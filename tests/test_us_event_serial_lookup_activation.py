from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.us.event_serial_lookup_activation as gate
from app.us.applicant_candidate_backfill_control import USApplicantServingEpoch

MAIN = "a" * 40
SOURCE = gate.SourceStats(
    active_rows=5,
    qualifying_rows=5,
    max_source_rank=123,
    binding_sum="456",
    binding_xor="789",
)
BATCHES = [
    gate.BatchStats(f"{i:02d}", 3 if i == 0 else 2 if i == 1 else 0, 123 if i < 2 else 0,
                    "10" if i == 0 else "20" if i == 1 else "0",
                    "11" if i == 0 else "21" if i == 1 else "0")
    for i in range(100)
]
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
        "source_batches": [batch.to_dict() for batch in BATCHES],
        "target_precondition": {
            "lookup": {
                "exists": False,
                "visible_rows": 0,
                "distinct_serials": 0,
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
            "estimated_lookup_bytes_ceiling": 150,
            "projected_free_bytes": 850,
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
            "serial_event_p95_ms": gate.SLO_P95_MS,
            "ready_version": gate.EVENT_SERIAL_LOOKUP_READY_VERSION,
        },
        "mutation_scope": {
            "create_table": gate.US_EVENT_SERIAL_LOOKUP_TABLE,
            "backfill_source": gate.SOURCE_TABLE,
            "batch_key": "left(serial_number,2)",
            "batch_count": 100,
            "final_marker_component": gate.READY_COMPONENT,
            "operation": "CREATE_INSERT_BATCHED_PREFIX_ONLY",
        },
    }
def _write_plan(path: Path, *, completed_prefixes: list[str] | None = None) -> str:
    plan = _plan()
    if completed_prefixes is not None:
        plan["target_precondition"]["completed_prefixes"] = completed_prefixes
    sha = gate._sha256(plan)
    path.write_text(
        json.dumps({"plan": plan, "plan_sha256": sha}),
        encoding="utf-8",
    )
    return sha


class FakeTarget:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.inserted: set[str] = set()
        self.schema_created = False
        self.ready = False

    def command(self, sql: str) -> str:
        self.commands.append(sql)
        if sql.startswith("CREATE TABLE"):
            self.schema_created = True
        for batch in BATCHES:
            needle = f"startsWith(serial_number,'{batch.prefix}')"
            if sql.startswith(f"INSERT INTO {gate.US_EVENT_SERIAL_LOOKUP_TABLE}") and needle in sql:
                self.inserted.add(batch.prefix)
        if gate.READY_COMPONENT in sql:
            self.ready = True
        return ""
def _patch_common(monkeypatch, target: FakeTarget) -> None:
    monkeypatch.setattr(gate, "_assert_plan_live", lambda *args, **kwargs: SOURCE)
    monkeypatch.setattr(gate, "_assert_remaining_capacity", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gate,
        "_validate_lookup_schema",
        lambda _client: target.schema_created,
    )
    monkeypatch.setattr(
        gate,
        "ready_marker",
        lambda _client: gate.EVENT_SERIAL_LOOKUP_READY_VERSION if target.ready else None,
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
            "serial_number": "00123456",
            "event_count": 3,
            "p95_ms": 12.0,
        },
    )
    monkeypatch.setattr(gate, "accepted_us_target_read_client", lambda: object())
    monkeypatch.setattr(
        gate,
        "events_for_serial",
        lambda *args, **kwargs: {
            "serial_number": "00123456",
            "event_count": 3,
            "events": [{}, {}, {}],
            "semantics": "OFFICIAL_USPTO_EVENT_FACTS_NOT_LEGAL_STATUS_CONCLUSION",
        },
    )


def _batch_observer(target: FakeTarget, *, mismatch: str | None = None):
    def observe(_client, prefix: str):
        expected = BATCHES[int(prefix)]
        if prefix == mismatch:
            return gate.BatchStats(prefix, 1, 99, "bad", "bad")
        if expected.rows == 0:
            return expected
        if prefix in target.inserted:
            return expected
        return gate.BatchStats(prefix, 0, 0, "0", "0")
    return observe


def test_target_schema_is_hot_us_and_serial_ordered() -> None:
    ddl = gate.target_table_ddl()
    assert "storage_policy = 'hot_us_only'" in ddl
    assert "ORDER BY (serial_number, event_key)" in ddl
    assert "event_key FixedString(64)" in ddl
    assert "description_text String" in ddl
    assert "MATERIALIZED VIEW" not in ddl
    assert "ALTER " not in ddl
    assert "DROP " not in ddl
def test_plan_requires_one_hundred_ordered_prefixes(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    loaded = gate.load_plan(path, sha)
    assert len(loaded["source_batches"]) == 100
    assert loaded["source_batches"][0]["prefix"] == "00"
    assert loaded["source_batches"][-1]["prefix"] == "99"


def test_backfill_sql_is_single_threaded() -> None:
    sql = gate._backfill_sql("60")
    assert "SETTINGS max_threads = 1, max_insert_threads = 1" in sql


def test_capacity_contract_scales_recovery_reserve_to_remaining_rows(monkeypatch) -> None:
    def fake_rows(_client, sql: str):
        if "system.disks" in sql:
            return [[1000, 2000]]
        if "system.tables" in sql:
            return [[100, gate.TARGET_STORAGE_POLICY]]
        raise AssertionError(sql)

    monkeypatch.setattr(gate, "_rows", fake_rows)
    contract = gate._capacity_contract(object(), remaining_rows=2, total_rows=5)

    assert contract["estimated_lookup_bytes_ceiling"] == 150
    assert contract["remaining_lookup_bytes_ceiling"] == 60
    assert contract["projected_free_bytes"] == 940


def test_remaining_capacity_waits_for_reclaimable_inactive_parts(monkeypatch) -> None:
    free_states = iter([(100, 200), (130, 200)])
    sleeps: list[float] = []
    monkeypatch.setattr(gate, "_live_free", lambda _client: next(free_states))
    monkeypatch.setattr(gate, "_inactive_lookup_bytes", lambda _client: (4, 40))
    monkeypatch.setattr(gate.time, "sleep", lambda seconds: sleeps.append(seconds))

    gate._assert_remaining_capacity(
        object(),
        plan_capacity={
            "hot_us_total_bytes": 200,
            "estimated_lookup_bytes_ceiling": 60,
        },
        remaining_rows=100,
        total_rows=100,
    )

    assert sleeps == [gate.CAPACITY_RECLAIM_POLL_SECONDS]


def test_remaining_capacity_refuses_when_inactive_parts_cannot_cover_shortfall(
    monkeypatch,
) -> None:
    monkeypatch.setattr(gate, "_live_free", lambda _client: (100, 200))
    monkeypatch.setattr(gate, "_inactive_lookup_bytes", lambda _client: (2, 10))

    with pytest.raises(RuntimeError, match="projected reserve fell below 30%"):
        gate._assert_remaining_capacity(
            object(),
            plan_capacity={
                "hot_us_total_bytes": 200,
                "estimated_lookup_bytes_ceiling": 60,
            },
            remaining_rows=100,
            total_rows=100,
        )


def test_prepare_accepts_exact_completed_prefix_for_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    target = FakeTarget()
    target.schema_created = True
    target.inserted.add("00")
    monkeypatch.setattr(gate, "source_stats", lambda _client: SOURCE)
    monkeypatch.setattr(gate, "source_batches", lambda _client: BATCHES)
    monkeypatch.setattr(
        gate,
        "lookup_stats",
        lambda _client: {
            "exists": True,
            "visible_rows": 3,
            "distinct_serials": 1,
            "max_source_rank": 123,
            "binding_sum": "10",
            "binding_xor": "11",
        },
    )
    monkeypatch.setattr(gate, "ready_marker", lambda _client: None)
    monkeypatch.setattr(
        gate,
        "_capacity_contract",
        lambda _client, **_kwargs: {
            "hot_us_free_bytes": 1000,
            "hot_us_total_bytes": 2000,
            "source_table_bytes": 100,
            "estimated_lookup_bytes_ceiling": 150,
            "projected_free_bytes": 850,
            "minimum_free_ratio_after_estimate": 0.30,
        },
    )
    monkeypatch.setattr(gate, "_batch_stats", _batch_observer(target))

    envelope = gate.prepare_plan(
        tmp_path / "recovery-plan.json",
        client=target,
        epoch_getter=_epoch,
        main_sha_getter=lambda: MAIN,
    )

    assert envelope["plan"]["target_precondition"]["completed_prefixes"] == ["00"]


def test_prepare_refuses_partial_prefix_recovery(tmp_path: Path, monkeypatch) -> None:
    target = FakeTarget()
    target.schema_created = True
    monkeypatch.setattr(gate, "source_stats", lambda _client: SOURCE)
    monkeypatch.setattr(gate, "source_batches", lambda _client: BATCHES)
    monkeypatch.setattr(
        gate,
        "lookup_stats",
        lambda _client: {
            "exists": True,
            "visible_rows": 1,
            "distinct_serials": 1,
            "max_source_rank": 99,
            "binding_sum": "bad",
            "binding_xor": "bad",
        },
    )
    monkeypatch.setattr(gate, "ready_marker", lambda _client: None)
    monkeypatch.setattr(gate, "_batch_stats", _batch_observer(target, mismatch="00"))

    with pytest.raises(RuntimeError, match="partial or digest-mismatched prefix 00"):
        gate.prepare_plan(
            tmp_path / "recovery-plan.json",
            client=target,
            epoch_getter=_epoch,
            main_sha_getter=lambda: MAIN,
        )


def test_execute_requires_exact_authority_before_mutation(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    with pytest.raises(PermissionError, match="exact #766 authority"):
        gate.execute_plan(
            path,
            plan_sha=sha,
            authority="GO WRONG",
            receipt_path=tmp_path / "receipt.json",
            client=target,
        )
    assert target.commands == []


def test_partial_prefix_refuses_automatic_resume(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    target.schema_created = True
    _patch_common(monkeypatch, target)
    monkeypatch.setattr(gate, "_batch_stats", _batch_observer(target, mismatch="00"))
    with pytest.raises(RuntimeError, match="partial or digest-mismatched"):
        gate.execute_plan(
            path,
            plan_sha=sha,
            authority=gate.authority_token(sha),
            receipt_path=tmp_path / "receipt.json",
            client=target,
        )
    assert target.commands == []


def test_recovery_capacity_starts_after_frozen_completed_rows(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path, completed_prefixes=["00"])
    target = FakeTarget()
    target.schema_created = True
    target.inserted.add("00")
    _patch_common(monkeypatch, target)
    monkeypatch.setattr(gate, "_batch_stats", _batch_observer(target))
    remaining: list[int] = []

    def record_capacity(*_args, **kwargs) -> None:
        remaining.append(int(kwargs["remaining_rows"]))

    monkeypatch.setattr(gate, "_assert_remaining_capacity", record_capacity)
    result = gate.execute_plan(
        path,
        plan_sha=sha,
        authority=gate.authority_token(sha),
        receipt_path=tmp_path / "receipt.json",
        client=target,
    )

    assert result["status"] == "SUCCESS"
    assert remaining[:2] == [2, 2]
    assert result["batch_summary"]["skipped_complete_prefixes"][0] == "00"


def test_recovery_frozen_completed_prefix_drift_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path, completed_prefixes=["00"])
    target = FakeTarget()
    target.schema_created = True
    _patch_common(monkeypatch, target)
    monkeypatch.setattr(gate, "_batch_stats", _batch_observer(target))

    with pytest.raises(RuntimeError, match="recovery completed prefix 00 drifted"):
        gate.execute_plan(
            path,
            plan_sha=sha,
            authority=gate.authority_token(sha),
            receipt_path=tmp_path / "receipt.json",
            client=target,
        )

    assert target.commands == []


def test_complete_prefixes_resume_without_duplicate_backfill(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    target.schema_created = True
    target.inserted.update({"00", "01"})
    _patch_common(monkeypatch, target)
    monkeypatch.setattr(gate, "_batch_stats", _batch_observer(target))
    result = gate.execute_plan(
        path,
        plan_sha=sha,
        authority=gate.authority_token(sha),
        receipt_path=tmp_path / "receipt.json",
        client=target,
    )
    assert result["status"] == "SUCCESS"
    assert result["batch_summary"]["inserted_prefixes"] == []
    assert result["batch_summary"]["skipped_complete_prefixes"] == ["00", "01"]
    assert len(target.commands) == 1
    assert gate.READY_COMPONENT in target.commands[0]
def test_fresh_path_creates_missing_prefixes_then_ready_last(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "plan.json"
    sha = _write_plan(path)
    target = FakeTarget()
    _patch_common(monkeypatch, target)
    monkeypatch.setattr(gate, "_batch_stats", _batch_observer(target))
    result = gate.execute_plan(
        path,
        plan_sha=sha,
        authority=gate.authority_token(sha),
        receipt_path=tmp_path / "receipt.json",
        client=target,
    )
    assert result["status"] == "SUCCESS"
    assert target.commands[0].startswith("CREATE TABLE")
    inserts = [
        sql for sql in target.commands
        if sql.startswith(f"INSERT INTO {gate.US_EVENT_SERIAL_LOOKUP_TABLE}")
    ]
    assert len(inserts) == 2
    assert "startsWith(serial_number,'00')" in inserts[0]
    assert "startsWith(serial_number,'01')" in inserts[1]
    assert gate.READY_COMPONENT in target.commands[-1]
    assert result["batch_summary"]["inserted_prefixes"] == ["00", "01"]
