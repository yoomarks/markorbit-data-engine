from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import time
from typing import Any

from app.us import target_bulk_host_worker as v1
from app.us.target_bulk_batch import validate_batch_manifest
from app.us.target_bulk_batch_audit import (
    BATCH_FINAL_AUDIT_VERSION,
    audit_target_bulk_batch,
    write_target_bulk_batch_audit,
)
from app.us.target_bulk_plan import validate_bulk_plan
from app.us.target_bulk_task_control import fail_closed_recover_target_bulk_tasks
from app.us.target_bulk_tasks import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_NEEDS_OPERATOR,
    STATUS_PREPARE_QUEUED,
    STATUS_RUN_QUEUED,
    STATUS_SUCCESS,
    claim_next_target_bulk_task,
    target_bulk_stop_requested,
    update_target_bulk_task,
)


HOST_WORKER_VERSION = "US_APPLICATION_TARGET_BULK_HOST_WORKER_V2"
POLL_SECONDS = 2.0


def _validated_completed_prefix(
    completed_sequences: object,
    *,
    allowed_sequences: list[int],
) -> list[int]:
    if not isinstance(completed_sequences, list):
        raise RuntimeError("host task completed-sequence checkpoint must be a list")
    completed: list[int] = []
    for item in completed_sequences:
        if isinstance(item, bool):
            raise RuntimeError("host task completed-sequence checkpoint contains invalid value")
        try:
            sequence = int(item)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "host task completed-sequence checkpoint contains invalid value"
            ) from exc
        completed.append(sequence)
    if completed != allowed_sequences[: len(completed)]:
        raise RuntimeError(
            "host task completed-sequence checkpoint is not the contiguous approved prefix"
        )
    return completed


