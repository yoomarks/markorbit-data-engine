from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace
import json
import sqlite3

import pytest

import app.us.applicant_candidate_backfill_control as control
from app.us.applicant_candidate_backfill import ApplicantIndexBackfillCursor
from app.us.applicant_name_lookup_backfill import ApplicantNameLookupBackfillCursor
from app.us.applicant_candidate_backfill_control import (
    USApplicantServingEpoch,
    _name_lookup_cursor_from_run,
    applicant_index_ready_for_epoch,
    applicant_name_lookup_ready_for_epoch,
    applicant_name_lookup_observed_at_for_epoch,
    current_us_applicant_serving_epoch,
    start_backfill_run,
)


class FakeCursor:
    def __init__(self, *, all_rows=None, one_row=None):
        self.all_rows = list(all_rows or [])
        self.one_row = one_row
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchall(self):
        return list(self.all_rows)

    def fetchone(self):
        return self.one_row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _factory(cursor):
    return lambda: FakeConnection(cursor)


def _epoch() -> USApplicantServingEpoch:
    return USApplicantServingEpoch(
        bulk_run_id="11111111-1111-1111-1111-111111111111",
        plan_sha256="a" * 64,
        checkpoint_sequence=310,
        final_audit_version="US_APPLICATION_TARGET_BULK_FINAL_AUDIT_V1",
    )


def test_serving_epoch_rejects_active_bulk_publication():
    cursor = FakeCursor(
        all_rows=[{"run_id": "r", "status": "RUNNING", "payload": {}, "metrics": {}}]
    )
    with pytest.raises(RuntimeError, match="not quiescent"):
        current_us_applicant_serving_epoch(connection_factory=_factory(cursor))


def test_serving_epoch_requires_durable_full_corpus_success():
    cursor = FakeCursor(
        all_rows=[
            {
                "run_id": "11111111-1111-1111-1111-111111111111",
                "status": "SUCCESS",
                "payload": {"approved_plan_sha256": "a" * 64},
                "metrics": {
                    "last_safe_checkpoint_sequence": 310,
                    "full_accepted_source_corpus_on_target": True,
                    "phase": "COMPLETE",
                    "final_audit_version": "US_APPLICATION_TARGET_BULK_FINAL_AUDIT_V1",
                },
            }
        ]
    )
    epoch = current_us_applicant_serving_epoch(connection_factory=_factory(cursor))
    assert epoch.checkpoint_sequence == 310
    assert len(epoch.token) == 64


def test_backfill_start_is_fail_closed_without_explicit_mutation_authority():
    with pytest.raises(PermissionError, match="explicit production mutation authorization"):
        start_backfill_run(epoch=_epoch(), implementation_sha="b" * 40)


def test_readiness_requires_matching_epoch_and_complete_receipt():
    epoch = _epoch()
    cursor = FakeCursor(
        one_row={
            "payload": {"source_epoch": epoch.to_dict()},
            "metrics": {
                "source_epoch_token": epoch.token,
                "completeness": {"complete": True},
            },
        }
    )
    assert (
        applicant_index_ready_for_epoch(
            epoch,
            connection_factory=_factory(cursor),
        )
        is True
    )


def test_readiness_rejects_stale_epoch():
    epoch = _epoch()
    stale = USApplicantServingEpoch(
        bulk_run_id=epoch.bulk_run_id,
        plan_sha256="c" * 64,
        checkpoint_sequence=310,
        final_audit_version=epoch.final_audit_version,
    )
    cursor = FakeCursor(
        one_row={
            "payload": {"source_epoch": stale.to_dict()},
            "metrics": {
                "source_epoch_token": stale.token,
                "completeness": {"complete": True},
            },
        }
    )
    assert applicant_index_ready_for_epoch(epoch, connection_factory=_factory(cursor)) is False


