from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from app.cn.audit_acceptance import build_acceptance_audit
from app.cn.replay_readiness import build_readiness
from app.db import clickhouse_execution_settings


CHECKPOINT_VERSION = "CN_M16_FINAL_CHECKPOINT_V1"
_PASS_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}


def evaluate_final_checkpoint(
    *,
    readiness: dict[str, Any],
    acceptance: dict[str, Any] | None,
) -> dict[str, Any]:
    readiness_status = str(readiness.get("status") or "UNKNOWN")
    hard_issues = list(readiness.get("hard_issues") or [])
    retry_issues = list(readiness.get("retry_issues") or [])

    if readiness_status != "COMPLETE":
        if readiness_status in {"BLOCKED", "RETRY_REQUIRED"}:
            status = "BLOCKED"
        else:
            status = "NOT_READY"
        reasons: list[dict[str, Any]] = []
        reasons.extend(hard_issues)
        reasons.extend(retry_issues)
        if not reasons:
            reasons.append(
                {
                    "code": "CN_REPLAY_NOT_COMPLETE",
                    "readiness_status": readiness_status,
                }
            )
        return {
            "checkpoint_version": CHECKPOINT_VERSION,
            "read_only": True,
            "status": status,
            "ready_for_next_domain": False,
            "reasons": reasons,
            "readiness": readiness,
            "acceptance": None,
            "acceptance_executed": False,
        }

    if acceptance is None:
        return {
            "checkpoint_version": CHECKPOINT_VERSION,
            "read_only": True,
            "status": "FAIL",
            "ready_for_next_domain": False,
            "reasons": [{"code": "ACCEPTANCE_REPORT_MISSING"}],
            "readiness": readiness,
            "acceptance": None,
            "acceptance_executed": False,
        }

    acceptance_status = str(acceptance.get("status") or "UNKNOWN")
    if acceptance_status == "PASS":
        status = "PASS"
    elif acceptance_status == "PASS_WITH_WARNINGS":
        status = "PASS_WITH_WARNINGS"
    elif acceptance_status == "NOT_READY":
        status = "NOT_READY"
    else:
        status = "FAIL"

    reasons: list[dict[str, Any]] = []
    for code in acceptance.get("hard_fail_reasons") or []:
        reasons.append({"code": str(code), "source": "acceptance_hard_fail"})
    for code in acceptance.get("not_ready_reasons") or []:
        reasons.append({"code": str(code), "source": "acceptance_not_ready"})

    packages = readiness.get("packages") or {}
    storage = readiness.get("storage_v2") or {}
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "read_only": True,
        "status": status,
        "ready_for_next_domain": status in _PASS_STATUSES,
        "reasons": reasons,
        "summary": {
            "registered_package_count": int(
                packages.get("registered_package_count") or 0
            ),
            "package_status_counts": packages.get("status_counts") or {},
            "active_clickhouse_bytes": int(storage.get("active_bytes") or 0),
            "active_clickhouse_rows": int(storage.get("active_rows") or 0),
            "goods_baseline_history_rows": int(
                storage.get("goods_baseline_history_rows") or 0
            ),
            "reconstructible_event_baseline_rows": int(
                storage.get("reconstructible_event_baseline_rows") or 0
            ),
            "legacy_party_history_rows": int(
                storage.get("legacy_party_history_rows") or 0
            ),
            "active_stage_rows": int(storage.get("active_stage_rows") or 0),
            "storage_v2_shadow_tables": storage.get("storage_v2_shadow_tables") or [],
            "pending_mutations": storage.get("pending_mutations") or [],
            "acceptance_status": acceptance_status,
            "acceptance_warning_reasons": acceptance.get("warning_reasons") or [],
        },
        "readiness": readiness,
        "acceptance": acceptance,
        "acceptance_executed": True,
    }


def build_final_checkpoint(
    *,
    persistent_worker_running: bool = False,
    readiness_builder: Callable[..., dict[str, Any]] = build_readiness,
    acceptance_builder: Callable[[], dict[str, Any]] = build_acceptance_audit,
) -> dict[str, Any]:
    """Build the final CN gate without mutating PostgreSQL or ClickHouse.

    The expensive M1.6 acceptance audit is deliberately short-circuited until
    replay readiness reports COMPLETE. This makes the checkpoint safe to call
    while a corpus replay is still pending or blocked without repeatedly
    scanning the full raw/fact corpus.
    """
    readiness = readiness_builder(
        persistent_worker_running=persistent_worker_running
    )
    if readiness.get("status") != "COMPLETE":
        return evaluate_final_checkpoint(readiness=readiness, acceptance=None)

    # The acceptance audit contains full-corpus orphan checks. On the retained CN
    # corpus the default hash JOIN can materialize a >12 GiB right side and exceed
    # the target host's ~14 GiB ClickHouse ceiling. Reuse the same disk-spilling
    # execution profile already proven by CN ingestion. These settings affect only
    # execution resources; query semantics and the read-only acceptance contract
    # remain unchanged.
    with clickhouse_execution_settings(
        join_algorithm="grace_hash",
        grace_hash_join_initial_buckets=32,
        send_receive_timeout=3600,
    ):
        acceptance = acceptance_builder()
    return evaluate_final_checkpoint(readiness=readiness, acceptance=acceptance)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only CN M1.6 final replay/storage/integrity checkpoint"
    )
    parser.add_argument(
        "--persistent-worker-running",
        action="store_true",
        help="Report that the persistent docker compose worker is running.",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = build_final_checkpoint(
        persistent_worker_running=args.persistent_worker_running
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    if report["status"] in _PASS_STATUSES:
        return 0
    if report["status"] == "NOT_READY":
        return 3
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