def _run_execution(task: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    payload = dict(task.get("payload") or {})
    metrics = dict(task.get("metrics") or {})
    run_id = str(task["run_id"])
    plan_path = Path(str(payload.get("plan_path") or ""))
    manifest_path = Path(str(payload.get("batch_manifest_path") or ""))
    master = v1._read_json(plan_path, "approved US target bulk master plan")
    manifest = v1._read_json(manifest_path, "approved US target bulk batch manifest")
    validate_bulk_plan(master)
    validate_batch_manifest(manifest, master_plan=master)

    approved = str(payload.get("approved_plan_sha256") or "").lower()
    if approved != str(master["plan_sha256"]).lower():
        raise RuntimeError("host execution does not have approval for the exact master plan SHA")
    if payload.get("batch_manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("host execution batch manifest SHA binding drifted")
    if v1._git_head(repo_root) != master["execution_main"]:
        raise RuntimeError("host execution main changed after operator approved the frozen plan")

    allowed_sequences = [int(item["sequence"]) for item in manifest["children"]]
    completed_sequences = _validated_completed_prefix(
        metrics.get("completed_sequences") or [],
        allowed_sequences=allowed_sequences,
    )

    for child in manifest["children"]:
        sequence = int(child["sequence"])
        if sequence in completed_sequences:
            continue
        if target_bulk_stop_requested(run_id):
            return update_target_bulk_task(
                run_id,
                status=STATUS_INTERRUPTED,
                metrics={
                    "worker_version": HOST_WORKER_VERSION,
                    "phase": "INTERRUPTED",
                    "current_sequence": sequence,
                    "completed_suffix_count": len(completed_sequences),
                    "completed_sequences": completed_sequences,
                    "stop_requested": True,
                },
                error_message="Stopped safely before starting the next approved child package.",
                finish=True,
            )

        child_path = Path(str(child.get("plan_path") or ""))
        child_plan = v1._read_json(child_path, f"child plan {sequence}")
        validate_bulk_plan(child_plan)
        if child_plan["plan_sha256"] != child["plan_sha256"]:
            raise RuntimeError(f"child plan file SHA binding drifted: {sequence}")

        update_target_bulk_task(
            run_id,
            metrics={
                "worker_version": HOST_WORKER_VERSION,
                "phase": "EXECUTING",
                "current_sequence": sequence,
                "current_file": child_plan["packages"][1]["file_name"],
                "completed_suffix_count": len(completed_sequences),
                "completed_sequences": completed_sequences,
                "stop_requested": False,
            },
            error_message=None,
        )
        v1._run_child_operator(
            task=task,
            repo_root=repo_root,
            child=child,
            child_plan=child_plan,
            completed_sequences=completed_sequences,
        )
        completed_sequences.append(sequence)
        update_target_bulk_task(
            run_id,
            metrics={
                "worker_version": HOST_WORKER_VERSION,
                "phase": "CHECKPOINTED",
                "current_sequence": sequence,
                "completed_suffix_count": len(completed_sequences),
                "completed_sequences": completed_sequences,
                "accepted_target_sequence_count": 2 + len(completed_sequences),
                "remaining_to_accepted_corpus": 310 - (2 + len(completed_sequences)),
                "last_safe_checkpoint_sequence": sequence,
                "stop_requested": target_bulk_stop_requested(run_id),
            },
            error_message=None,
        )

    if completed_sequences != allowed_sequences:
        raise RuntimeError("host task completed-sequence checkpoint does not cover the approved batch")

    update_target_bulk_task(
        run_id,
        metrics={
            "worker_version": HOST_WORKER_VERSION,
            "phase": "FINAL_AUDIT",
            "completed_suffix_count": len(completed_sequences),
            "completed_sequences": completed_sequences,
            "stop_requested": False,
        },
        error_message=None,
    )
    state_dir = repo_root / "reports" / "production_us_application_bulk_state"
    audit_path = (
        repo_root
        / "reports"
        / "production_us_application_bulk_host_logs"
        / run_id
        / "batch_final_audit.json"
    )
    try:
        audit = audit_target_bulk_batch(
            master_plan=master,
            batch_manifest=manifest,
            state_dir=state_dir,
        )
        write_target_bulk_batch_audit(audit_path, audit)
    except Exception as exc:
        raise RuntimeError(
            f"batch final audit failed: {type(exc).__name__}: {exc}"
        ) from exc

    return update_target_bulk_task(
        run_id,
        status=STATUS_SUCCESS,
        metrics={
            "worker_version": HOST_WORKER_VERSION,
            "phase": "COMPLETE",
            "completed_suffix_count": len(completed_sequences),
            "completed_sequences": completed_sequences,
            "accepted_target_sequence_count": 2 + len(completed_sequences),
            "remaining_to_accepted_corpus": 310 - (2 + len(completed_sequences)),
            "last_safe_checkpoint_sequence": completed_sequences[-1]
            if completed_sequences
            else 2,
            "final_audit_version": BATCH_FINAL_AUDIT_VERSION,
            "final_audit_path": str(audit_path),
            "final_audit_verified_sequences": audit["verified_sequences"],
            "full_accepted_source_corpus_on_target": bool(
                audit["full_accepted_source_corpus_on_target"]
            ),
            "stop_requested": False,
        },
        error_message=None,
        finish=True,
    )


def execute_claimed_target_bulk_task(task: dict[str, Any]) -> dict[str, Any]:
    repo_root = v1._repo_root()
    claimed_from = str(task.get("claimed_from_status") or "")
    if claimed_from == STATUS_PREPARE_QUEUED:
        return v1.execute_claimed_target_bulk_task(task)
    if claimed_from != STATUS_RUN_QUEUED:
        raise RuntimeError(f"unexpected target bulk host claim status: {claimed_from}")

    try:
        return _run_execution(task, repo_root=repo_root)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        blocked = (
            "guarded child operator failed" in str(exc)
            or "batch final audit failed" in str(exc)
            or "completed-sequence checkpoint" in str(exc)
        )
        status = STATUS_BLOCKED if blocked else STATUS_FAILED
        return update_target_bulk_task(
            str(task["run_id"]),
            status=status,
            metrics={"phase": status, "worker_version": HOST_WORKER_VERSION},
            error_message=message,
            finish=True,
        )


def run_once() -> bool:
    task = claim_next_target_bulk_task()
    if task is None:
        return False
    execute_claimed_target_bulk_task(task)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Windows-host V2 worker for guarded US Application target bulk tasks"
    )
    parser.add_argument("--once", action="store_true", help="claim at most one task and exit")
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("US target bulk host worker must run on Windows")
    if args.poll_seconds < 0.5:
        parser.error("--poll-seconds must be at least 0.5")

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("markorbit.us.target_bulk_host_worker_v2")
    recovery = fail_closed_recover_target_bulk_tasks()
    if any(recovery.values()):
        logger.warning("US target bulk host recovery: %s", recovery)

    if args.once:
        return 0 if run_once() else 1
    while True:
        try:
            if not run_once():
                time.sleep(args.poll_seconds)
        except Exception:
            logger.exception("US target bulk host worker cycle failed")
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