def test_name_lookup_readiness_requires_its_nested_complete_receipt():
    epoch = _epoch()
    incomplete = FakeCursor(
        one_row={
            "payload": {"source_epoch": epoch.to_dict()},
            "metrics": {
                "source_epoch_token": epoch.token,
                "completeness": {"complete": True},
            },
        }
    )
    assert (
        applicant_name_lookup_ready_for_epoch(epoch, connection_factory=_factory(incomplete))
        is False
    )

    complete = FakeCursor(
        one_row={
            "payload": {"source_epoch": epoch.to_dict()},
            "metrics": {
                "source_epoch_token": epoch.token,
                "completeness": {
                    "complete": True,
                    "name_lookup_complete": True,
                    "name_lookup": {"complete": True},
                },
            },
        }
    )
    assert (
        applicant_name_lookup_ready_for_epoch(epoch, connection_factory=_factory(complete)) is True
    )


def _complete_name_lookup_row(epoch):
    return {
        "status": "SUCCESS",
        "payload": {"source_epoch": epoch.to_dict()},
        "metrics": {
            "source_epoch_token": epoch.token,
            "completeness": {
                "complete": True, "name_lookup_complete": True,
                "name_lookup": {"complete": True},
            },
        },
        "finished_at": datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc),
    }


def test_name_lookup_observation_uses_matching_complete_durable_record():
    epoch = _epoch()
    row = _complete_name_lookup_row(epoch)
    cursor = FakeCursor(one_row=row)
    assert applicant_name_lookup_observed_at_for_epoch(
        epoch, connection_factory=_factory(cursor)
    ) == row["finished_at"]
    sql, params = cursor.executions[0]
    assert "finished_at" in sql and "status = 'SUCCESS'" in sql and "LIMIT 1" in sql
    assert params == (control.BACKFILL_JOB_TYPE, True, True)


@pytest.mark.parametrize("path,value", [
    ("status", "RUNNING"),
    ("status", "FAILED"),
    ("status", None),
    ("payload.source_epoch.token", "wrong-epoch"),
    ("metrics.source_epoch_token", "wrong-epoch"),
    ("metrics.completeness.complete", False),
    ("metrics.completeness.name_lookup_complete", False),
    ("metrics.completeness.name_lookup.complete", False),
    ("finished_at", None),
    ("finished_at", "2026-09-06T01:02:03Z"),
    ("finished_at", datetime(2026, 9, 6, 1, 2, 3)),
])
def test_name_lookup_observation_rejects_unknown_or_unbound_evidence(path, value):
    epoch = _epoch()
    row = _complete_name_lookup_row(epoch)
    target = row
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    assert applicant_name_lookup_observed_at_for_epoch(
        epoch, connection_factory=_factory(FakeCursor(one_row=row))
    ) is None


def test_name_lookup_observation_orders_latest_run_before_old_success():
    # Execute the production SELECT; SQLite adapts only driver placeholders/row decoding.
    epoch = _epoch()
    receipt = _complete_name_lookup_row(epoch)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS control")
    conn.execute("CREATE TABLE control.job_run (run_id TEXT, job_type TEXT, status TEXT, "
                 "payload TEXT, metrics TEXT, started_at TEXT, finished_at TEXT)")
    for run_id, status, started, finished in [
        ("old", "SUCCESS", "2026-09-05T00:00:00Z", "2026-09-06T01:02:03Z"),
        ("new", "RUNNING", "2026-09-07T00:00:00Z", None),
    ]:
        conn.execute("INSERT INTO control.job_run VALUES (?, ?, ?, ?, ?, ?, ?)", (
            run_id, control.BACKFILL_JOB_TYPE, status, json.dumps(receipt["payload"]),
            json.dumps(receipt["metrics"]), started, finished,
        ))

    def decoded(row):
        if row is None:
            return None
        result = dict(row)
        for field in ["payload", "metrics"]:
            result[field] = json.loads(result[field])
        if result["finished_at"] is not None:
            result["finished_at"] = datetime.fromisoformat(result["finished_at"])
        return result

    @contextmanager
    def cursor_reader():
        cursor = conn.cursor()
        try:
            yield SimpleNamespace(
                execute=lambda sql, params: cursor.execute(sql.replace("%s", "?"), params),
                fetchone=lambda: decoded(cursor.fetchone()),
            )
        finally:
            cursor.close()

    @contextmanager
    def factory():
        yield SimpleNamespace(cursor=cursor_reader)

    try:
        assert applicant_name_lookup_ready_for_epoch(epoch, connection_factory=factory) is True
        assert applicant_name_lookup_observed_at_for_epoch(epoch, connection_factory=factory) is None
        conn.execute("UPDATE control.job_run SET status='SUCCESS', finished_at=? WHERE run_id='new'",
                     ("2026-09-08T01:02:03Z",))
        assert applicant_name_lookup_observed_at_for_epoch(
            epoch, connection_factory=factory
        ) == datetime(2026, 9, 8, 1, 2, 3, tzinfo=timezone.utc)
    finally:
        conn.close()


