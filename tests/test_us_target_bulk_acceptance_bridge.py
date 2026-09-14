from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.us import target_bulk_acceptance_bridge as bridge


MAIN = "e" * 40
TARGET_PLAN_SHA = "8" * 64
PRIOR_PLAN_SHA = "7" * 64
CLOSEOUT_PLAN_SHA = "6" * 64


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _evidence(tmp_path: Path) -> dict[str, Path]:
    return {
        "bulk_plan": _write(tmp_path / "bulk_plan.json", {
            "plan_sha256": TARGET_PLAN_SHA,
            "accepted_source_count": 343,
            "end_sequence": 343,
        }),
        "bulk_journal": _write(tmp_path / "bulk_journal.json", {
            "state": "COMPLETE",
            "plan_sha256": TARGET_PLAN_SHA,
            "last_completed_sequence": 343,
        }),
        "target_audit": _write(tmp_path / "audit.json", {
            "audit_version": "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V2",
            "plan_sha256": TARGET_PLAN_SHA,
            "accepted_source_count": 343,
            "full_accepted_source_corpus_on_target": True,
            "verified_sequences": list(range(1, 344)),
        }),
        "closeout_plan": _write(tmp_path / "closeout_plan.json", {
            "plan_sha256": CLOSEOUT_PLAN_SHA,
        }),
        "closeout_receipt": _write(tmp_path / "closeout_receipt.json", {
            "decision": "US_APPLICATION_SOURCE_ARCHIVE_CLOSEOUT_COMPLETE",
            "plan_sha256": CLOSEOUT_PLAN_SHA,
            "archive_zip_count": 343,
            "incoming_zip_count": 0,
            "clickhouse_mutation_performed": False,
            "source_archive_mutation_performed": True,
        }),
    }


def _prior() -> dict:
    return {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "plan_sha256": PRIOR_PLAN_SHA,
        "checkpoint_sequence": 310,
        "final_audit_version": "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V2",
    }


def _patch_prepare(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bridge, "_git", lambda *_args, **_kwargs: MAIN)
    monkeypatch.setattr(bridge, "_remote_main", lambda *_args, **_kwargs: MAIN)
    monkeypatch.setattr(bridge, "latest_complete_target_bulk_epoch", lambda: _prior())
    monkeypatch.setattr(bridge, "active_target_bulk_task", lambda: None)
    monkeypatch.setattr(bridge, "_source_layout", lambda _root: ([], [f"p{i:03d}.zip" for i in range(1, 344)]))


def test_build_bridge_plan_binds_full_evidence_and_unique_authority(monkeypatch, tmp_path: Path) -> None:
    _patch_prepare(monkeypatch, tmp_path)
    evidence = _evidence(tmp_path)
    plan = bridge.build_bridge_plan(
        repo_root=tmp_path,
        raw_root=tmp_path,
        bulk_plan_path=evidence["bulk_plan"],
        bulk_journal_path=evidence["bulk_journal"],
        target_audit_path=evidence["target_audit"],
        closeout_plan_path=evidence["closeout_plan"],
        closeout_receipt_path=evidence["closeout_receipt"],
        authority_generation_id="11111111-1111-4111-8111-111111111111",
    )
    assert plan["accepted_checkpoint_sequence"] == 343
    assert plan["prior_checkpoint_sequence"] == 310
    assert plan["prior_plan_sha256"] == PRIOR_PLAN_SHA
    assert plan["target_plan_sha256"] == TARGET_PLAN_SHA
    assert plan["execution_main"] == MAIN
    assert plan["plan_sha256"] == bridge.canonical_plan_sha(plan)
    assert plan["required_authority_token"].endswith(plan["plan_sha256"])


def test_validate_bridge_plan_rejects_source_layout_drift(monkeypatch, tmp_path: Path) -> None:
    _patch_prepare(monkeypatch, tmp_path)
    evidence = _evidence(tmp_path)
    plan = bridge.build_bridge_plan(
        repo_root=tmp_path, raw_root=tmp_path,
        bulk_plan_path=evidence["bulk_plan"], bulk_journal_path=evidence["bulk_journal"],
        target_audit_path=evidence["target_audit"], closeout_plan_path=evidence["closeout_plan"],
        closeout_receipt_path=evidence["closeout_receipt"],
        authority_generation_id="11111111-1111-4111-8111-111111111111",
    )
    monkeypatch.setattr(bridge, "_source_layout", lambda _root: (["unexpected.zip"], ["x"] * 342))
    with pytest.raises(RuntimeError, match="source layout drifted"):
        bridge.validate_bridge_plan(plan, repo_root=tmp_path)


