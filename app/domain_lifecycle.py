from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.us_ttab.transition_gate import build_transition_gate as build_ttab_gate


LIFECYCLE_VERSION = "MARKORBIT_DOMAIN_LIFECYCLE_V1"
FROZEN_ORDER = ["CN", "US_APPLICATION", "US_ASSIGNMENT", "US_TTAB", "FINAL_ACCEPTANCE"]


def _application_gate(ttab_gate: dict[str, Any]) -> dict[str, Any]:
    assignment_gate = ttab_gate.get("assignment_gate") or {}
    return assignment_gate.get("application_gate") or {}


def _cn_checkpoint(ttab_gate: dict[str, Any]) -> dict[str, Any]:
    application_gate = _application_gate(ttab_gate)
    return application_gate.get("cn_checkpoint") or {}


def evaluate_lifecycle(ttab_gate: dict[str, Any]) -> dict[str, Any]:
    ttab_status = str(ttab_gate.get("status") or "UNKNOWN")
    assignment_gate = ttab_gate.get("assignment_gate") or {}
    assignment_status = str(assignment_gate.get("status") or "UNKNOWN")
    application_gate = assignment_gate.get("application_gate") or {}
    application_status = str(application_gate.get("status") or "UNKNOWN")
    cn_checkpoint = application_gate.get("cn_checkpoint") or {}
    cn_status = str(cn_checkpoint.get("status") or "UNKNOWN")

    gates = {
        "cn": {
            "status": cn_status,
            "accepted": bool(cn_checkpoint.get("ready_for_next_domain")),
        },
        "us_application": {
            "status": application_status,
            "accepted": application_status == "US_APPLICATION_ALREADY_ACCEPTED",
        },
        "us_assignment": {
            "status": assignment_status,
            "accepted": bool(assignment_gate.get("assignment_ready")),
        },
        "us_ttab": {
            "status": ttab_status,
            "accepted": bool(ttab_gate.get("ttab_ready")),
        },
    }

    if ttab_status == "TTAB_ACCEPTED" and gates["us_ttab"]["accepted"]:
        current_phase = "FINAL_ACCEPTANCE"
        lifecycle_status = "FINAL_ACCEPTANCE_REQUIRED"
        next_action = {
            "code": "RUN_FOUR_DOMAIN_ACCEPTANCE",
            "description": (
                "All four domain gates are accepted. Run the existing formal four-domain "
                "acceptance with pinned Application coverage policy and formal report files."
            ),
        }
    elif ttab_status == "TTAB_PHASE_UNLOCKED":
        current_phase = "US_TTAB"
        lifecycle_status = "IN_PROGRESS"
        next_action = ttab_gate.get("next_action") or {}
    elif assignment_status == "ASSIGNMENT_PHASE_UNLOCKED":
        current_phase = "US_ASSIGNMENT"
        lifecycle_status = "IN_PROGRESS"
        next_action = assignment_gate.get("next_action") or {}
    elif application_status in {
        "READY_FOR_US_APPLICATION_REPLAY",
        "US_APPLICATION_NOT_READY",
        "US_PIPELINE_REPORT_MISSING",
    }:
        current_phase = "US_APPLICATION"
        lifecycle_status = "IN_PROGRESS"
        next_action = application_gate.get("next_action") or {}
    elif application_status == "US_APPLICATION_ALREADY_ACCEPTED":
        # Application acceptance is complete but Assignment did not yield a normal
        # unlocked/accepted state. Keep the phase on Assignment and surface its blocker.
        current_phase = "US_ASSIGNMENT"
        lifecycle_status = "BLOCKED"
        next_action = assignment_gate.get("next_action") or {}
    else:
        current_phase = "CN"
        lifecycle_status = "BLOCKED" if cn_status in {"FAIL", "BLOCKED"} else "IN_PROGRESS"
        next_action = application_gate.get("next_action") or {
            "code": "COMPLETE_CN_AND_PASS_FINAL_CHECKPOINT",
            "description": "Complete CN and pass its final checkpoint before entering US Application.",
        }

    return {
        "lifecycle_version": LIFECYCLE_VERSION,
        "read_only": True,
        "frozen_order": FROZEN_ORDER,
        "current_phase": current_phase,
        "status": lifecycle_status,
        "next_action": next_action,
        "gates": gates,
        "transition_report": ttab_gate,
    }


def build_lifecycle(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    verify_us_source_files: bool = False,
    verify_assignment_sources: bool = False,
    verify_ttab_sources: bool = False,
    persistent_worker_running: bool = False,
    ttab_gate_builder: Callable[..., dict[str, Any]] = build_ttab_gate,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    ttab_gate = ttab_gate_builder(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        verify_us_source_files=verify_us_source_files,
        verify_assignment_sources=verify_assignment_sources,
        verify_ttab_sources=verify_ttab_sources,
        persistent_worker_running=persistent_worker_running,
    )
    return evaluate_lifecycle(ttab_gate)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only MarkOrbit Data Engine domain lifecycle status"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--raw-root", type=Path, default=None)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument("--verify-us-source-files", action="store_true")
    parser.add_argument("--verify-assignment-sources", action="store_true")
    parser.add_argument("--verify-ttab-sources", action="store_true")
    parser.add_argument("--persistent-worker-running", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")

    report = build_lifecycle(
        args.raw_root or get_settings().raw_data_root,
        expected_history_parts=args.expected_history_parts,
        deep_source_test=args.deep_source_test,
        verify_us_source_files=args.verify_us_source_files,
        verify_assignment_sources=args.verify_assignment_sources,
        verify_ttab_sources=args.verify_ttab_sources,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
