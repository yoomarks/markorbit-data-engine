from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import postgres_conn
from app.us.target_bulk_tasks import latest_complete_target_bulk_epoch
from app.us_assignment import ASSIGNMENT_JURISDICTION
from app.us_assignment.corpus_manifest import preflight_manifest
from app.us_assignment.corpus_replay import _build_replay_plan_from_preflight
from app.us_assignment.repository import list_assignment_packages
from app.us_assignment.transition_gate import build_transition_gate

AUTHORITY_VERSION = "US_ASSIGNMENT_PRODUCTION_REPLAY_AUTHORITY_V1"
AUTHORITY_RECEIPT_VERSION = "US_ASSIGNMENT_PRODUCTION_REPLAY_AUTHORITY_RECEIPT_V1"
AUTHORITY_JOB_TYPE = AUTHORITY_VERSION
AUTHORITY_TRIGGER_TYPE = "ADMIN_UI"
AUTHORITY_LOCK_NAME = "markorbit:us-assignment-production-authority"
AUTHORITY_TASK_KIND = "US_ASSIGNMENT_PRODUCTION_REPLAY_AUTHORITY"
_ALLOWED_REPLAY_STATES = {"READY", "RETRY_REQUIRED"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_plan_sha(plan: dict[str, Any]) -> str:
    body = dict(plan)
    body.pop("plan_sha256", None)
    body.pop("required_authority_token", None)
    raw = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _git(args: list[str], root: Path, timeout: int = 45) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _remote_main(root: Path) -> str:
    last = ""
    for delay in (0, 2, 5):
        if delay:
            time.sleep(delay)
        try:
            out = _git(["ls-remote", "origin", "refs/heads/main"], root, timeout=35)
            parts = out.split()
            if (
                len(parts) == 2
                and parts[1] == "refs/heads/main"
                and len(parts[0]) == 40
            ):
                return parts[0].lower()
            last = out
        except Exception as exc:
            last = str(exc)
    raise RuntimeError(f"unable to verify live origin/main: {last}")


def _relative_to_raw(raw_root: Path, path: Path) -> str:
    root = raw_root.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise RuntimeError(
            "Assignment production manifest must be inside RAW_DATA_ROOT"
        ) from exc


def _manifest_evidence(manifest_path: Path, raw_root: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise RuntimeError(f"Assignment manifest missing: {manifest_path}")
    return {
        "relative_path": _relative_to_raw(raw_root, manifest_path),
        "size_bytes": manifest_path.stat().st_size,
        "sha256": sha256_file(manifest_path),
    }


def _source_entries(
    preflight: dict[str, Any],
    raw_root: Path,
) -> tuple[list[dict[str, Any]], int, int]:
    root = raw_root.resolve()
    entries: list[dict[str, Any]] = []
    incoming = 0
    archive = 0
    for item in preflight.get("plan", []):
        path = Path(str(item["path"])).resolve()
        relative = path.relative_to(root).as_posix()
        if relative.startswith("incoming/us_assignment/"):
            incoming += 1
        elif relative.startswith("archive/us_assignment/"):
            archive += 1
        else:
            raise RuntimeError(
                f"Assignment source resolved outside domain directories: {relative}"
            )
        entries.append(
            {
                "manifest_path": str(item["manifest_path"]),
                "file_name": str(item["file_name"]),
                "source_kind": str(item["source_kind"]),
                "effective_date": str(item["effective_date"]),
                "sha256": str(item["sha256"]).lower(),
                "size_bytes": int(item["size_bytes"]),
                "xml_members": list(item.get("xml_members") or []),
            }
        )
    return entries, incoming, archive


def _source_plan_sha(entries: list[dict[str, Any]]) -> str:
    raw = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _assignment_baseline(transition: dict[str, Any]) -> dict[str, Any]:
    readiness = transition.get("assignment_readiness") or {}
    acceptance = readiness.get("acceptance") or {}
    tables = acceptance.get("tables") or {}
    compact_tables = {
        name: {
            "row_count": int(metrics.get("row_count") or 0),
            "source_package_count": int(metrics.get("source_package_count") or 0),
        }
        for name, metrics in sorted(tables.items())
    }
    return {
        "assignment_state": str(transition.get("assignment_state") or ""),
        "acceptance_status": str(acceptance.get("status") or ""),
        "schema": acceptance.get("schema") or {},
        "package_count": int(acceptance.get("package_count") or 0),
        "successful_package_count": int(
            acceptance.get("successful_package_count") or 0
        ),
        "tables": compact_tables,
    }


def _next_action_signature(replay: dict[str, Any]) -> dict[str, Any] | None:
    action = replay.get("next_action")
    if not action:
        return None
    return {
        "manifest_path": str(action.get("manifest_path") or ""),
        "file_name": str(action.get("file_name") or ""),
        "sha256": str(action.get("sha256") or "").lower(),
        "action": str(action.get("action") or ""),
        "registry_status": str(action.get("registry_status") or ""),
        "package_id": (
            str(action.get("package_id")) if action.get("package_id") else None
        ),
    }


def _authority_rows(plan_sha256: str | None = None) -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            if plan_sha256 is None:
                cur.execute(
                    """
                    SELECT run_id, status, payload, metrics, error_message
                    FROM control.job_run
                    WHERE job_type = %s AND status = 'RUNNING'
                    ORDER BY started_at DESC
                    """,
                    (AUTHORITY_JOB_TYPE,),
                )
            else:
                cur.execute(
                    """
                    SELECT run_id, status, payload, metrics, error_message
                    FROM control.job_run
                    WHERE job_type = %s AND payload->>'plan_sha256' = %s
                    ORDER BY started_at DESC
                    """,
                    (AUTHORITY_JOB_TYPE, plan_sha256),
                )
            return [dict(row) for row in cur.fetchall()]


def _start_state(
    raw_root: Path,
    manifest_path: Path,
    expected_history_parts: int,
) -> dict[str, Any]:
    preflight = preflight_manifest(manifest_path, raw_root)
    if not preflight.get("safe"):
        raise RuntimeError(
            f"Assignment source preflight is not READY: {preflight.get('issues')}"
        )
    replay = _build_replay_plan_from_preflight(preflight)
    if replay.get("status") not in _ALLOWED_REPLAY_STATES:
        raise RuntimeError(
            f"Assignment deterministic replay is not actionable: {replay.get('status')}"
        )
    packages = list_assignment_packages()
    transition = build_transition_gate(
        raw_root,
        expected_history_parts=expected_history_parts,
        verify_us_source_files=True,
        verify_assignment_sources=True,
    )
    if transition.get("status") != "ASSIGNMENT_PHASE_UNLOCKED":
        raise RuntimeError(
            f"Assignment phase is not unlocked: {transition.get('status')}"
        )
    epoch = latest_complete_target_bulk_epoch()
    if epoch is None:
        raise RuntimeError("Accepted US Application epoch is missing")
    accepted_count = int(
        (
            ((transition.get("application_gate") or {}).get("us_pipeline") or {}).get(
                "accepted_target_sequence_count"
            )
        )
        or 0
    )
    if accepted_count != int(epoch["checkpoint_sequence"]):
        raise RuntimeError(
            "Application transition checkpoint does not match latest accepted epoch"
        )
    baseline = _assignment_baseline(transition)
    if baseline["package_count"] != len(packages):
        raise RuntimeError("Assignment audit package count drifted from registry")
    entries, incoming, archive = _source_entries(preflight, raw_root)
    remaining = int(replay.get("remaining_count") or 0)
    if remaining < 1 or remaining > len(entries):
        raise RuntimeError("Assignment replay remaining count is invalid")
    return {
        "preflight": preflight,
        "replay": replay,
        "transition": transition,
        "epoch": epoch,
        "baseline": baseline,
        "entries": entries,
        "incoming": incoming,
        "archive": archive,
        "packages": packages,
        "remaining": remaining,
    }


def build_authority_plan(
    *,
    repo_root: Path,
    raw_root: Path,
    manifest_path: Path,
    expected_history_parts: int,
    authority_generation_id: str | None = None,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")
    root = repo_root.resolve()
    if _git(["status", "--porcelain", "--untracked-files=no"], root):
        raise RuntimeError("production authority prepare requires a clean tracked worktree")
    head = _git(["rev-parse", "HEAD"], root).lower()
    if _remote_main(root) != head:
        raise RuntimeError("local HEAD is not live origin/main during authority prepare")
    if _authority_rows():
        raise RuntimeError("another US Assignment production authority run is active")

    generation = (
        str(uuid.uuid4()) if authority_generation_id is None else str(authority_generation_id)
    )
    parsed_generation = uuid.UUID(generation)
    if parsed_generation.version != 4 or str(parsed_generation) != generation.lower():
        raise ValueError("authority_generation_id must be canonical UUIDv4")

    manifest = _manifest_evidence(manifest_path, raw_root)
    state = _start_state(raw_root, manifest_path, expected_history_parts)
    entries = state["entries"]
    epoch = state["epoch"]
    preflight = state["preflight"]
    packages = state["packages"]
    replay = state["replay"]
    replay_status = str(replay["status"])
    resume_failed = replay_status == "RETRY_REQUIRED"
    plan: dict[str, Any] = {
        "version": AUTHORITY_VERSION,
        "authority_generation_id": generation.lower(),
        "execution_main": head,
        "expected_application_history_parts": expected_history_parts,
        "application_epoch": {
            "run_id": str(epoch["run_id"]),
            "plan_sha256": str(epoch["plan_sha256"]),
            "checkpoint_sequence": int(epoch["checkpoint_sequence"]),
        },
        "manifest": manifest,
        "source_plan_sha256": _source_plan_sha(entries),
        "sources": entries,
        "expected_source_count": len(entries),
        "expected_snapshot_packages": int(
            preflight.get("expected_snapshot_packages") or 0
        ),
        "expected_daily_packages": int(preflight.get("expected_daily_packages") or 0),
        "daily_through": preflight.get("daily_through"),
        "expected_registry_count": len(packages),
        "expected_successful_registry_count": sum(
            1 for row in packages if str(row.get("status") or "") == "SUCCESS"
        ),
        "expected_incoming_source_count": state["incoming"],
        "expected_archive_source_count": state["archive"],
        "assignment_baseline": state["baseline"],
        "transition_status": str(state["transition"]["status"]),
        "application_gate_status": str(
            (state["transition"].get("application_gate") or {}).get("status") or ""
        ),
        "start_mode": (
            "INITIAL_EMPTY_START" if not packages else "RESUME_BOUND_STATE"
        ),
        "replay_status": replay_status,
        "remaining_count": state["remaining"],
        "next_action": _next_action_signature(replay),
        "replay_mode": "ALL_REMAINING_PACKAGES",
        "resume_failed": resume_failed,
        "production_mutation_scope": (
            "ASSIGNMENT_SCHEMA_INIT_REGISTER_INGEST_AND_SOURCE_ARCHIVE"
        ),
        "schema_mutation_authorized": True,
        "assignment_fact_mutation_authorized": True,
        "source_archive_mutation_authorized": True,
        "legal_ownership_conclusion": False,
        "semantics": "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION",
    }
    digest = canonical_plan_sha(plan)
    plan["plan_sha256"] = digest
    plan["required_authority_token"] = (
        f"GO #545 US Assignment production replay {digest}"
    )
    return plan


def validate_authority_plan(
    plan: dict[str, Any],
    *,
    raw_root: Path,
    expected_main: str,
    reject_consumed: bool = True,
) -> dict[str, Any]:
    if str(plan.get("version")) != AUTHORITY_VERSION:
        raise RuntimeError("Assignment production authority plan version mismatch")
    digest = canonical_plan_sha(plan)
    if str(plan.get("plan_sha256") or "").lower() != digest:
        raise RuntimeError("Assignment production authority canonical SHA mismatch")
    required = f"GO #545 US Assignment production replay {digest}"
    if str(plan.get("required_authority_token") or "") != required:
        raise RuntimeError("Assignment production authority token binding mismatch")
    if str(plan.get("execution_main") or "").lower() != expected_main.lower():
        raise RuntimeError("Assignment production authority execution main drifted")
    if reject_consumed and _authority_rows(digest):
        raise RuntimeError("Assignment production authority plan was already consumed")
    active = _authority_rows()
    if active:
        raise RuntimeError("another US Assignment production authority run is active")

    manifest_item = plan.get("manifest") or {}
    manifest_path = raw_root.resolve() / str(manifest_item.get("relative_path") or "")
    manifest_path = manifest_path.resolve()
    try:
        manifest_path.relative_to(raw_root.resolve())
    except ValueError as exc:
        raise RuntimeError("bound Assignment manifest escaped RAW_DATA_ROOT") from exc
    if not manifest_path.is_file():
        raise RuntimeError("bound Assignment manifest disappeared")
    if manifest_path.stat().st_size != int(manifest_item.get("size_bytes") or -1):
        raise RuntimeError("bound Assignment manifest size drifted")
    if sha256_file(manifest_path) != str(manifest_item.get("sha256") or "").lower():
        raise RuntimeError("bound Assignment manifest SHA drifted")

    state = _start_state(
        raw_root,
        manifest_path,
        int(plan["expected_application_history_parts"]),
    )
    entries = state["entries"]
    packages = state["packages"]
    replay = state["replay"]
    if entries != list(plan.get("sources") or []):
        raise RuntimeError("Assignment source corpus identity drifted after plan freeze")
    if _source_plan_sha(entries) != str(plan.get("source_plan_sha256") or ""):
        raise RuntimeError("Assignment source-plan SHA drifted after plan freeze")
    if len(entries) != int(plan.get("expected_source_count") or -1):
        raise RuntimeError("Assignment source count drifted after plan freeze")
    if len(packages) != int(plan.get("expected_registry_count") or -1):
        raise RuntimeError("Assignment registry count drifted after plan freeze")
    successful = sum(
        1 for row in packages if str(row.get("status") or "") == "SUCCESS"
    )
    if successful != int(plan.get("expected_successful_registry_count") or -1):
        raise RuntimeError("Assignment successful registry count drifted")
    if state["incoming"] != int(plan.get("expected_incoming_source_count") or -1):
        raise RuntimeError("Assignment incoming source count drifted after plan freeze")
    if state["archive"] != int(plan.get("expected_archive_source_count") or -1):
        raise RuntimeError("Assignment archive source count drifted after plan freeze")
    if state["baseline"] != plan.get("assignment_baseline"):
        raise RuntimeError("Assignment database baseline drifted")
    if str(replay.get("status") or "") != str(plan.get("replay_status") or ""):
        raise RuntimeError("Assignment replay status drifted after plan freeze")
    if int(replay.get("remaining_count") or 0) != int(plan.get("remaining_count") or -1):
        raise RuntimeError("Assignment replay remaining count drifted after plan freeze")
    if _next_action_signature(replay) != plan.get("next_action"):
        raise RuntimeError("Assignment replay next action drifted after plan freeze")
    expected_resume = str(replay.get("status") or "") == "RETRY_REQUIRED"
    if bool(plan.get("resume_failed")) != expected_resume:
        raise RuntimeError("Assignment retry authority binding drifted")

    epoch = state["epoch"]
    bound_epoch = plan.get("application_epoch") or {}
    if str(epoch["run_id"]) != str(bound_epoch.get("run_id") or ""):
        raise RuntimeError("accepted US Application epoch run changed after plan freeze")
    if str(epoch["plan_sha256"]) != str(bound_epoch.get("plan_sha256") or ""):
        raise RuntimeError("accepted US Application epoch plan changed after plan freeze")
    if int(epoch["checkpoint_sequence"]) != int(
        bound_epoch.get("checkpoint_sequence") or 0
    ):
        raise RuntimeError(
            "accepted US Application checkpoint changed after plan freeze"
        )
    if state["transition"].get("status") != plan.get("transition_status"):
        raise RuntimeError("Application to Assignment transition status drifted")
    return {
        "plan_sha256": digest,
        "source_count": len(entries),
        "remaining_count": int(replay["remaining_count"]),
        "resume_failed": expected_resume,
        "application_checkpoint": int(epoch["checkpoint_sequence"]),
    }


def consume_authority_plan(
    plan: dict[str, Any],
    *,
    raw_root: Path,
    expected_main: str,
    authority_token: str,
    connection_factory=postgres_conn,
) -> dict[str, Any]:
    validated = validate_authority_plan(
        plan,
        raw_root=raw_root,
        expected_main=expected_main,
    )
    if authority_token != str(plan["required_authority_token"]):
        raise PermissionError("exact US Assignment production authority token is required")
    plan_sha = str(plan["plan_sha256"])
    payload = {
        "task_kind": AUTHORITY_TASK_KIND,
        "plan_sha256": plan_sha,
        "authority_generation_id": str(plan["authority_generation_id"]),
        "expected_main": str(plan["execution_main"]),
        "manifest_sha256": str((plan.get("manifest") or {}).get("sha256") or ""),
        "source_plan_sha256": str(plan["source_plan_sha256"]),
        "expected_source_count": int(plan["expected_source_count"]),
        "expected_registry_count": int(plan["expected_registry_count"]),
        "remaining_count": int(plan["remaining_count"]),
        "resume_failed": bool(plan["resume_failed"]),
        "production_mutation_authorized": True,
        "mutation_scope": str(plan["production_mutation_scope"]),
        "legal_ownership_conclusion": False,
    }
    metrics = {
        "phase": "AUTHORIZED_FOR_REPLAY",
        "source_count": int(plan["expected_source_count"]),
        "remaining_count": int(plan["remaining_count"]),
        "application_checkpoint": int(
            (plan.get("application_epoch") or {}).get("checkpoint_sequence") or 0
        ),
        "legal_ownership_conclusion": False,
    }
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                (AUTHORITY_LOCK_NAME,),
            )
            cur.execute(
                """
                SELECT run_id
                FROM control.job_run
                WHERE job_type = %s AND payload->>'plan_sha256' = %s
                LIMIT 1
                """,
                (AUTHORITY_JOB_TYPE, plan_sha),
            )
            if cur.fetchone() is not None:
                raise RuntimeError("Assignment authority plan was consumed concurrently")
            cur.execute(
                """
                SELECT run_id
                FROM control.job_run
                WHERE job_type = %s AND status = 'RUNNING'
                LIMIT 1
                """,
                (AUTHORITY_JOB_TYPE,),
            )
            if cur.fetchone() is not None:
                raise RuntimeError("another Assignment authority run became active")
            cur.execute(
                "SELECT count(*) AS count FROM control.source_package WHERE jurisdiction = %s",
                (ASSIGNMENT_JURISDICTION,),
            )
            count = int(cur.fetchone()["count"])
            if count != int(plan["expected_registry_count"]):
                raise RuntimeError("Assignment registry changed before authority commit")
            cur.execute(
                """
                INSERT INTO control.job_run (
                    job_type, trigger_type, status, payload, metrics
                ) VALUES (%s, %s, 'RUNNING', %s::jsonb, %s::jsonb)
                RETURNING run_id
                """,
                (
                    AUTHORITY_JOB_TYPE,
                    AUTHORITY_TRIGGER_TYPE,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(metrics, ensure_ascii=False),
                ),
            )
            run_id = str(cur.fetchone()["run_id"])
        conn.commit()
    return {
        "version": AUTHORITY_RECEIPT_VERSION,
        "decision": "US_ASSIGNMENT_PRODUCTION_REPLAY_AUTHORIZED",
        "run_id": run_id,
        "plan_sha256": plan_sha,
        "authority_generation_id": str(plan["authority_generation_id"]),
        "source_count": int(plan["expected_source_count"]),
        "remaining_count": int(plan["remaining_count"]),
        "resume_failed": bool(plan["resume_failed"]),
        "production_mutation_authorized": True,
        "legal_ownership_conclusion": False,
        **validated,
    }


def validate_active_receipt(
    plan: dict[str, Any],
    receipt: dict[str, Any],
    *,
    connection_factory=postgres_conn,
) -> dict[str, Any]:
    if str(receipt.get("version")) != AUTHORITY_RECEIPT_VERSION:
        raise RuntimeError("Assignment authority receipt version mismatch")
    plan_sha = str(plan.get("plan_sha256") or "")
    if canonical_plan_sha(plan) != plan_sha:
        raise RuntimeError("Assignment authority plan canonical SHA mismatch")
    if str(receipt.get("plan_sha256") or "") != plan_sha:
        raise RuntimeError("Assignment authority receipt plan binding mismatch")
    run_id = str(receipt.get("run_id") or "")
    if not run_id:
        raise RuntimeError("Assignment authority receipt is missing run_id")
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, payload
                FROM control.job_run
                WHERE run_id = %s AND job_type = %s
                """,
                (run_id, AUTHORITY_JOB_TYPE),
            )
            row = cur.fetchone()
    if row is None:
        raise RuntimeError("Assignment authority run disappeared")
    if str(row["status"]) != "RUNNING":
        raise RuntimeError("Assignment authority run is no longer active")
    payload = row["payload"] or {}
    if str(payload.get("plan_sha256") or "") != plan_sha:
        raise RuntimeError("Assignment authority database binding mismatch")
    if bool(payload.get("resume_failed")) != bool(plan.get("resume_failed")):
        raise RuntimeError("Assignment authority retry binding mismatch")
    return {
        "run_id": run_id,
        "plan_sha256": plan_sha,
        "status": "RUNNING",
        "resume_failed": bool(plan.get("resume_failed")),
    }


def finalize_authority(
    plan: dict[str, Any],
    receipt: dict[str, Any],
    *,
    success: bool,
    report_path: str = "",
    error_message: str = "",
    connection_factory=postgres_conn,
) -> dict[str, Any]:
    active = validate_active_receipt(
        plan,
        receipt,
        connection_factory=connection_factory,
    )
    status = "SUCCESS" if success else "FAILED"
    metrics = {
        "phase": "COMPLETE" if success else "FAILED",
        "report_path": report_path,
        "legal_ownership_conclusion": False,
    }
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.job_run
                SET status = %s,
                    finished_at = now(),
                    metrics = metrics || %s::jsonb,
                    error_message = %s
                WHERE run_id = %s AND status = 'RUNNING'
                RETURNING run_id
                """,
                (
                    status,
                    json.dumps(metrics, ensure_ascii=False),
                    None if success else error_message,
                    active["run_id"],
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Assignment authority run could not be finalized")
        conn.commit()
    return {
        "decision": "US_ASSIGNMENT_PRODUCTION_REPLAY_AUTHORITY_FINALIZED",
        "run_id": active["run_id"],
        "plan_sha256": active["plan_sha256"],
        "status": status,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guarded US Assignment production replay authority"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--repo-root", type=Path, required=True)
    prepare.add_argument("--raw-root", type=Path, required=True)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--expected-history-parts", type=int, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--expected-main", required=True)
    validate.add_argument("--raw-root", type=Path, default=None)

    consume = sub.add_parser("consume")
    consume.add_argument("--plan", type=Path, required=True)
    consume.add_argument("--expected-main", required=True)
    consume.add_argument("--authority-token", required=True)
    consume.add_argument("--raw-root", type=Path, default=None)
    consume.add_argument("--output", type=Path, required=True)

    verify = sub.add_parser("verify-receipt")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--plan", type=Path, required=True)
    finalize.add_argument("--receipt", type=Path, required=True)
    finalize.add_argument("--success", action="store_true")
    finalize.add_argument("--report-path", default="")
    finalize.add_argument("--error-message", default="")

    args = parser.parse_args()
    if args.command == "prepare":
        plan = build_authority_plan(
            repo_root=args.repo_root,
            raw_root=args.raw_root,
            manifest_path=args.manifest,
            expected_history_parts=args.expected_history_parts,
        )
        _write_json(args.output, plan)
        print(
            json.dumps(
                {
                    "status": "FROZEN",
                    "plan_sha256": plan["plan_sha256"],
                    "required_authority_token": plan["required_authority_token"],
                    "source_count": plan["expected_source_count"],
                    "remaining_count": plan["remaining_count"],
                    "resume_failed": plan["resume_failed"],
                },
                ensure_ascii=False,
            )
        )
        return 0

    plan = _load_json(args.plan)
    if args.command == "validate":
        result = validate_authority_plan(
            plan,
            raw_root=args.raw_root or get_settings().raw_data_root,
            expected_main=args.expected_main,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "consume":
        receipt = consume_authority_plan(
            plan,
            raw_root=args.raw_root or get_settings().raw_data_root,
            expected_main=args.expected_main,
            authority_token=args.authority_token,
        )
        _write_json(args.output, receipt)
        print(json.dumps(receipt, ensure_ascii=False, default=str))
        return 0

    receipt = _load_json(args.receipt)
    if args.command == "verify-receipt":
        result = validate_active_receipt(plan, receipt)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    result = finalize_authority(
        plan,
        receipt,
        success=bool(args.success),
        report_path=args.report_path,
        error_message=args.error_message,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
