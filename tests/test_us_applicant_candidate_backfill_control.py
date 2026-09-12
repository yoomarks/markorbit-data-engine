from __future__ import annotations

import pytest

import app.us.applicant_candidate_backfill_control as control
from app.us.applicant_candidate_backfill import ApplicantIndexBackfillCursor
from app.us.applicant_name_lookup_backfill import ApplicantNameLookupBackfillCursor
from app.us.applicant_candidate_backfill_control import (
    USApplicantServingEpoch,
    _name_lookup_cursor_from_run,
    applicant_index_ready_for_epoch,
    applicant_name_lookup_ready_for_epoch,
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

    result = control.execute_backfill_run(
        "run", client=object(), serving_epoch_getter=lambda: epoch
    )

    assert result["completeness"]["complete"] is False
    assert result["completeness"]["name_lookup_complete"] is False
    assert completed[0]["complete"] is False