def test_name_lookup_observation_requires_a_durable_record():
    assert applicant_name_lookup_observed_at_for_epoch(
        _epoch(), connection_factory=_factory(FakeCursor())
    ) is None


def test_name_lookup_resume_cursor_is_independent_from_candidate_cursor():
    cursor = _name_lookup_cursor_from_run(
        {
            "metrics": {
                "after_serial": "candidate-finished",
                "emitted": 99,
                "name_lookup_cursor": {
                    "after_candidate_key": "a" * 64,
                    "after_serial": "lookup-serial",
                    "after_owner_key": "b" * 64,
                    "emitted": 12,
                },
            }
        }
    )
    assert cursor.after_serial == "lookup-serial"
    assert cursor.emitted == 12


def test_resume_clears_cooperative_stop_flag(monkeypatch):
    epoch = _epoch()
    monkeypatch.setattr(
        control,
        "load_backfill_run",
        lambda *args, **kwargs: {
            "status": "INTERRUPTED",
            "payload": {
                "source_epoch": epoch.to_dict(),
                "stop_requested": True,
                "implementation_sha": "a" * 40,
            },
            "metrics": {"emitted": 25},
        },
    )
    cursor = FakeCursor(one_row={"run_id": "run"})
    resumed = control.resume_backfill_run(
        "run",
        current_epoch=epoch,
        implementation_sha="b" * 40,
        connection_factory=_factory(cursor),
    )
    assert resumed.emitted == 25
    assert "'stop_requested', false" in cursor.executions[0][0]
    assert "'implementation_sha', %s::text" in cursor.executions[0][0]
    assert cursor.executions[0][1][0] == "b" * 40
    assert '"from": "aaaaaaaa' in cursor.executions[0][1][1]
    assert '"to": "bbbbbbbb' in cursor.executions[0][1][1]


def test_execute_requires_both_candidate_and_name_lookup_completeness(monkeypatch):
    epoch = _epoch()
    completed = []
    monkeypatch.setattr(
        control, "resume_backfill_run", lambda *args, **kwargs: ApplicantIndexBackfillCursor()
    )
    monkeypatch.setattr(
        control,
        "backfill_us_applicant_candidate_index",
        lambda **kwargs: ApplicantIndexBackfillCursor(emitted=3),
    )
    monkeypatch.setattr(
        control,
        "load_backfill_run",
        lambda *args, **kwargs: {"metrics": {"name_lookup_cursor": {"emitted": 1}}},
    )
    monkeypatch.setattr(
        control,
        "backfill_us_applicant_name_lookup",
        lambda **kwargs: ApplicantNameLookupBackfillCursor(emitted=3),
    )
    monkeypatch.setattr(
        control,
        "verify_us_applicant_candidate_index",
        lambda client: {"complete": True, "index_visible_rows": 3},
    )
    monkeypatch.setattr(
        control,
        "verify_us_applicant_name_lookup",
        lambda client: {"complete": False, "lookup_visible_rows": 2},
    )
    monkeypatch.setattr(
        control,
        "complete_backfill_run",
        lambda *args, **kwargs: completed.append(kwargs["completeness"]),
    )
    monkeypatch.setattr(control, "reconcile_us_applicant_candidate_index", lambda **kwargs: 0)
    monkeypatch.setattr(control, "reconcile_us_applicant_name_lookup", lambda **kwargs: 0)

    result = control.execute_backfill_run(
        "run", client=object(), serving_epoch_getter=lambda: epoch
    )

    assert result["completeness"]["complete"] is False
    assert result["completeness"]["name_lookup_complete"] is False
    assert completed[0]["complete"] is False


