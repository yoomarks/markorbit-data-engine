from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.cn.goods_lifecycle import ensure_m16_goods_replay_boundary, ensure_m16_goods_schema
from app.cn.package_meta import infer_package_descriptor
from app.cn.preflight_m16_real_data import build_preflight
from app.cn.replay_plan import collect_incoming_packages, evaluate_replay_plan
from app.config import get_settings
from app.db import postgres_conn
from app.jobs import scan_and_ingest_cn
from app.scanner import SUPPORTED_SUFFIXES
from app.version import engine_version


GUARD_VERSION = "CN_M16_GUARDED_ONE_SHOT_V1"


def _registered_partitions() -> list[dict[str, str]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_name, partition_dimension, partition_value
                FROM control.source_package
                WHERE jurisdiction = 'CN'
                ORDER BY package_sequence
                """
            )
            return [dict(row) for row in cur.fetchall()]


def incoming_policy_issues(
    incoming_dir: Path,
    *,
    registered_partitions: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Reject inputs whose precedence cannot be determined safely before scanning."""
    issues: list[dict[str, Any]] = []
    files = sorted(
        path
        for path in incoming_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ) if incoming_dir.exists() else []

    incoming_by_partition: dict[str, list[str]] = defaultdict(list)
    for path in files:
        if path.suffix.lower() != ".zip":
            issues.append(
                {
                    "type": "UNSUPPORTED_M16_CN_SOURCE_SUFFIX",
                    "file_name": path.name,
                    "suffix": path.suffix.lower(),
                }
            )
            continue
        descriptor = infer_package_descriptor(path)
        if descriptor.package_kind == "UNKNOWN":
            issues.append(
                {
                    "type": "UNKNOWN_PACKAGE_PRECEDENCE",
                    "file_name": path.name,
                }
            )
            continue
        key = f"{descriptor.partition_dimension}:{descriptor.partition_value}"
        incoming_by_partition[key].append(path.name)

    for key, names in sorted(incoming_by_partition.items()):
        if len(set(names)) > 1:
            issues.append(
                {
                    "type": "AMBIGUOUS_INCOMING_PARTITION",
                    "partition_key": key,
                    "file_names": sorted(set(names)),
                }
            )

    registered_by_partition: dict[str, set[str]] = defaultdict(set)
    for row in registered_partitions:
        dimension = str(row.get("partition_dimension") or "")
        value = str(row.get("partition_value") or "")
        if dimension and value:
            registered_by_partition[f"{dimension}:{value}"].add(str(row.get("file_name") or ""))

    for key, names in sorted(incoming_by_partition.items()):
        registered_names = registered_by_partition.get(key, set())
        for name in names:
            if registered_names and name not in registered_names:
                issues.append(
                    {
                        "type": "NEW_FILE_FOR_REGISTERED_PARTITION",
                        "partition_key": key,
                        "file_name": name,
                        "registered_file_names": sorted(registered_names),
                    }
                )
    return issues


def build_execution_guard() -> dict[str, Any]:
    settings = get_settings()
    registered = _registered_partitions()
    incoming_dir = settings.raw_data_root / "incoming" / "cn"
    policy_issues = incoming_policy_issues(
        incoming_dir,
        registered_partitions=registered,
    )
    if engine_version() != "M1.6":
        policy_issues.append(
            {
                "type": "UNEXPECTED_ENGINE_VERSION",
                "engine_version": engine_version(),
            }
        )

    if policy_issues:
        return {
            "allowed": False,
            "guard_version": GUARD_VERSION,
            "mode": "INPUT_POLICY_BLOCKED",
            "issues": policy_issues,
        }

    if not registered:
        preflight = build_preflight()
        plan = evaluate_replay_plan(
            collect_incoming_packages(settings.raw_data_root),
            preflight=preflight,
        )
        allowed = (
            preflight.get("status") != "FAIL"
            and bool(preflight.get("safe_to_run_replay_command"))
            and plan.get("status") != "FAIL"
        )
        return {
            "allowed": allowed,
            "guard_version": GUARD_VERSION,
            "mode": "CLEAN_RESET_FIRST_RUN",
            "issues": [] if allowed else [
                {
                    "type": "CLEAN_REPLAY_GATE_FAILED",
                    "preflight_hard_fail_reasons": preflight.get("hard_fail_reasons") or [],
                    "plan_hard_fail_reasons": plan.get("hard_fail_reasons") or [],
                }
            ],
            "preflight": {
                "status": preflight.get("status"),
                "mode": preflight.get("mode"),
                "warning_reasons": preflight.get("warning_reasons") or [],
            },
            "replay_plan": {
                "status": plan.get("status"),
                "package_count": plan.get("package_count"),
                "warning_reasons": plan.get("warning_reasons") or [],
                "expected_processing_order": plan.get("expected_processing_order") or [],
            },
        }

    # Once the first clean scan has registered all incoming packages, registry
    # source_rank freezes their replay order. Subsequent one-shot runs therefore
    # need the runtime/schema boundary, while the ingestion path itself rechecks
    # the advisory lock and each package SHA before publication.
    ensure_m16_goods_schema()
    ensure_m16_goods_replay_boundary()
    return {
        "allowed": True,
        "guard_version": GUARD_VERSION,
        "mode": "REGISTERED_REPLAY_CONTINUATION",
        "issues": [],
        "registered_package_count": len(registered),
    }


def main() -> int:
    try:
        guard = build_execution_guard()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "CN_EXECUTION_GUARD_FAILED",
                    "guard_version": GUARD_VERSION,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 4

    print(
        json.dumps(
            {
                "event": "CN_EXECUTION_GUARD",
                **guard,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        flush=True,
    )
    if not guard.get("allowed"):
        return 4

    try:
        result = scan_and_ingest_cn(trigger_type="MANUAL_GUARDED_WORKER")
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "CN_RUN_FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
    if result.get("ingest", {}).get("busy"):
        return 3
    if result.get("ingest", {}).get("failed"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
