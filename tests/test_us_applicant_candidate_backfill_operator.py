from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.us.applicant_candidate_backfill_control import USApplicantServingEpoch
from app.us.applicant_candidate_backfill_operator import (
    TargetApplicantBackfillClient,
    execute_backfill_plan,
    load_backfill_plan,
    prepare_backfill_plan,
)
from app.us.applicant_candidate_index import APPLICANT_INDEX_TABLE


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class SchemaClient:
    def __init__(self):
        self.queries = []

    def query(self, sql, *, settings=None):
        self.queries.append((sql, settings))
        if "FROM system.tables" in sql:
            return Result([
                ["us_applicant_candidate_current", "1" * 36, "ReplacingMergeTree", "candidate_key, serial_number, owner_key"],
                ["us_owner_current", "2" * 36, "ReplacingMergeTree", "serial_number, owner_key"],
            ])
        if "schema_version" in sql:
            return Result([["US_OWNER_READ_V1"]])
        raise AssertionError(sql)


class BaseClient:
    def __init__(self):
        self.sql = []
        self.inserts = []

    def query(self, sql):
        self.sql.append(sql)
        return Result([])

    def insert(self, table, rows, *, column_names):
        self.inserts.append((table, rows, column_names))


def epoch() -> USApplicantServingEpoch:
    return USApplicantServingEpoch(
        bulk_run_id="bulk-run",
        plan_sha256="a" * 64,
        checkpoint_sequence=310,
        final_audit_version="US_TARGET_BULK_FINAL_AUDIT_V1",
    )


def make_plan(tmp_path: Path, *, sha: str = "b" * 40):
    path = tmp_path / "plan.json"
    envelope = prepare_backfill_plan(
        path,
        client=SchemaClient(),
        epoch_getter=epoch,
        main_sha_getter=lambda: sha,
    )
    return path, envelope


def test_target_adapter_restricts_settings_and_insert_scope():
    base = BaseClient()
    client = TargetApplicantBackfillClient(base)
    client.query(
        "SELECT 1",
        settings={"max_threads": 1, "read_overflow_mode": "throw"},
    )
    assert "SETTINGS max_threads = 1, read_overflow_mode = 'throw'" in base.sql[0]

    with pytest.raises(ValueError, match="unsupported Applicant backfill query settings"):
        client.query("SELECT 1", settings={"max_memory_usage": 1})
    with pytest.raises(RuntimeError, match="may insert only"):
        client.insert("markorbit_facts.us_owner_current", [], column_names=[])


def test_prepare_fails_without_durable_p310_epoch(tmp_path: Path):
    path = tmp_path / "plan.json"

    def unavailable_epoch():
        raise RuntimeError("no durable complete US Application bulk serving epoch is available")

    with pytest.raises(RuntimeError, match="no durable complete"):
        prepare_backfill_plan(
            path,
            client=SchemaClient(),
            epoch_getter=unavailable_epoch,
            main_sha_getter=lambda: "b" * 40,
        )
    assert not path.exists()


def test_load_plan_rejects_exact_sha_mismatch(tmp_path: Path):
    path, envelope = make_plan(tmp_path)
    assert envelope["plan_sha256"]
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        load_backfill_plan(path, "0" * 64)


def test_execute_requires_explicit_mutation_authority(tmp_path: Path):
    path, envelope = make_plan(tmp_path)
    with pytest.raises(PermissionError, match="explicit production mutation authorization"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=tmp_path / "receipt.json",
            production_mutation_authorized=False,
            client=SchemaClient(),
            epoch_getter=epoch,
            main_sha_getter=lambda: "b" * 40,
        )
    assert not (tmp_path / "receipt.json").exists()


def test_execute_rejects_main_drift_before_mutation(tmp_path: Path):
    path, envelope = make_plan(tmp_path)
    started = []
    with pytest.raises(RuntimeError, match="main SHA drifted"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=tmp_path / "receipt.json",
            production_mutation_authorized=True,
            client=SchemaClient(),
            epoch_getter=epoch,
            main_sha_getter=lambda: "c" * 40,
            start_fn=lambda **kwargs: started.append(kwargs) or "run",
        )
    assert started == []


