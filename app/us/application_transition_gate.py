from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.cn.serving_state_checkpoint import (
    CHECKPOINT_VERSION as CN_SERVING_CHECKPOINT_VERSION,
    build_serving_state_checkpoint,
)
from app.config import get_settings
from app.us.pipeline_readiness import build_readiness as build_us_readiness
from app.us.source_preflight import build_preflight as build_us_source_preflight
from app.us.target_bulk_tasks import latest_complete_target_bulk_epoch


TRANSITION_VERSION = "CN_TO_US_APPLICATION_TRANSITION_V2"
_LEGACY_CN_ACCEPTED_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}
_LIGHTWEIGHT_CN_ACCEPTED_STATUSES = {"PASS", "WARN"}


def _current_application_source_file_count(raw_root: Path) -> int:
    total = 0
    for directory in (raw_root / "incoming" / "us", raw_root / "archive" / "us"):
        if not directory.exists():
            continue
        total += sum(
            1
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".zip", ".xml"}
        )
    return total


def _target_bulk_accepted_pipeline(
    raw_root: Path,
    *,
    epoch: dict[str, Any] | None,
    expected_history_parts: int,
    deep_source_test: bool,
    verify_source_files: bool,
    source_preflight_builder: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if epoch is None:
        return None
    checkpoint = int(epoch.get("checkpoint_sequence") or 0)
    observed_count = _current_application_source_file_count(raw_root)
    if checkpoint < 1 or observed_count != checkpoint:
        return None
    reports: dict[str, Any] = {"target_bulk_epoch": epoch}
    if verify_source_files:
        preflight = source_preflight_builder(
            raw_root,
            expected_history_parts=expected_history_parts,
            deep_source_test=deep_source_test,
        )
        reports["source_preflight"] = preflight
        semantic_count = int(
            (preflight.get("source_inventory") or {}).get("semantic_source_count") or 0
        )
        if not preflight.get("safe_to_replay") or semantic_count != checkpoint:
            return {
                "state": "SOURCE_CORPUS_BLOCKED",
                "ready": False,
                "reason_codes": list(preflight.get("hard_issue_types") or [])
                + list(preflight.get("not_ready_reasons") or [])
                + (["target_bulk_checkpoint_source_count_mismatch"] if semantic_count != checkpoint else []),
                "next_action": {
                    "code": "INVESTIGATE_TARGET_BULK_SOURCE_EVIDENCE",
                    "description": "Target-bulk accepted epoch no longer matches source-backed corpus evidence.",
                },
                "evidence_mode": "TARGET_BULK_ACCEPTED_EPOCH",
                "reports": reports,
            }
    return {
        "state": "ACCEPTED",
        "ready": True,
        "reason_codes": [],
        "next_action": {
            "code": "NONE",
            "description": "US Application target-bulk corpus is durably accepted.",
        },
        "evidence_mode": "TARGET_BULK_ACCEPTED_EPOCH",
        "accepted_target_sequence_count": checkpoint,
        "target_bulk_epoch": epoch,
        "reports": reports,
    }


def _cn_checkpoint_accepted(cn_checkpoint: dict[str, Any]) -> bool:
    """Accept the lightweight CN serving checkpoint without claiming a new full audit.

    Legacy final-checkpoint-shaped reports remain supported for injected callers/tests,
    but the production default is the metadata-only serving checkpoint.
    """
    status = str(cn_checkpoint.get("status") or "UNKNOWN")
    if cn_checkpoint.get("checkpoint_version") == CN_SERVING_CHECKPOINT_VERSION:
        return bool(
            status in _LIGHTWEIGHT_CN_ACCEPTED_STATUSES
            and cn_checkpoint.get("expected_package_success")
            and cn_checkpoint.get("quiescent")
            and cn_checkpoint.get("core_tables_ready")
            and cn_checkpoint.get("goods_schema_exact")
            and cn_checkpoint.get("full_corpus_scan") is False
            and cn_checkpoint.get("package_reprocessed") is False
        )

    return bool(
        status in _LEGACY_CN_ACCEPTED_STATUSES
        and cn_checkpoint.get("ready_for_next_domain")
    )


def evaluate_transition(
    *,
    cn_checkpoint: dict[str, Any],
    us_pipeline: dict[str, Any] | None,
    expected_history_parts: int,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    cn_status = str(cn_checkpoint.get("status") or "UNKNOWN")
    cn_accepted = _cn_checkpoint_accepted(cn_checkpoint)
    common = {
        "transition_version": TRANSITION_VERSION,
        "read_only": True,
        "expected_history_parts": expected_history_parts,
        "cn_checkpoint_status": cn_status,
        "cn_gate_passed": cn_accepted,
        "cn_checkpoint": cn_checkpoint,
    }

    if not cn_accepted:
        return {
            **common,
            "status": "BLOCKED_BY_CN",
            "ready_for_us_application": False,
            "safe_to_start_us_replay": False,
            "reason_codes": ["cn_serving_checkpoint_not_accepted"],
            "us_pipeline_evaluated": False,
            "us_pipeline": None,
            "next_action": {
                "code": "PASS_CN_LIGHTWEIGHT_SERVING_CHECKPOINT",
                "description": (
                    "Restore a healthy, quiescent CN serving state before evaluating "
                    "or starting US Application replay. Do not rerun the accepted CN "
                    "full-corpus audit solely for this transition."
                ),
            },
        }

    if us_pipeline is None:
        return {
            **common,
            "status": "US_PIPELINE_REPORT_MISSING",
            "ready_for_us_application": False,
            "safe_to_start_us_replay": False,
            "reason_codes": ["us_pipeline_report_missing"],
            "us_pipeline_evaluated": False,
            "us_pipeline": None,
            "next_action": {
                "code": "RECHECK_US_APPLICATION_TRANSITION",
                "description": "Re-run the read-only US Application transition gate.",
            },
        }

    us_state = str(us_pipeline.get("state") or "UNKNOWN")
    us_ready = bool(us_pipeline.get("ready"))
    reason_codes = list(us_pipeline.get("reason_codes") or [])
    next_action = us_pipeline.get("next_action") or {}

    if us_state == "REPLAY_READY":
        status = "READY_FOR_US_APPLICATION_REPLAY"
        ready_for_us_application = True
        safe_to_start_us_replay = True
    elif us_state == "ACCEPTED" and us_ready:
        status = "US_APPLICATION_ALREADY_ACCEPTED"
        ready_for_us_application = True
        safe_to_start_us_replay = False
    else:
        status = "US_APPLICATION_NOT_READY"
        ready_for_us_application = False
        safe_to_start_us_replay = False

    return {
        **common,
        "status": status,
        "ready_for_us_application": ready_for_us_application,
        "safe_to_start_us_replay": safe_to_start_us_replay,
        "reason_codes": reason_codes,
        "us_pipeline_evaluated": True,
        "us_pipeline_state": us_state,
        "us_pipeline": us_pipeline,
        "next_action": next_action,
    }


def build_transition_gate(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    verify_source_files: bool = False,
    persistent_worker_running: bool = False,
    cn_checkpoint_builder: Callable[..., dict[str, Any]] = build_serving_state_checkpoint,
    us_readiness_builder: Callable[..., dict[str, Any]] = build_us_readiness,
    target_bulk_epoch_builder: Callable[[], dict[str, Any] | None] | None = None,
    source_preflight_builder: Callable[..., dict[str, Any]] = build_us_source_preflight,
) -> dict[str, Any]:
    """Return the read-only CN -> US Application transition decision.

    The production CN prerequisite is the metadata-only serving-state checkpoint;
    the already-accepted CN full-corpus semantic audit is not repeated here. US
    source/schema/replay readiness is still fail-closed and is not evaluated until
    the CN serving checkpoint passes. No source package is staged, registered,
    reset, replayed, or otherwise mutated by this gate.
    """
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    if persistent_worker_running:
        return evaluate_transition(
            cn_checkpoint={
                "checkpoint_version": CN_SERVING_CHECKPOINT_VERSION,
                "status": "BLOCKED",
                "read_only": True,
                "evidence_mode": "LIGHTWEIGHT_SERVING_CHECKPOINT",
                "reasons": [
                    {
                        "code": "PERSISTENT_WORKER_RUNNING",
                        "message": (
                            "Persistent worker must be stopped before the US "
                            "Application transition gate."
                        ),
                        "severity": "BLOCKED",
                    }
                ],
                "full_corpus_scan": False,
                "package_reprocessed": False,
            },
            us_pipeline=None,
            expected_history_parts=expected_history_parts,
        )

    cn_checkpoint = cn_checkpoint_builder()
    if not _cn_checkpoint_accepted(cn_checkpoint):
        return evaluate_transition(
            cn_checkpoint=cn_checkpoint,
            us_pipeline=None,
            expected_history_parts=expected_history_parts,
        )

    if target_bulk_epoch_builder is not None:
        target_epoch = target_bulk_epoch_builder()
    elif us_readiness_builder is build_us_readiness:
        target_epoch = latest_complete_target_bulk_epoch()
    else:
        target_epoch = None
    target_pipeline = _target_bulk_accepted_pipeline(
        raw_root,
        epoch=target_epoch,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        verify_source_files=verify_source_files,
        source_preflight_builder=source_preflight_builder,
    )
    us_pipeline = target_pipeline or us_readiness_builder(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        verify_source_files=verify_source_files,
    )
    return evaluate_transition(
        cn_checkpoint=cn_checkpoint,
        us_pipeline=us_pipeline,
        expected_history_parts=expected_history_parts,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only CN to US Application corpus transition gate"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--raw-root", type=Path, default=None)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument("--verify-source-files", action="store_true")
    parser.add_argument("--persistent-worker-running", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")

    report = build_transition_gate(
        args.raw_root or get_settings().raw_data_root,
        expected_history_parts=args.expected_history_parts,
        deep_source_test=args.deep_source_test,
        verify_source_files=args.verify_source_files,
        persistent_worker_running=args.persistent_worker_running,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            default=str,
        )
    )
    if report["status"] in {
        "READY_FOR_US_APPLICATION_REPLAY",
        "US_APPLICATION_ALREADY_ACCEPTED",
    }:
        return 0
    if report["status"] == "US_APPLICATION_NOT_READY":
        return 3
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
