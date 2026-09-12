from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

from app.db import postgres_conn
from app.us import target_bulk_host_worker as v1
from app.us.target_bulk_batch import validate_batch_manifest
from app.us.target_bulk_batch_audit import (
    BATCH_FINAL_AUDIT_VERSION,
    audit_target_bulk_batch,
    write_target_bulk_batch_audit,
)
from app.us.target_bulk_plan import validate_bulk_plan
from app.us.target_bulk_tasks import (
    STATUS_BLOCKED,
    STATUS_SUCCESS,
    TARGET_BULK_DOMAIN,
    TARGET_BULK_EXECUTION_LANE,
    TARGET_BULK_TASK_KIND,
)
from app.us.target_canary import write_receipt


FINAL_AUDIT_RECOVERY_VERSION = "US_APPLICATION_TARGET_BULK_FINAL_AUDIT_RECOVERY_V1"


def _git_clean(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return not result.stdout.strip()


def _load_task(run_id: str) -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, job_type, trigger_type, status, started_at, finished_at,
                       payload, metrics, COALESCE(error_message, '') AS error_message
                FROM control.job_run
                WHERE run_id = %s
                  AND trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = %s
                """,
                (run_id, TARGET_BULK_TASK_KIND, TARGET_BULK_EXECUTION_LANE, TARGET_BULK_DOMAIN),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("US Application target bulk recovery task was not found")
    return dict(row)


def _snapshot(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(task.get("status") or ""),
        "payload": dict(task.get("payload") or {}),
        "metrics": dict(task.get("metrics") or {}),
        "error_message": str(task.get("error_message") or ""),
    }


def _validate_recovery_candidate(
    task: dict[str, Any],
    *,
    master: dict[str, Any],
    manifest: dict[str, Any],
) -> list[int]:
    if str(task.get("status") or "") != STATUS_BLOCKED:
        raise RuntimeError("final-audit recovery requires BLOCKED task state")
    error = str(task.get("error_message") or "")
    if "batch final audit failed" not in error:
        raise RuntimeError("final-audit recovery refuses a non-audit block")

    payload = dict(task.get("payload") or {})
    metrics = dict(task.get("metrics") or {})
    validate_bulk_plan(master)
    validate_batch_manifest(manifest, master_plan=master)

    plan_sha = str(master["plan_sha256"]).lower()
    if str(payload.get("approved_plan_sha256") or "").lower() != plan_sha:
        raise RuntimeError("final-audit recovery approved plan SHA drifted")
    if str(payload.get("plan_sha256") or "").lower() != plan_sha:
        raise RuntimeError("final-audit recovery payload plan SHA drifted")
    if str(metrics.get("plan_sha256") or "").lower() != plan_sha:
        raise RuntimeError("final-audit recovery metrics plan SHA drifted")
    if payload.get("batch_manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("final-audit recovery manifest SHA drifted")
    if metrics.get("batch_manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("final-audit recovery metrics manifest SHA drifted")

    allowed = [int(item["sequence"]) for item in manifest["children"]]
    completed = metrics.get("completed_sequences")
    if completed != allowed:
        raise RuntimeError("final-audit recovery requires every approved child COMPLETE")
    if int(metrics.get("completed_suffix_count") or -1) != len(allowed):
        raise RuntimeError("final-audit recovery completed suffix count drifted")
    if not allowed:
        raise RuntimeError("final-audit recovery approved batch is empty")

    end = allowed[-1]
    required_exact = {
        "current_sequence": end,
        "last_safe_checkpoint_sequence": end,
        "last_archived_source_sequence": end,
    }
    for field, expected in required_exact.items():
        if int(metrics.get(field) or -1) != expected:
            raise RuntimeError(f"final-audit recovery checkpoint drifted: {field}")
    for field in ("current_package_state", "current_canary_state", "child_journal_state"):
        if str(metrics.get(field) or "") != "COMPLETE":
            raise RuntimeError(f"final-audit recovery requires COMPLETE {field}")
    if str(metrics.get("phase") or "") != STATUS_BLOCKED:
        raise RuntimeError("final-audit recovery requires BLOCKED metrics phase")
    return allowed


def _persist_success(
    run_id: str,
    *,
    expected_snapshot: dict[str, Any],
    metrics_patch: dict[str, Any],
) -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, status, payload, metrics, COALESCE(error_message, '') AS error_message
                FROM control.job_run
                WHERE run_id = %s
                  AND trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = %s
                FOR UPDATE
                """,
                (run_id, TARGET_BULK_TASK_KIND, TARGET_BULK_EXECUTION_LANE, TARGET_BULK_DOMAIN),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("final-audit recovery task disappeared before commit")
            current = dict(row)
            if _snapshot(current) != expected_snapshot:
                raise RuntimeError("final-audit recovery task changed during read-only audit")
            cur.execute(
                """
                UPDATE control.job_run
                SET status = %s,
                    metrics = metrics || %s::jsonb,
                    error_message = NULL,
                    finished_at = now()
                WHERE run_id = %s
                RETURNING run_id, job_type, trigger_type, status, started_at,
                          finished_at, payload, metrics, error_message
                """,
                (STATUS_SUCCESS, json.dumps(metrics_patch, ensure_ascii=False), run_id),
            )
            updated = dict(cur.fetchone())
        conn.commit()
    return updated


def recover_final_audit(
    *,
    run_id: str,
    expected_recovery_main: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = (repo_root or v1._repo_root()).resolve()
    recovery_main = v1._git_head(root)
    if recovery_main != expected_recovery_main:
        raise RuntimeError("final-audit recovery main SHA mismatch")
    if not _git_clean(root):
        raise RuntimeError("final-audit recovery requires a clean git worktree")

    task = _load_task(run_id)
    before = _snapshot(task)
    payload = before["payload"]
    plan_path = Path(str(payload.get("plan_path") or ""))
    manifest_path = Path(str(payload.get("batch_manifest_path") or ""))
    master = v1._read_json(plan_path, "final-audit recovery master plan")
    manifest = v1._read_json(manifest_path, "final-audit recovery batch manifest")
    allowed = _validate_recovery_candidate(task, master=master, manifest=manifest)

    state_dir = root / "reports" / "production_us_application_bulk_state"
    log_dir = root / "reports" / "production_us_application_bulk_host_logs" / run_id
    audit_path = log_dir / "batch_final_audit.json"
    recovery_path = log_dir / "batch_final_audit_recovery.json"

    audit = audit_target_bulk_batch(
        master_plan=master,
        batch_manifest=manifest,
        state_dir=state_dir,
    )
    if audit.get("verified_sequences") != list(range(1, 311)):
        raise RuntimeError("final-audit recovery did not verify exact 1..310 sequence coverage")
    if not bool(audit.get("full_accepted_source_corpus_on_target")):
        raise RuntimeError("final-audit recovery did not prove the full accepted corpus")

    write_target_bulk_batch_audit(audit_path, audit)
    receipt = {
        "recovery_version": FINAL_AUDIT_RECOVERY_VERSION,
        "run_id": run_id,
        "recovery_main": recovery_main,
        "original_execution_main": master["execution_main"],
        "master_plan_sha256": master["plan_sha256"],
        "batch_manifest_sha256": manifest["manifest_sha256"],
        "approved_sequences": allowed,
        "verified_sequences": audit["verified_sequences"],
        "full_accepted_source_corpus_on_target": True,
        "target_mutation_performed": False,
        "child_operator_executed": False,
        "audit_path": str(audit_path),
    }
    write_receipt(recovery_path, receipt)

    metrics_patch = {
        "worker_version": "US_APPLICATION_TARGET_BULK_HOST_WORKER_V2",
        "phase": "COMPLETE",
        "completed_suffix_count": len(allowed),
        "completed_sequences": allowed,
        "accepted_target_sequence_count": 310,
        "remaining_to_accepted_corpus": 0,
        "last_safe_checkpoint_sequence": allowed[-1],
        "final_audit_version": BATCH_FINAL_AUDIT_VERSION,
        "final_audit_path": str(audit_path),
        "final_audit_verified_sequences": audit["verified_sequences"],
        "full_accepted_source_corpus_on_target": True,
        "final_audit_recovered_from_blocked": True,
        "final_audit_recovery_version": FINAL_AUDIT_RECOVERY_VERSION,
        "final_audit_recovery_main": recovery_main,
        "final_audit_recovery_receipt_path": str(recovery_path),
        "stop_requested": False,
    }
    updated = _persist_success(
        run_id,
        expected_snapshot=before,
        metrics_patch=metrics_patch,
    )
    return {"task": updated, "audit": audit, "recovery_receipt": receipt}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only recovery of a blocked US bulk final audit")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-recovery-main", required=True)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    result = recover_final_audit(
        run_id=args.run_id,
        expected_recovery_main=args.expected_recovery_main,
    )
    print(json.dumps(result, ensure_ascii=False, default=str, sort_keys=True, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