def test_execute_rejects_epoch_drift_before_mutation(tmp_path: Path):
    path, envelope = make_plan(tmp_path)
    drifted = USApplicantServingEpoch(
        bulk_run_id="bulk-run-2",
        plan_sha256="a" * 64,
        checkpoint_sequence=310,
        final_audit_version="US_TARGET_BULK_FINAL_AUDIT_V1",
    )
    started = []
    with pytest.raises(RuntimeError, match="serving epoch drifted"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=tmp_path / "receipt.json",
            production_mutation_authorized=True,
            client=SchemaClient(),
            epoch_getter=lambda: drifted,
            main_sha_getter=lambda: "b" * 40,
            start_fn=lambda **kwargs: started.append(kwargs) or "run",
        )
    assert started == []


def test_execute_rejects_schema_drift_before_mutation(tmp_path: Path):
    path, envelope = make_plan(tmp_path)

    class DriftedSchemaClient(SchemaClient):
        def query(self, sql, *, settings=None):
            result = super().query(sql, settings=settings)
            if "FROM system.tables" in sql:
                result.result_rows[0][1] = "9" * 36
            return result

    started = []
    with pytest.raises(RuntimeError, match="schema identity drifted"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=tmp_path / "receipt.json",
            production_mutation_authorized=True,
            client=DriftedSchemaClient(),
            epoch_getter=epoch,
            main_sha_getter=lambda: "b" * 40,
            start_fn=lambda **kwargs: started.append(kwargs) or "run",
        )
    assert started == []


def test_execute_writes_success_receipt(tmp_path: Path):
    path, envelope = make_plan(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    started = []

    def fake_start(**kwargs):
        started.append(kwargs)
        return "backfill-run"

    def fake_execute(run_id, **kwargs):
        assert run_id == "backfill-run"
        return {
            "cursor": {"after_serial": "9", "after_owner_key": "f" * 64, "emitted": 10},
            "source_epoch": epoch().to_dict(),
            "completeness": {"complete": True, "source_visible_rows": 10, "index_visible_rows": 10},
        }

    receipt = execute_backfill_plan(
        path,
        plan_sha=envelope["plan_sha256"],
        receipt_path=receipt_path,
        production_mutation_authorized=True,
        client=SchemaClient(),
        epoch_getter=epoch,
        main_sha_getter=lambda: "b" * 40,
        start_fn=fake_start,
        execute_fn=fake_execute,
    )
    assert receipt["status"] == "SUCCESS"
    assert receipt["run_id"] == "backfill-run"
    assert started[0]["production_mutation_authorized"] is True
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted == receipt


def test_execute_failure_writes_auditable_receipt(tmp_path: Path):
    path, envelope = make_plan(tmp_path)
    receipt_path = tmp_path / "receipt.json"

    def fake_execute(run_id, **kwargs):
        raise RuntimeError("synthetic backfill failure")

    with pytest.raises(RuntimeError, match="synthetic backfill failure"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=receipt_path,
            production_mutation_authorized=True,
            client=SchemaClient(),
            epoch_getter=epoch,
            main_sha_getter=lambda: "b" * 40,
            start_fn=lambda **kwargs: "backfill-run",
            execute_fn=fake_execute,
        )
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "FAILED"
    assert persisted["run_id"] == "backfill-run"
    assert persisted["error_type"] == "RuntimeError"
    assert persisted["plan_sha256"] == envelope["plan_sha256"]


def test_load_plan_rejects_unknown_plan_keys(tmp_path: Path):
    path, envelope = make_plan(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["plan"]["unexpected"] = "value"
    raw["plan_sha256"] = __import__(
        "hashlib"
    ).sha256(
        json.dumps(
            raw["plan"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed Applicant backfill plan keys"):
        load_backfill_plan(path, raw["plan_sha256"])


def test_execute_sha_mismatch_writes_failure_receipt_before_mutation(tmp_path: Path):
    path, _ = make_plan(tmp_path)
    receipt_path = tmp_path / "sha-failure.json"
    started = []
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        execute_backfill_plan(
            path,
            plan_sha="0" * 64,
            receipt_path=receipt_path,
            production_mutation_authorized=True,
            client=SchemaClient(),
            epoch_getter=epoch,
            main_sha_getter=lambda: "b" * 40,
            start_fn=lambda **kwargs: started.append(kwargs) or "run",
        )
    assert started == []
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "FAILED"
    assert persisted["run_id"] is None
    assert persisted["error_type"] == "RuntimeError"