def test_execute_accepts_already_complete_epoch_without_mutation(monkeypatch):
    epoch = _epoch()
    completed = []
    monkeypatch.setattr(
        control, "resume_backfill_run", lambda *args, **kwargs: ApplicantIndexBackfillCursor()
    )
    monkeypatch.setattr(
        control,
        "backfill_us_applicant_candidate_index",
        lambda **kwargs: pytest.fail("candidate backfill must not run"),
    )
    monkeypatch.setattr(
        control,
        "backfill_us_applicant_name_lookup",
        lambda **kwargs: pytest.fail("name lookup backfill must not run"),
    )
    monkeypatch.setattr(
        control,
        "verify_us_applicant_candidate_index",
        lambda client: {"complete": True, "index_visible_rows": 32_467_363},
    )
    monkeypatch.setattr(
        control,
        "verify_us_applicant_name_lookup",
        lambda client: {"complete": True, "lookup_visible_rows": 32_467_363},
    )
    monkeypatch.setattr(
        control,
        "complete_backfill_run",
        lambda *args, **kwargs: completed.append(kwargs["completeness"]),
    )

    result = control.execute_backfill_run(
        "run", client=object(), serving_epoch_getter=lambda: epoch
    )

    assert result["cursor"]["emitted"] == 32_467_363
    assert result["name_lookup_cursor"]["emitted"] == 32_467_363
    assert result["completeness"]["complete"] is True
    assert completed[0]["complete"] is True


def test_execute_reconciles_bounded_gap_before_full_replay(monkeypatch):
    epoch = _epoch()
    completed = []
    candidate_receipts = iter(
        [
            {"complete": False, "source_visible_rows": 100, "index_visible_rows": 98},
            {"complete": True, "source_visible_rows": 100, "index_visible_rows": 100},
        ]
    )
    lookup_receipts = iter(
        [
            {"complete": False, "source_visible_rows": 100, "lookup_visible_rows": 97},
            {"complete": True, "source_visible_rows": 100, "lookup_visible_rows": 100},
        ]
    )
    monkeypatch.setattr(
        control, "resume_backfill_run", lambda *args, **kwargs: ApplicantIndexBackfillCursor()
    )
    monkeypatch.setattr(
        control,
        "backfill_us_applicant_candidate_index",
        lambda **kwargs: pytest.fail("candidate full replay must not run"),
    )
    monkeypatch.setattr(
        control,
        "backfill_us_applicant_name_lookup",
        lambda **kwargs: pytest.fail("lookup full replay must not run"),
    )
    monkeypatch.setattr(
        control, "verify_us_applicant_candidate_index", lambda client: next(candidate_receipts)
    )
    monkeypatch.setattr(
        control, "verify_us_applicant_name_lookup", lambda client: next(lookup_receipts)
    )
    monkeypatch.setattr(control, "reconcile_us_applicant_candidate_index", lambda **kwargs: 2)
    monkeypatch.setattr(control, "reconcile_us_applicant_name_lookup", lambda **kwargs: 3)
    monkeypatch.setattr(
        control,
        "complete_backfill_run",
        lambda *args, **kwargs: completed.append(kwargs["completeness"]),
    )

    result = control.execute_backfill_run(
        "run", client=object(), serving_epoch_getter=lambda: epoch
    )

    assert result["cursor"]["emitted"] == 100
    assert result["name_lookup_cursor"]["emitted"] == 100
    assert result["completeness"]["reconciliation"] == {
        "candidate_rows": 2,
        "name_lookup_rows": 3,
    }
    assert completed[0]["complete"] is True


def test_serving_epoch_accepts_newer_durable_checkpoint_than_legacy_baseline():
    cursor = FakeCursor(
        all_rows=[
            {
                "run_id": "22222222-2222-2222-2222-222222222222",
                "status": "SUCCESS",
                "payload": {"approved_plan_sha256": "d" * 64},
                "metrics": {
                    "last_safe_checkpoint_sequence": 343,
                    "accepted_target_sequence_count": 343,
                    "remaining_to_accepted_corpus": 0,
                    "last_archived_source_sequence": 343,
                    "full_accepted_source_corpus_on_target": True,
                    "phase": "COMPLETE",
                    "final_audit_version": "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V2",
                },
            }
        ]
    )
    epoch = current_us_applicant_serving_epoch(connection_factory=_factory(cursor))
    assert epoch.checkpoint_sequence == 343
    assert epoch.plan_sha256 == "d" * 64
