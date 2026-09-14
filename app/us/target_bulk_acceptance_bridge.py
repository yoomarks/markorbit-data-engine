from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from app.db import postgres_conn
from app.us.target_bulk_tasks import (
    TARGET_BULK_ACTION,
    TARGET_BULK_DOMAIN,
    TARGET_BULK_EXECUTION_LANE,
    TARGET_BULK_TASK_KIND,
    TARGET_BULK_TASK_VERSION,
    accepted_target_bulk_epoch_from_row,
    active_target_bulk_task,
    latest_complete_target_bulk_epoch,
)

BRIDGE_VERSION = "US_APPLICATION_TARGET_BULK_ACCEPTANCE_BRIDGE_V1"
BRIDGE_JOB_TYPE = "US_APPLICATION_TARGET_BULK_ACCEPTANCE_BRIDGE_V1"
BRIDGE_TRIGGER_TYPE = "ADMIN_UI"


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
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _git(args: list[str], root: Path, timeout: int = 45) -> str:
    proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, timeout=timeout)
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
            if len(parts) == 2 and parts[1] == "refs/heads/main" and len(parts[0]) == 40:
                return parts[0].lower()
            last = out
        except Exception as exc:
            last = str(exc)
    raise RuntimeError(f"unable to verify live origin/main: {last}")


def _source_layout(raw_root: Path) -> tuple[list[str], list[str]]:
    incoming = sorted(p.name for p in (raw_root / "incoming" / "us").glob("*.zip"))
    archive = sorted(p.name for p in (raw_root / "archive" / "us").glob("*.zip"))
    return incoming, archive