class _ApplyCursor:
    def __init__(self) -> None:
        self.next_row = None
        self.insert_params = None

    def execute(self, sql, params=None):
        if "status = ANY" in sql:
            self.next_row = None
        elif "SELECT run_id, status, payload, metrics" in sql:
            self.next_row = {
                "run_id": _prior()["run_id"],
                "status": "SUCCESS",
                "payload": {"approved_plan_sha256": PRIOR_PLAN_SHA},
                "metrics": {
                    "phase": "COMPLETE",
                    "full_accepted_source_corpus_on_target": True,
                    "last_safe_checkpoint_sequence": 310,
                    "accepted_target_sequence_count": 310,
                    "remaining_to_accepted_corpus": 0,
                    "last_archived_source_sequence": 310,
                },
            }
        elif "INSERT INTO control.job_run" in sql:
            self.insert_params = params
            self.next_row = {"run_id": "22222222-2222-2222-2222-222222222222"}

    def fetchone(self):
        return self.next_row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ApplyConnection:
    def __init__(self, cursor: _ApplyCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_apply_bridge_inserts_one_success_epoch(monkeypatch) -> None:
    cursor = _ApplyCursor()
    conn = _ApplyConnection(cursor)
    plan = {
        "required_authority_token": "GO exact",
        "plan_sha256": "5" * 64,
        "target_plan_sha256": TARGET_PLAN_SHA,
        "accepted_checkpoint_sequence": 343,
        "prior_checkpoint_sequence": 310,
        "prior_epoch_run_id": _prior()["run_id"],
        "prior_plan_sha256": PRIOR_PLAN_SHA,
        "execution_main": MAIN,
        "raw_root": "ignored",
        "expected_incoming_zip_count": 0,
        "expected_archive_zip_count": 343,
        "authority_generation_id": "11111111-1111-4111-8111-111111111111",
        "evidence": {"target_audit": {"path": "ignored"}},
    }
    monkeypatch.setattr(bridge, "validate_bridge_plan", lambda _plan: {
        "plan_sha256": plan["plan_sha256"], "checkpoint": 343,
    })
    monkeypatch.setattr(bridge, "_load_verified_evidence", lambda _item: {
        "audit_version": "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V2",
        "verified_sequences": list(range(1, 344)),
    })
    monkeypatch.setattr(bridge, "_source_layout", lambda _root: ([], ["x"] * 343))
    monkeypatch.setattr(bridge, "_validate_evidence_payloads", lambda _plan: {})
    receipt = bridge.apply_bridge_plan(
        plan, authority_token="GO exact", connection_factory=lambda: conn,
    )
    assert receipt["decision"] == "US_APPLICATION_TARGET_ACCEPTANCE_BRIDGE_COMPLETE"
    assert receipt["accepted_checkpoint_sequence"] == 343
    assert conn.committed is True
    assert cursor.insert_params is not None
    payload = json.loads(cursor.insert_params[2])
    metrics = json.loads(cursor.insert_params[3])
    assert payload["host_phase"] == "ACCEPTANCE_BRIDGE"
    assert payload["approved_plan_sha256"] == plan["plan_sha256"]
    assert metrics["full_accepted_source_corpus_on_target"] is True
    assert metrics["last_safe_checkpoint_sequence"] == 343
    assert metrics["clickhouse_mutation_performed"] is False


def test_accepted_epoch_rejects_non_contiguous_verified_sequences() -> None:
    row = {
        "run_id": "33333333-3333-3333-3333-333333333333",
        "status": "SUCCESS",
        "payload": {"approved_plan_sha256": "a" * 64},
        "metrics": {
            "phase": "COMPLETE",
            "full_accepted_source_corpus_on_target": True,
            "last_safe_checkpoint_sequence": 310,
            "accepted_target_sequence_count": 310,
            "remaining_to_accepted_corpus": 0,
            "last_archived_source_sequence": 310,
            "final_audit_verified_sequences": [1] * 309 + [310],
        },
    }
    from app.us.target_bulk_tasks import accepted_target_bulk_epoch_from_row

    assert accepted_target_bulk_epoch_from_row(row) is None


def test_apply_bridge_rejects_wrong_exact_authority(monkeypatch) -> None:
    plan = {"required_authority_token": "GO exact"}
    monkeypatch.setattr(bridge, "validate_bridge_plan", lambda _plan: {})
    with pytest.raises(PermissionError, match="exact acceptance-bridge authority"):
        bridge.apply_bridge_plan(plan, authority_token="GO wrong")
