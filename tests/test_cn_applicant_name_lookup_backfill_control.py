from __future__ import annotations

from datetime import date

import pytest

import app.cn.applicant_name_lookup_backfill_control as control
from app.cn.applicant_name_lookup_backfill import CNApplicantNameLookupBackfillCursor
from app.cn.applicant_name_lookup_backfill_control import (
    CNApplicantServingEpoch,
    lookup_ready_for_epoch,
    start_backfill_run,
)
from app.cn.research_filing_to_prelim_duration import ServingEpoch


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchone(self):
        return next(self.rows, None)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Connection:
    def __init__(self, cursor):
        self.value = cursor

    def cursor(self):
        return self.value

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def factory(cursor):
    return lambda: Connection(cursor)


def epoch() -> CNApplicantServingEpoch:
    return CNApplicantServingEpoch.from_serving_epoch(ServingEpoch(date(2026, 9, 1), 42, 40))


def test_epoch_token_is_deterministic():
    assert epoch().token == epoch().token
    assert len(epoch().token) == 64


def test_start_requires_explicit_production_authority():
    with pytest.raises(PermissionError, match="explicit production mutation authorization"):
        start_backfill_run(epoch=epoch(), implementation_sha="a" * 40)


def test_readiness_requires_exact_epoch_and_complete_receipt():
    current = epoch()
    cursor = Cursor(
        [
            {
                "payload": {"source_epoch": current.to_dict()},
                "metrics": {
                    "source_epoch_token": current.token,
                    "completeness": {"complete": True},
                },
            }
        ]
    )
    assert lookup_ready_for_epoch(current, connection_factory=factory(cursor)) is True

    stale = CNApplicantServingEpoch("2026-09-01", 43, 40)
    cursor = Cursor(
        [
            {
                "payload": {"source_epoch": current.to_dict()},
                "metrics": {
                    "source_epoch_token": current.token,
                    "completeness": {"complete": True},
                },
            }
        ]
    )
    assert lookup_ready_for_epoch(stale, connection_factory=factory(cursor)) is False


def test_execute_rejects_partial_completeness_and_marks_resumable(monkeypatch):
    current = epoch()
    finished = []
    monkeypatch.setattr(
        control,
        "resume_backfill_run",
        lambda *args, **kwargs: CNApplicantNameLookupBackfillCursor(),
    )
    monkeypatch.setattr(
        control,
        "backfill_cn_applicant_name_lookup",
        lambda **kwargs: CNApplicantNameLookupBackfillCursor(emitted=2),
    )
    monkeypatch.setattr(
        control, "verify_cn_applicant_name_lookup", lambda client: {"complete": False}
    )
    monkeypatch.setattr(control, "_finish", lambda *args: finished.append(args))
    with pytest.raises(RuntimeError, match="not accepted"):
        control.execute_backfill_run("run", client=object(), serving_epoch_getter=lambda: current)
    assert finished[0][1] == "INTERRUPTED"
    assert finished[0][2]["resumable"] is True