def _evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"evidence file missing: {path}")
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _load_verified_evidence(item: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(item["path"]))
    if not path.is_file():
        raise RuntimeError(f"bound evidence file missing: {path}")
    if path.stat().st_size != int(item["size_bytes"]):
        raise RuntimeError(f"bound evidence size drifted: {path}")
    observed = sha256_file(path)
    if observed != str(item["sha256"]).lower():
        raise RuntimeError(f"bound evidence SHA drifted: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _validate_evidence_payloads(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence = {name: _load_verified_evidence(item) for name, item in plan["evidence"].items()}
    bulk_plan = evidence["bulk_plan"]
    journal = evidence["bulk_journal"]
    audit = evidence["target_audit"]
    closeout_plan = evidence["closeout_plan"]
    closeout = evidence["closeout_receipt"]
    checkpoint = int(plan["accepted_checkpoint_sequence"])
    target_plan_sha = str(plan["target_plan_sha256"])

    if str(bulk_plan.get("plan_sha256")) != target_plan_sha:
        raise RuntimeError("target bulk plan SHA binding drifted")
    if int(bulk_plan.get("accepted_source_count") or 0) != checkpoint:
        raise RuntimeError("target bulk plan accepted source count drifted")
    if int(bulk_plan.get("end_sequence") or 0) != checkpoint:
        raise RuntimeError("target bulk plan end sequence drifted")

    if str(journal.get("state")) != "COMPLETE":
        raise RuntimeError("target bulk journal is not COMPLETE")
    if str(journal.get("plan_sha256")) != target_plan_sha:
        raise RuntimeError("target bulk journal plan binding drifted")
    if int(journal.get("last_completed_sequence") or 0) != checkpoint:
        raise RuntimeError("target bulk journal checkpoint drifted")

    if str(audit.get("audit_version")) != "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V2":
        raise RuntimeError("target audit version drifted")
    if str(audit.get("plan_sha256")) != target_plan_sha:
        raise RuntimeError("target audit plan binding drifted")
    if int(audit.get("accepted_source_count") or 0) != checkpoint:
        raise RuntimeError("target audit accepted source count drifted")
    if not bool(audit.get("full_accepted_source_corpus_on_target")):
        raise RuntimeError("target audit does not prove full accepted corpus")
    if list(audit.get("verified_sequences") or []) != list(range(1, checkpoint + 1)):
        raise RuntimeError("target audit verified sequence coverage drifted")

    if str(closeout.get("decision")) != "US_APPLICATION_SOURCE_ARCHIVE_CLOSEOUT_COMPLETE":
        raise RuntimeError("source closeout receipt is not COMPLETE")
    if int(closeout.get("archive_zip_count") or 0) != checkpoint:
        raise RuntimeError("source closeout archive count drifted")
    incoming_count = closeout.get("incoming_zip_count")
    if incoming_count is None or int(incoming_count) != 0:
        raise RuntimeError("source closeout incoming count drifted")
    if bool(closeout.get("clickhouse_mutation_performed")):
        raise RuntimeError("source closeout unexpectedly claims ClickHouse mutation")
    if not bool(closeout.get("source_archive_mutation_performed")):
        raise RuntimeError("source closeout did not archive sources")
    if str(closeout.get("plan_sha256")) != str(closeout_plan.get("plan_sha256")):
        raise RuntimeError("source closeout plan/receipt binding drifted")

    return evidence


def build_bridge_plan(
    *,
    repo_root: Path,
    raw_root: Path,
    bulk_plan_path: Path,
    bulk_journal_path: Path,
    target_audit_path: Path,
    closeout_plan_path: Path,
    closeout_receipt_path: Path,
    authority_generation_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    head = _git(["rev-parse", "HEAD"], root).lower()
    if _remote_main(root) != head:
        raise RuntimeError("local HEAD is not live origin/main during bridge prepare")
    prior = latest_complete_target_bulk_epoch()
    if prior is None:
        raise RuntimeError("no prior accepted US Application target-bulk epoch is available")
    incoming, archive = _source_layout(raw_root)
    checkpoint = len(incoming) + len(archive)
    generation = str(uuid.uuid4()) if authority_generation_id is None else str(authority_generation_id)
    parsed_generation = uuid.UUID(generation)
    if parsed_generation.version != 4 or str(parsed_generation) != generation.lower():
        raise ValueError("authority_generation_id must be canonical UUIDv4")

    plan: dict[str, Any] = {
        "version": BRIDGE_VERSION,
        "authority_generation_id": generation.lower(),
        "execution_main": head,
        "repo_root": str(root),
        "raw_root": str(raw_root.resolve()),
        "prior_epoch_run_id": str(prior["run_id"]),
        "prior_plan_sha256": str(prior["plan_sha256"]),
        "prior_checkpoint_sequence": int(prior["checkpoint_sequence"]),
        "accepted_checkpoint_sequence": checkpoint,
        "target_plan_sha256": "",
        "expected_archive_zip_count": checkpoint,
        "expected_incoming_zip_count": 0,
        "control_plane_mutation_scope": "INSERT_ONE_ACCEPTED_TARGET_BULK_EPOCH_ONLY",
        "clickhouse_mutation_authorized": False,
        "source_mutation_authorized": False,
        "evidence": {
            "bulk_plan": _evidence(bulk_plan_path),
            "bulk_journal": _evidence(bulk_journal_path),
            "target_audit": _evidence(target_audit_path),
            "closeout_plan": _evidence(closeout_plan_path),
            "closeout_receipt": _evidence(closeout_receipt_path),
        },
    }
    bulk_payload = _load_verified_evidence(plan["evidence"]["bulk_plan"])
    plan["target_plan_sha256"] = str(bulk_payload.get("plan_sha256") or "").lower()
    if len(plan["target_plan_sha256"]) != 64:
        raise RuntimeError("bound target bulk plan is missing plan SHA-256")
    if checkpoint <= int(prior["checkpoint_sequence"]):
        raise RuntimeError("acceptance bridge must advance the accepted checkpoint")
    if incoming or len(archive) != checkpoint:
        raise RuntimeError("source corpus is not fully archived for acceptance bridge")
    _validate_evidence_payloads(plan)
    digest = canonical_plan_sha(plan)
    plan["plan_sha256"] = digest
    plan["required_authority_token"] = f"GO #545 US Application target acceptance bridge {digest}"
    return plan


def validate_bridge_plan(plan: dict[str, Any], *, repo_root: Path | None = None) -> dict[str, Any]:
    if str(plan.get("version")) != BRIDGE_VERSION:
        raise RuntimeError("acceptance bridge plan version mismatch")
    digest = canonical_plan_sha(plan)
    if str(plan.get("plan_sha256") or "").lower() != digest:
        raise RuntimeError("acceptance bridge canonical plan SHA mismatch")
    required = f"GO #545 US Application target acceptance bridge {digest}"
    if str(plan.get("required_authority_token") or "") != required:
        raise RuntimeError("acceptance bridge authority token binding mismatch")
    root = (repo_root or Path(str(plan["repo_root"]))).resolve()
    head = _git(["rev-parse", "HEAD"], root).lower()
    if head != str(plan["execution_main"]).lower() or _remote_main(root) != head:
        raise RuntimeError("acceptance bridge execution main drifted")
    if active_target_bulk_task() is not None:
        raise RuntimeError("active US Application target-bulk task blocks acceptance bridge")
    prior = latest_complete_target_bulk_epoch()
    if prior is None:
        raise RuntimeError("prior accepted US Application epoch disappeared")
    if str(prior["run_id"]) != str(plan["prior_epoch_run_id"]):
        raise RuntimeError("prior accepted epoch changed after bridge freeze")
    if int(prior["checkpoint_sequence"]) != int(plan["prior_checkpoint_sequence"]):
        raise RuntimeError("prior accepted checkpoint changed after bridge freeze")
    if str(prior["plan_sha256"]) != str(plan["prior_plan_sha256"]):
        raise RuntimeError("prior accepted plan changed after bridge freeze")
    incoming, archive = _source_layout(Path(str(plan["raw_root"])))
    if len(incoming) != int(plan["expected_incoming_zip_count"]) or len(archive) != int(plan["expected_archive_zip_count"]):
        raise RuntimeError("US Application source layout drifted after bridge freeze")
    _validate_evidence_payloads(plan)
    return {"plan_sha256": digest, "checkpoint": int(plan["accepted_checkpoint_sequence"])}


def apply_bridge_plan(
    plan: dict[str, Any],
    *,
    authority_token: str,
    connection_factory=postgres_conn,
) -> dict[str, Any]:
    validated = validate_bridge_plan(plan)
    if authority_token != str(plan["required_authority_token"]):
        raise PermissionError("exact acceptance-bridge authority token is required")
    checkpoint = int(plan["accepted_checkpoint_sequence"])
    audit = _load_verified_evidence(plan["evidence"]["target_audit"])
    bridge_sha = str(plan["plan_sha256"])
    payload = {
        "task_kind": TARGET_BULK_TASK_KIND,
        "task_version": TARGET_BULK_TASK_VERSION,
        "domain": TARGET_BULK_DOMAIN,
        "action": TARGET_BULK_ACTION,
        "execution_lane": TARGET_BULK_EXECUTION_LANE,
        "host_phase": "ACCEPTANCE_BRIDGE",
        "approved_plan_sha256": bridge_sha,
        "plan_sha256": bridge_sha,
        "target_plan_sha256": str(plan["target_plan_sha256"]),
        "expected_main": str(plan["execution_main"]),
        "expected_history_parts": 91,
        "start_sequence": int(plan["prior_checkpoint_sequence"]) + 1,
        "end_sequence": checkpoint,
        "production_mutation_authorized": True,
        "acceptance_bridge_version": BRIDGE_VERSION,
        "authority_generation_id": str(plan["authority_generation_id"]),
    }
    metrics = {
        "phase": "COMPLETE",
        "plan_sha256": bridge_sha,
        "target_plan_sha256": str(plan["target_plan_sha256"]),
        "execution_main": str(plan["execution_main"]),
        "corpus_total": checkpoint,
        "last_safe_checkpoint_sequence": checkpoint,
        "accepted_target_sequence_count": checkpoint,
        "last_archived_source_sequence": checkpoint,
        "remaining_to_accepted_corpus": 0,
        "full_accepted_source_corpus_on_target": True,
        "final_audit_version": str(audit.get("audit_version") or ""),
        "final_audit_verified_sequences": list(audit.get("verified_sequences") or []),
        "control_plane_mutation_performed": True,
        "clickhouse_mutation_performed": False,
        "source_mutation_performed": False,
        "bridge_from_checkpoint": int(plan["prior_checkpoint_sequence"]),
        "bridge_to_checkpoint": checkpoint,
    }
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("markorbit:us-application-target-bulk-task-queue",))
            cur.execute(
                """
                SELECT run_id FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND status = ANY(%s)
                LIMIT 1
                """,
                (TARGET_BULK_TASK_KIND, TARGET_BULK_EXECUTION_LANE,
                 ["HOST_PREPARE_QUEUED", "NEEDS_OPERATOR", "HOST_RUN_QUEUED", "RUNNING"]),
            )
            if cur.fetchone() is not None:
                raise RuntimeError("active US Application target-bulk task appeared before bridge commit")
            cur.execute(
                """
                SELECT run_id, status, payload, metrics
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = %s
                  AND payload->>'execution_lane' = %s
                  AND payload->>'domain' = 'US_APPLICATION'
                  AND status = 'SUCCESS'
                ORDER BY finished_at DESC NULLS LAST, started_at DESC, run_id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (TARGET_BULK_TASK_KIND, TARGET_BULK_EXECUTION_LANE),
            )
            latest = cur.fetchone()
            latest_epoch = accepted_target_bulk_epoch_from_row(dict(latest)) if latest is not None else None
            if latest_epoch is None or str(latest_epoch["run_id"]) != str(plan["prior_epoch_run_id"]):
                raise RuntimeError("prior accepted epoch changed before bridge commit")
            if int(latest_epoch["checkpoint_sequence"]) != int(plan["prior_checkpoint_sequence"]):
                raise RuntimeError("prior accepted checkpoint changed before bridge commit")
            if str(latest_epoch["plan_sha256"]) != str(plan["prior_plan_sha256"]):
                raise RuntimeError("prior accepted plan changed before bridge commit")
            locked_incoming, locked_archive = _source_layout(Path(str(plan["raw_root"])))
            if len(locked_incoming) != int(plan["expected_incoming_zip_count"]) or len(locked_archive) != int(plan["expected_archive_zip_count"]):
                raise RuntimeError("US Application source layout drifted before bridge commit")
            _validate_evidence_payloads(plan)
            cur.execute(
                """
                INSERT INTO control.job_run (
                    job_type, trigger_type, status, started_at, finished_at,
                    payload, metrics, error_message
                ) VALUES (%s, %s, 'SUCCESS', now(), now(), %s::jsonb, %s::jsonb, NULL)
                RETURNING run_id
                """,
                (BRIDGE_JOB_TYPE, BRIDGE_TRIGGER_TYPE,
                 json.dumps(payload, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False)),
            )
            run_id = str(cur.fetchone()["run_id"])
        conn.commit()
    return {
        "decision": "US_APPLICATION_TARGET_ACCEPTANCE_BRIDGE_COMPLETE",
        "run_id": run_id,
        "plan_sha256": bridge_sha,
        "accepted_checkpoint_sequence": checkpoint,
        "control_plane_mutation_performed": True,
        "clickhouse_mutation_performed": False,
        "source_mutation_performed": False,
        **validated,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded US Application accepted-epoch bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--repo-root", type=Path, required=True)
    prepare.add_argument("--raw-root", type=Path, required=True)
    prepare.add_argument("--bulk-plan", type=Path, required=True)
    prepare.add_argument("--bulk-journal", type=Path, required=True)
    prepare.add_argument("--target-audit", type=Path, required=True)
    prepare.add_argument("--closeout-plan", type=Path, required=True)
    prepare.add_argument("--closeout-receipt", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--plan", type=Path, required=True)
    apply_cmd.add_argument("--authority-token", required=True)
    apply_cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        plan = build_bridge_plan(
            repo_root=args.repo_root, raw_root=args.raw_root,
            bulk_plan_path=args.bulk_plan, bulk_journal_path=args.bulk_journal,
            target_audit_path=args.target_audit, closeout_plan_path=args.closeout_plan,
            closeout_receipt_path=args.closeout_receipt,
        )
        _write_json(args.output, plan)
        print(json.dumps({"status": "FROZEN", "plan_sha256": plan["plan_sha256"],
                          "required_authority_token": plan["required_authority_token"]}, ensure_ascii=False))
        return 0
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    receipt = apply_bridge_plan(plan, authority_token=args.authority_token)
    _write_json(args.output, receipt)
    print(json.dumps(receipt, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
