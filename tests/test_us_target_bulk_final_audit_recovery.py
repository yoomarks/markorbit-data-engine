from pathlib import Path

import pytest

from app.us import target_bulk_final_audit_recovery as recovery


def _task() -> dict:
    return {
        "run_id": "00000000-0000-0000-0000-000000000600",
        "status": "BLOCKED",
        "payload": {
            "plan_sha256": "a" * 64,
            "approved_plan_sha256": "a" * 64,
            "batch_manifest_sha256": "b" * 64,
            "plan_path": "plan.json",
            "batch_manifest_path": "manifest.json",
        },
        "metrics": {
            "phase": "BLOCKED",
            "plan_sha256": "a" * 64,
            "batch_manifest_sha256": "b" * 64,
            "completed_sequences": [292, 293],
            "completed_suffix_count": 2,
            "current_sequence": 293,
            "last_safe_checkpoint_sequence": 293,
            "last_archived_source_sequence": 293,
            "current_package_state": "COMPLETE",
            "current_canary_state": "COMPLETE",
            "child_journal_state": "COMPLETE",
        },
        "error_message": "RuntimeError: batch final audit failed: legacy audit mismatch",
    }


def _master_manifest() -> tuple[dict, dict]:
    return (
        {"plan_sha256": "a" * 64, "execution_main": "d" * 40},
        {
            "manifest_sha256": "b" * 64,
            "children": [{"sequence": 292}, {"sequence": 293}],
        },
    )


def _disable_schema_validation(monkeypatch) -> None:
    monkeypatch.setattr(recovery, "validate_bulk_plan", lambda plan: None)
    monkeypatch.setattr(
        recovery, "validate_batch_manifest", lambda manifest, master_plan: None
    )


def test_recovery_candidate_requires_exact_completed_final_audit_block(monkeypatch) -> None:
    _disable_schema_validation(monkeypatch)
    master, manifest = _master_manifest()
    assert recovery._validate_recovery_candidate(
        _task(), master=master, manifest=manifest
    ) == [292, 293]

    bad = _task()
    bad["error_message"] = "guarded child operator failed"
    with pytest.raises(RuntimeError, match="non-audit block"):
        recovery._validate_recovery_candidate(bad, master=master, manifest=manifest)

    bad = _task()
    bad["metrics"]["completed_sequences"] = [292]
    with pytest.raises(RuntimeError, match="every approved child COMPLETE"):
        recovery._validate_recovery_candidate(bad, master=master, manifest=manifest)

    bad = _task()
    bad["metrics"]["last_archived_source_sequence"] = 292
    with pytest.raises(RuntimeError, match="checkpoint drifted"):
        recovery._validate_recovery_candidate(bad, master=master, manifest=manifest)


def test_recover_final_audit_is_read_only_and_finalizes_control_state(monkeypatch, tmp_path) -> None:
    _disable_schema_validation(monkeypatch)
    task = _task()
    master, manifest = _master_manifest()
    recovery_main = "c" * 40
    captured: dict = {}

    monkeypatch.setattr(recovery.v1, "_git_head", lambda root: recovery_main)
    monkeypatch.setattr(recovery, "_git_clean", lambda root: True)
    monkeypatch.setattr(recovery, "_load_task", lambda run_id: task)
    monkeypatch.setattr(
        recovery.v1,
        "_read_json",
        lambda path, label: master if "master" in label else manifest,
    )
    monkeypatch.setattr(
        recovery,
        "audit_target_bulk_batch",
        lambda **kwargs: {
            "verified_sequences": list(range(1, 311)),
            "full_accepted_source_corpus_on_target": True,
        },
    )
    monkeypatch.setattr(recovery, "write_target_bulk_batch_audit", lambda path, audit: None)
    monkeypatch.setattr(recovery, "write_receipt", lambda path, receipt: None)

    def persist(run_id, *, expected_snapshot, metrics_patch):
        captured["run_id"] = run_id
        captured["snapshot"] = expected_snapshot
        captured["metrics"] = metrics_patch
        return {"status": "SUCCESS", "metrics": metrics_patch}

    monkeypatch.setattr(recovery, "_persist_success", persist)

    result = recovery.recover_final_audit(
        run_id=task["run_id"],
        expected_recovery_main=recovery_main,
        repo_root=tmp_path,
    )

    assert result["task"]["status"] == "SUCCESS"
    assert captured["metrics"]["accepted_target_sequence_count"] == 310
    assert captured["metrics"]["remaining_to_accepted_corpus"] == 0
    assert captured["metrics"]["final_audit_recovered_from_blocked"] is True
    assert result["recovery_receipt"]["target_mutation_performed"] is False
    assert result["recovery_receipt"]["child_operator_executed"] is False


def test_recover_final_audit_requires_clean_exact_recovery_main(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(recovery.v1, "_git_head", lambda root: "c" * 40)
    with pytest.raises(RuntimeError, match="main SHA mismatch"):
        recovery.recover_final_audit(
            run_id="run",
            expected_recovery_main="d" * 40,
            repo_root=tmp_path,
        )

    monkeypatch.setattr(recovery, "_git_clean", lambda root: False)
    with pytest.raises(RuntimeError, match="clean git worktree"):
        recovery.recover_final_audit(
            run_id="run",
            expected_recovery_main="c" * 40,
            repo_root=tmp_path,
        )
