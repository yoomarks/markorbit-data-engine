from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.domain import DiscoveredPackage
from app.repository import create_job_run, finish_job_run
from app.scanner import sha256_file
from app.us.ingest import ingest_us_package
from app.us.migrations import US_SCHEMA_VERSION, ensure_us_m1_schema
from app.us.repository import list_us_replay_registry, register_us_package
from app.us.run_guard import recover_interrupted_us_ingestions, us_ingestion_guard
from app.us.source_preflight import build_preflight


REPLAY_EXECUTOR_VERSION = "US_DETERMINISTIC_REPLAY_V1"
RETRYABLE_STATUSES = {"FAILED", "MISSING_FILE", "INTERRUPTED"}
KNOWN_PENDING_STATUSES = {"REGISTERED", "PROCESSING", *RETRYABLE_STATUSES}


def _profile_schema_version(row: dict[str, Any]) -> str:
    profile = row.get("profile") or {}
    if not isinstance(profile, dict):
        return ""
    totals = profile.get("totals") or {}
    if not isinstance(totals, dict):
        return ""
    return str(totals.get("schema_version") or "")


def _profile_source_sha(row: dict[str, Any]) -> str:
    profile = row.get("profile") or {}
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("source_sha256") or "").lower()


def _registry_by_sha(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        digest = str(row.get("sha256") or "").lower()
        grouped.setdefault(digest, []).append(row)
    duplicates = sorted(digest for digest, items in grouped.items() if digest and len(items) > 1)
    return {digest: items[0] for digest, items in grouped.items() if digest}, duplicates


def build_replay_plan(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    registry_rows: list[dict[str, Any]] | None = None,
    source_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    preflight = source_preflight or build_preflight(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
    )
    registry = list_us_replay_registry() if registry_rows is None else list(registry_rows)
    registry_by_sha, duplicate_registry_shas = _registry_by_sha(registry)
    source_shas = {
        str(row.get("sha256") or "").lower()
        for row in preflight.get("replay_plan") or []
        if row.get("sha256")
    }

    blockers: list[str] = []
    details: dict[str, Any] = {}
    if not preflight.get("safe_to_replay"):
        blockers.append("source_preflight_not_safe")
    if duplicate_registry_shas:
        blockers.append("duplicate_registry_sha256")
        details["duplicate_registry_sha256"] = duplicate_registry_shas

    extra_registry = [
        {
            "package_id": str(row.get("package_id") or ""),
            "file_name": str(row.get("file_name") or ""),
            "sha256": str(row.get("sha256") or "").lower(),
            "status": str(row.get("status") or ""),
        }
        for row in registry
        if str(row.get("sha256") or "").lower() not in source_shas
    ]
    if extra_registry:
        blockers.append("registered_us_package_not_in_source_plan")
        details["extra_registry_packages"] = extra_registry

    steps: list[dict[str, Any]] = []
    seen_non_success = False
    registered_ranks: list[tuple[int, int, str]] = []
    identity_mismatches: list[dict[str, Any]] = []
    stale_successes: list[dict[str, Any]] = []
    out_of_order_successes: list[dict[str, Any]] = []
    staging_required: list[dict[str, Any]] = []
    unknown_statuses: list[dict[str, Any]] = []

    for source in preflight.get("replay_plan") or []:
        sequence = int(source["sequence"])
        digest = str(source["sha256"]).lower()
        registry_row = registry_by_sha.get(digest)
        status = "UNREGISTERED" if registry_row is None else str(registry_row.get("status") or "")

        if registry_row is not None:
            if (
                str(registry_row.get("package_kind") or "") != str(source["package_kind"])
                or str(registry_row.get("partition_value") or "")
                != str(source["partition_value"])
            ):
                identity_mismatches.append(
                    {
                        "sequence": sequence,
                        "sha256": digest,
                        "source_package_kind": source["package_kind"],
                        "source_partition_value": source["partition_value"],
                        "registry_package_kind": registry_row.get("package_kind"),
                        "registry_partition_value": registry_row.get("partition_value"),
                    }
                )
            registered_ranks.append(
                (sequence, int(registry_row.get("source_rank") or 0), digest)
            )

        if status == "SUCCESS":
            if seen_non_success:
                out_of_order_successes.append(
                    {
                        "sequence": sequence,
                        "file_name": source["file_name"],
                        "sha256": digest,
                    }
                )
            if registry_row is not None and (
                _profile_schema_version(registry_row) != US_SCHEMA_VERSION
                or _profile_source_sha(registry_row) != digest
            ):
                stale_successes.append(
                    {
                        "sequence": sequence,
                        "package_id": str(registry_row.get("package_id") or ""),
                        "file_name": source["file_name"],
                        "profile_schema_version": _profile_schema_version(registry_row),
                        "profile_source_sha256": _profile_source_sha(registry_row),
                        "expected_schema_version": US_SCHEMA_VERSION,
                        "expected_sha256": digest,
                    }
                )
            action = "SKIP_SUCCESS"
        else:
            seen_non_success = True
            if source.get("location") != "incoming":
                staging_required.append(
                    {
                        "sequence": sequence,
                        "file_name": source["file_name"],
                        "path": source["path"],
                        "status": status,
                    }
                )
            if status == "UNREGISTERED":
                action = "REGISTER_AND_INGEST"
            elif status == "REGISTERED":
                action = "INGEST"
            elif status in RETRYABLE_STATUSES:
                action = "RETRY_FULL_PACKAGE"
            elif status == "PROCESSING":
                action = "RECOVER_AND_RETRY"
            else:
                action = "BLOCK_UNKNOWN_STATUS"
                unknown_statuses.append(
                    {
                        "sequence": sequence,
                        "file_name": source["file_name"],
                        "status": status,
                    }
                )

        steps.append(
            {
                "sequence": sequence,
                "package_kind": source["package_kind"],
                "partition_value": source["partition_value"],
                "file_name": source["file_name"],
                "path": source["path"],
                "location": source["location"],
                "sha256": digest,
                "registry_package_id": (
                    str(registry_row.get("package_id")) if registry_row is not None else None
                ),
                "registry_status": status,
                "source_rank": (
                    int(registry_row.get("source_rank") or 0) if registry_row is not None else None
                ),
                "action": action,
            }
        )

    if identity_mismatches:
        blockers.append("registry_source_identity_mismatch")
        details["registry_source_identity_mismatches"] = identity_mismatches
    if stale_successes:
        blockers.append("successful_package_requires_m13_replay")
        details["stale_successes"] = stale_successes
    if out_of_order_successes:
        blockers.append("out_of_order_success_package")
        details["out_of_order_successes"] = out_of_order_successes
    if staging_required:
        blockers.append("pending_source_requires_archive_staging")
        details["staging_required"] = staging_required
    if unknown_statuses:
        blockers.append("unknown_registry_status")
        details["unknown_registry_statuses"] = unknown_statuses

    rank_order_violations: list[dict[str, Any]] = []
    last_rank: int | None = None
    last_sequence: int | None = None
    for sequence, rank, digest in sorted(registered_ranks):
        if rank <= 0 or (last_rank is not None and rank <= last_rank):
            rank_order_violations.append(
                {
                    "sequence": sequence,
                    "source_rank": rank,
                    "sha256": digest,
                    "previous_sequence": last_sequence,
                    "previous_source_rank": last_rank,
                }
            )
        last_rank = rank
        last_sequence = sequence
    if rank_order_violations:
        blockers.append("registered_source_rank_order_violation")
        details["source_rank_order_violations"] = rank_order_violations

    blockers = list(dict.fromkeys(blockers))
    unfinished = [step for step in steps if step["action"] != "SKIP_SUCCESS"]
    if blockers:
        status = "BLOCKED"
    elif not unfinished:
        status = "COMPLETE"
    else:
        status = "READY"

    return {
        "status": status,
        "executor_version": REPLAY_EXECUTOR_VERSION,
        "safe_to_execute": status in {"READY", "COMPLETE"},
        "expected_history_parts": expected_history_parts,
        "preflight_status": preflight.get("status"),
        "preflight": preflight,
        "registry_package_count": len(registry),
        "step_count": len(steps),
        "success_prefix_count": len(steps) - len(unfinished),
        "remaining_count": len(unfinished),
        "next_step": unfinished[0] if unfinished else None,
        "steps": steps,
        "blockers": blockers,
        "blocker_details": details,
        "acceptance_required_after_complete": True,
        "policy_note": (
            "Replay order comes only from the source preflight plan. Successful packages must form "
            "a strict prefix; later successes cannot skip an earlier unfinished package. Pending "
            "archive-only sources must be staged explicitly before execution."
        ),
    }


def _discovered_package(step: dict[str, Any]) -> DiscoveredPackage:
    path = Path(str(step["path"]))
    if not path.is_file():
        raise RuntimeError(f"Planned USPTO source is missing: {path}")
    expected_sha = str(step["sha256"]).lower()
    actual_sha = sha256_file(path).lower()
    if actual_sha != expected_sha:
        raise RuntimeError(
            f"Planned USPTO source changed after replay planning: {path.name}: "
            f"expected={expected_sha} actual={actual_sha}"
        )
    stat = path.stat()
    return DiscoveredPackage(
        jurisdiction="US",
        path=path,
        file_name=path.name,
        file_size=stat.st_size,
        sha256=actual_sha,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )


def execute_replay(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    max_packages: int | None = 1,
    trigger_type: str = "MANUAL_US_DETERMINISTIC_REPLAY",
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")
    if max_packages is not None and max_packages < 1:
        raise ValueError("max_packages must be at least 1 or None")

    result: dict[str, Any] = {
        "status": "UNKNOWN",
        "executor_version": REPLAY_EXECUTOR_VERSION,
        "processed": [],
        "processed_count": 0,
        "recovered_interrupted": [],
        "busy": False,
        "acceptance_required_after_complete": True,
        "source_preflight_runs": 0,
    }

    with us_ingestion_guard() as acquired:
        if not acquired:
            return {**result, "status": "BUSY", "busy": True}

        ensure_us_m1_schema()
        result["recovered_interrupted"] = recover_interrupted_us_ingestions()

        # The source preflight hashes and structurally inspects the complete corpus.
        # Freeze it once per process so an N-package replay does not become O(N^2)
        # full-corpus I/O. Every selected package is still SHA-verified again by
        # _discovered_package immediately before ingestion. A restarted executor
        # performs a fresh complete preflight before any new mutation.
        source_preflight = build_preflight(
            raw_root,
            expected_history_parts=expected_history_parts,
            deep_source_test=deep_source_test,
        )
        result["source_preflight_runs"] = 1
        initial = build_replay_plan(
            raw_root,
            expected_history_parts=expected_history_parts,
            deep_source_test=deep_source_test,
            source_preflight=source_preflight,
        )
        result["initial_plan"] = initial
        if initial["status"] == "BLOCKED":
            return {**result, "status": "BLOCKED", "final_plan": initial}
        if initial["status"] == "COMPLETE":
            return {**result, "status": "COMPLETE", "final_plan": initial}

        run_id = create_job_run(
            job_type="US_DETERMINISTIC_REPLAY",
            trigger_type=trigger_type,
            payload={
                "executor_version": REPLAY_EXECUTOR_VERSION,
                "expected_history_parts": expected_history_parts,
                "deep_source_test": deep_source_test,
                "max_packages": max_packages,
                "source_preflight_runs": 1,
            },
        )
        try:
            while max_packages is None or result["processed_count"] < max_packages:
                plan = build_replay_plan(
                    raw_root,
                    expected_history_parts=expected_history_parts,
                    deep_source_test=deep_source_test,
                    source_preflight=source_preflight,
                )
                if plan["status"] == "BLOCKED":
                    result["status"] = "BLOCKED"
                    result["final_plan"] = plan
                    finish_job_run(
                        run_id,
                        "FAILED",
                        metrics={
                            "processed_count": result["processed_count"],
                            "status": "BLOCKED",
                            "blockers": plan["blockers"],
                        },
                        error_message=f"Replay blocked: {plan['blockers']}",
                    )
                    return result
                if plan["status"] == "COMPLETE":
                    result["status"] = "COMPLETE"
                    result["final_plan"] = plan
                    break

                step = dict(plan["next_step"])
                package_id = step.get("registry_package_id")
                retrying = step["registry_status"] in RETRYABLE_STATUSES
                source = _discovered_package(step)

                if step["action"] == "REGISTER_AND_INGEST":
                    package_id, _inserted = register_us_package(source)
                    retrying = False
                elif step["action"] == "RECOVER_AND_RETRY":
                    # PROCESSING rows should have been converted to INTERRUPTED under the lock.
                    result["status"] = "BLOCKED"
                    result["error"] = "PROCESSING package remained after interrupted recovery"
                    result["final_plan"] = plan
                    finish_job_run(
                        run_id,
                        "FAILED",
                        metrics={"processed_count": result["processed_count"]},
                        error_message=result["error"],
                    )
                    return result

                if not package_id:
                    raise RuntimeError(
                        f"Replay step has no registered package id: {step['file_name']}"
                    )

                try:
                    metrics = ingest_us_package(
                        str(package_id),
                        source.path,
                        raw_root,
                        trigger_type=trigger_type,
                        retrying=retrying,
                    )
                except Exception as exc:
                    result["status"] = "FAILED"
                    result["error"] = str(exc)
                    result["failed_step"] = step
                    result["final_plan"] = build_replay_plan(
                        raw_root,
                        expected_history_parts=expected_history_parts,
                        deep_source_test=deep_source_test,
                        source_preflight=source_preflight,
                    )
                    finish_job_run(
                        run_id,
                        "FAILED",
                        metrics={
                            "processed_count": result["processed_count"],
                            "failed_sequence": step["sequence"],
                            "failed_file_name": step["file_name"],
                        },
                        error_message=str(exc),
                    )
                    return result

                result["processed"].append(
                    {
                        "sequence": step["sequence"],
                        "package_id": str(package_id),
                        "file_name": step["file_name"],
                        "sha256": step["sha256"],
                        "retrying": retrying,
                        "metrics": metrics,
                    }
                )
                result["processed_count"] += 1

            if "final_plan" not in result:
                result["final_plan"] = build_replay_plan(
                    raw_root,
                    expected_history_parts=expected_history_parts,
                    deep_source_test=deep_source_test,
                    source_preflight=source_preflight,
                )
            if result["status"] == "UNKNOWN":
                result["status"] = (
                    "COMPLETE"
                    if result["final_plan"]["status"] == "COMPLETE"
                    else "PAUSED"
                )
            finish_job_run(
                run_id,
                "SUCCESS",
                metrics={
                    "processed_count": result["processed_count"],
                    "status": result["status"],
                    "remaining_count": result["final_plan"].get("remaining_count"),
                    "source_preflight_runs": result["source_preflight_runs"],
                },
            )
            return result
        except Exception as exc:
            finish_job_run(
                run_id,
                "FAILED",
                metrics={"processed_count": result["processed_count"]},
                error_message=str(exc),
            )
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic stop-on-failure USPTO replay executor"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument("--max-packages", type=int, default=1)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process the full remaining deterministic plan, stopping on the first failure.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate registry/fact state. Without this flag only the replay plan is printed.",
    )
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")
    if args.max_packages < 1:
        parser.error("--max-packages must be at least 1")

    raw_root = get_settings().raw_data_root
    report = (
        execute_replay(
            raw_root,
            expected_history_parts=args.expected_history_parts,
            deep_source_test=args.deep_source_test,
            max_packages=None if args.all else args.max_packages,
        )
        if args.apply
        else build_replay_plan(
            raw_root,
            expected_history_parts=args.expected_history_parts,
            deep_source_test=args.deep_source_test,
        )
    )
    report["mode"] = "APPLY" if args.apply else "DRY_RUN"
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
