from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from app.us.target_bulk_batch import (
    derive_batch_manifest,
    validate_batch_manifest,
    write_batch_manifest,
)
from app.us.target_bulk_journal import load_bulk_journal
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


HOST_WORKER_VERSION = "US_APPLICATION_TARGET_BULK_HOST_WORKER_V1"
POLL_SECONDS = 2.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object")
    return payload


def _git_head(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {completed.stderr.strip()}")
    value = completed.stdout.strip().lower()
    if len(value) != 40:
        raise RuntimeError("git HEAD is not a 40-character commit SHA")
    return value


def _parse_kv_output(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def _run_plan_operator(task: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    payload = dict(task.get("payload") or {})
    run_id = str(task["run_id"])
    expected_main = _git_head(repo_root)
    script = repo_root / "scripts" / "plan-production-us-application-bulk-replay.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ExpectedMain",
        expected_main,
        "-StartSequence",
        str(int(payload.get("start_sequence") or 3)),
    ]
    end_sequence = payload.get("end_sequence")
    max_packages = payload.get("max_packages")
    if end_sequence is not None:
        command.extend(["-EndSequence", str(int(end_sequence))])
    elif max_packages is not None:
        command.extend(["-MaxPackages", str(int(max_packages))])
    else:
        raise RuntimeError("queued host task has no explicit bulk bound")

    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    fields = _parse_kv_output(output)
    if completed.returncode != 0 or fields.get("decision") != "US_APPLICATION_TARGET_BULK_PLAN_FROZEN":
        raise RuntimeError(
            "read-only US target bulk plan operator failed: "
            f"exit={completed.returncode} output={output[-4000:]}"
        )

    plan_path = Path(fields["plan_path"])
    plan = _read_json(plan_path, "US target bulk master plan")
    validate_bulk_plan(plan)
    if plan["execution_main"] != expected_main:
        raise RuntimeError("prepared master plan execution main drifted")
    if plan["plan_sha256"] != fields.get("plan_sha256"):
        raise RuntimeError("prepared master plan SHA disagrees with operator output")

    child_dir = plan_path.parent / "children"
    manifest = derive_batch_manifest(plan, output_dir=child_dir)
    manifest_path = plan_path.parent / "batch_manifest.json"
    write_batch_manifest(manifest_path, manifest)
    validate_batch_manifest(manifest, master_plan=plan)

    metrics = {
        "worker_version": HOST_WORKER_VERSION,
        "phase": "NEEDS_OPERATOR",
        "plan_sha256": plan["plan_sha256"],
        "inventory_sha256": plan["inventory_sha256"],
        "batch_manifest_sha256": manifest["manifest_sha256"],
        "execution_main": expected_main,
        "start_sequence": int(plan["start_sequence"]),
        "end_sequence": int(plan["end_sequence"]),
        "suffix_package_count": int(plan["suffix_package_count"]),
        "child_count": int(manifest["child_count"]),
        "corpus_total": 310,
        "accepted_existing_target_sequence": 2,
        "package1_target_bridge_required": True,
        "completed_suffix_count": 0,
        "completed_sequences": [],
        "production_mutation_authorized": False,
    }
    payload_patch = {
        "host_phase": "AWAITING_APPROVAL",
        "expected_main": expected_main,
        "plan_path": str(plan_path),
        "plan_sha256": plan["plan_sha256"],
        "batch_manifest_path": str(manifest_path),
        "batch_manifest_sha256": manifest["manifest_sha256"],
        "production_mutation_authorized": False,
    }
    return update_target_bulk_task(
        run_id,
        status=STATUS_NEEDS_OPERATOR,
        payload_patch=payload_patch,
        metrics=metrics,
        error_message=None,
    )


def _child_progress(
    *,
    repo_root: Path,
    child: dict[str, Any],
    child_plan: dict[str, Any],
) -> dict[str, Any]:
    journal_path = (
        repo_root
        / "reports"
        / "production_us_application_bulk_state"
        / f"bulk_{child['plan_sha256']}.journal.json"
    )
    if not journal_path.is_file():
        return {
            "child_journal_state": "NOT_STARTED",
            "current_package_state": "PENDING",
            "current_canary_state": "NOT_STARTED",
        }
    journal = load_bulk_journal(journal_path, plan=child_plan)
    sequence = int(child["sequence"])
    state = dict(journal["packages"].get(str(sequence)) or {})
    canary_state = "NOT_STARTED"
    canary_path = Path(str(state.get("canary_journal_path") or ""))
    if canary_path.is_file():
        try:
            raw = _read_json(canary_path, "current package canary journal")
            canary_state = str(raw.get("state") or "UNKNOWN")
        except RuntimeError:
            canary_state = "UNREADABLE"
    return {
        "child_journal_state": str(journal.get("state") or "UNKNOWN"),
        "current_package_state": str(state.get("status") or "PENDING"),
        "current_canary_state": canary_state,
    }


def _run_child_operator(
    *,
    task: dict[str, Any],
    repo_root: Path,
    child: dict[str, Any],
    child_plan: dict[str, Any],
    completed_sequences: list[int],
) -> None:
    run_id = str(task["run_id"])
    log_dir = repo_root / "reports" / "production_us_application_bulk_host_logs" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"child_{int(child['sequence']):03d}.log"
    script = repo_root / "scripts" / "run-production-us-application-bulk-replay.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ExpectedMain",
        str(child_plan["execution_main"]),
        "-PlanPath",
        str(child["plan_path"]),
        "-Authority",
        str(child["required_authority_token"]),
    ]

    monitor_error: str | None = None
    with log_path.open("w", encoding="utf-8") as log_stream:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
        while process.poll() is None:
            try:
                progress = _child_progress(
                    repo_root=repo_root,
                    child=child,
                    child_plan=child_plan,
                )
                stop_pending = target_bulk_stop_requested(run_id)
                update_target_bulk_task(
                    run_id,
                    metrics={
                        "phase": "STOP_PENDING" if stop_pending else "EXECUTING",
                        "current_sequence": int(child["sequence"]),
                        "current_file": child_plan["packages"][1]["file_name"],
                        "completed_suffix_count": len(completed_sequences),
                        "completed_sequences": completed_sequences,
                        "stop_requested": stop_pending,
                        **progress,
                    },
                    error_message=(
                        "Stop requested; current package is allowed to reach its durable boundary."
                        if stop_pending
                        else None
                    ),
                )
                monitor_error = None
            except Exception as exc:
                # Never abandon a production child merely because progress telemetry or
                # the control database is temporarily unavailable. The guarded child
                # owns the mutation lock and must be allowed to reach its durable exit.
                monitor_error = f"{type(exc).__name__}: {exc}"
            time.sleep(POLL_SECONDS)
        return_code = int(process.returncode or 0)

    if return_code != 0:
        try:
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
        except OSError:
            log_tail = "<host child log unreadable>"
        raise RuntimeError(
            f"guarded child operator failed: sequence={child['sequence']} "
            f"exit={return_code} log_tail={log_tail}"
        )
    if monitor_error:
        update_target_bulk_task(
            run_id,
            metrics={
                "phase": "CHILD_COMPLETE_TELEMETRY_RECOVERED",
                "current_sequence": int(child["sequence"]),
                "monitor_error": monitor_error,
            },
            error_message=None,
        )


def _run_execution(task: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    payload = dict(task.get("payload") or {})
    metrics = dict(task.get("metrics") or {})
    run_id = str(task["run_id"])
    plan_path = Path(str(payload.get("plan_path") or ""))
    manifest_path = Path(str(payload.get("batch_manifest_path") or ""))
    master = _read_json(plan_path, "approved US target bulk master plan")
    manifest = _read_json(manifest_path, "approved US target bulk batch manifest")
    validate_bulk_plan(master)
    validate_batch_manifest(manifest, master_plan=master)

    approved = str(payload.get("approved_plan_sha256") or "").lower()
    if approved != str(master["plan_sha256"]).lower():
        raise RuntimeError("host execution does not have approval for the exact master plan SHA")
    if payload.get("batch_manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("host execution batch manifest SHA binding drifted")
    if _git_head(repo_root) != master["execution_main"]:
        raise RuntimeError("host execution main changed after operator approved the frozen plan")

    completed_sequences = [int(item) for item in metrics.get("completed_sequences") or []]
    allowed_sequences = [int(item["sequence"]) for item in manifest["children"]]
    if any(item not in allowed_sequences for item in completed_sequences):
        raise RuntimeError("host task completed-sequence checkpoint escaped the approved manifest")

    for child in manifest["children"]:
        sequence = int(child["sequence"])
        if sequence in completed_sequences:
            continue
        if target_bulk_stop_requested(run_id):
            return update_target_bulk_task(
                run_id,
                status=STATUS_INTERRUPTED,
                metrics={
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
        child_plan = _read_json(child_path, f"child plan {sequence}")
        validate_bulk_plan(child_plan)
        if child_plan["plan_sha256"] != child["plan_sha256"]:
            raise RuntimeError(f"child plan file SHA binding drifted: {sequence}")

        update_target_bulk_task(
            run_id,
            metrics={
                "phase": "EXECUTING",
                "current_sequence": sequence,
                "current_file": child_plan["packages"][1]["file_name"],
                "completed_suffix_count": len(completed_sequences),
                "completed_sequences": completed_sequences,
                "stop_requested": False,
            },
            error_message=None,
        )
        _run_child_operator(
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

    return update_target_bulk_task(
        run_id,
        status=STATUS_SUCCESS,
        metrics={
            "phase": "COMPLETE",
            "completed_suffix_count": len(completed_sequences),
            "completed_sequences": completed_sequences,
            "accepted_target_sequence_count": 2 + len(completed_sequences),
            "remaining_to_accepted_corpus": 310 - (2 + len(completed_sequences)),
            "last_safe_checkpoint_sequence": completed_sequences[-1] if completed_sequences else 2,
            "stop_requested": False,
        },
        error_message=None,
        finish=True,
    )


def execute_claimed_target_bulk_task(task: dict[str, Any]) -> dict[str, Any]:
    repo_root = _repo_root()
    claimed_from = str(task.get("claimed_from_status") or "")
    if claimed_from == STATUS_PREPARE_QUEUED:
        try:
            return _run_plan_operator(task, repo_root=repo_root)
        except Exception as exc:
            return update_target_bulk_task(
                str(task["run_id"]),
                status=STATUS_BLOCKED,
                metrics={"phase": "PREPARE_BLOCKED", "worker_version": HOST_WORKER_VERSION},
                error_message=f"{type(exc).__name__}: {exc}",
                finish=True,
            )
    if claimed_from != STATUS_RUN_QUEUED:
        raise RuntimeError(f"unexpected target bulk host claim status: {claimed_from}")

    try:
        # The actual `target_bulk_cli execute` child owns the global mutation lock.
        # Keeping the lock in the mutating process prevents an orchestrator crash
        # from releasing the lock while a PowerShell/Python child is still writing.
        return _run_execution(task, repo_root=repo_root)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        status = STATUS_BLOCKED if "guarded child operator failed" in str(exc) else STATUS_FAILED
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
        description="Windows-host worker for guarded US Application target bulk tasks"
    )
    parser.add_argument("--once", action="store_true", help="claim at most one task and exit")
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("US target bulk host worker must run on Windows")
    if args.poll_seconds < 0.5:
        parser.error("--poll-seconds must be at least 0.5")

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("markorbit.us.target_bulk_host_worker")
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
