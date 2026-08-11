from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.cn.final_checkpoint import build_final_checkpoint
from app.config import get_settings
from app.us.pipeline_readiness import build_readiness as build_us_readiness


TRANSITION_VERSION = "CN_TO_US_APPLICATION_TRANSITION_V1"
_CN_ACCEPTED_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}


def evaluate_transition(
    *,
    cn_checkpoint: dict[str, Any],
    us_pipeline: dict[str, Any] | None,
    expected_history_parts: int,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    cn_status = str(cn_checkpoint.get("status") or "UNKNOWN")
    cn_accepted = (
        cn_status in _CN_ACCEPTED_STATUSES
        and bool(cn_checkpoint.get("ready_for_next_domain"))
    )
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
            "reason_codes": ["cn_final_checkpoint_not_accepted"],
            "us_pipeline_evaluated": False,
            "us_pipeline": None,
            "next_action": {
                "code": "COMPLETE_CN_AND_PASS_FINAL_CHECKPOINT",
                "description": (
                    "Finish CN replay and pass the CN M1.6 final checkpoint before "
                    "evaluating or starting US Application replay."
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
    cn_checkpoint_builder: Callable[..., dict[str, Any]] = build_final_checkpoint,
    us_readiness_builder: Callable[..., dict[str, Any]] = build_us_readiness,
) -> dict[str, Any]:
    """Return the read-only CN -> US Application transition decision.

    US source/schema/replay readiness is deliberately not evaluated until the
    CN final checkpoint is accepted. This encodes corpus ordering without
    staging, registering, resetting, replaying, or changing database state.
    """
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    cn_checkpoint = cn_checkpoint_builder(
        persistent_worker_running=persistent_worker_running
    )
    if not (
        cn_checkpoint.get("status") in _CN_ACCEPTED_STATUSES
        and cn_checkpoint.get("ready_for_next_domain")
    ):
        return evaluate_transition(
            cn_checkpoint=cn_checkpoint,
            us_pipeline=None,
            expected_history_parts=expected_history_parts,
        )

    us_pipeline = us_readiness_builder(
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
