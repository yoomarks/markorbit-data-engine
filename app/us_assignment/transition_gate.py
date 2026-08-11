from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.us.application_transition_gate import build_transition_gate as build_application_gate
from app.us_assignment.readiness import build_readiness as build_assignment_readiness


TRANSITION_VERSION = "US_APPLICATION_TO_ASSIGNMENT_TRANSITION_V1"
_APPLICATION_ACCEPTED_STATUS = "US_APPLICATION_ALREADY_ACCEPTED"


def evaluate_transition(
    *,
    application_gate: dict[str, Any],
    assignment_readiness: dict[str, Any] | None,
    expected_history_parts: int,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    application_status = str(application_gate.get("status") or "UNKNOWN")
    application_accepted = (
        application_status == _APPLICATION_ACCEPTED_STATUS
        and bool(application_gate.get("ready_for_us_application"))
    )
    common = {
        "transition_version": TRANSITION_VERSION,
        "read_only": True,
        "expected_history_parts": expected_history_parts,
        "application_gate_status": application_status,
        "application_gate_passed": application_accepted,
        "application_gate": application_gate,
    }

    if not application_accepted:
        return {
            **common,
            "status": "BLOCKED_BY_US_APPLICATION",
            "ready_for_assignment_phase": False,
            "assignment_readiness_evaluated": False,
            "assignment_ready": False,
            "reason_codes": ["us_application_not_source_backed_accepted"],
            "assignment_readiness": None,
            "next_action": application_gate.get("next_action")
            or {
                "code": "COMPLETE_US_APPLICATION",
                "description": (
                    "Complete and source-back accept US Application before evaluating "
                    "US Assignment."
                ),
            },
        }

    if assignment_readiness is None:
        return {
            **common,
            "status": "ASSIGNMENT_READINESS_REPORT_MISSING",
            "ready_for_assignment_phase": True,
            "assignment_readiness_evaluated": False,
            "assignment_ready": False,
            "reason_codes": ["assignment_readiness_report_missing"],
            "assignment_readiness": None,
            "next_action": {
                "code": "RECHECK_ASSIGNMENT_TRANSITION",
                "description": "Re-run the read-only US Assignment transition gate.",
            },
        }

    assignment_state = str(assignment_readiness.get("state") or "UNKNOWN")
    assignment_ready = bool(assignment_readiness.get("ready"))
    if assignment_ready:
        status = "ASSIGNMENT_ACCEPTED"
    else:
        status = "ASSIGNMENT_PHASE_UNLOCKED"

    return {
        **common,
        "status": status,
        "ready_for_assignment_phase": True,
        "assignment_readiness_evaluated": True,
        "assignment_ready": assignment_ready,
        "assignment_state": assignment_state,
        "reason_codes": list(assignment_readiness.get("reason_codes") or []),
        "assignment_readiness": assignment_readiness,
        "next_action": assignment_readiness.get("next_action") or {},
    }


def build_transition_gate(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    verify_us_source_files: bool = False,
    verify_assignment_sources: bool = False,
    persistent_worker_running: bool = False,
    application_gate_builder: Callable[..., dict[str, Any]] = build_application_gate,
    assignment_readiness_builder: Callable[..., dict[str, Any]] = build_assignment_readiness,
) -> dict[str, Any]:
    """Return the read-only US Application -> Assignment transition decision.

    The Assignment readiness builder is never called until the chained CN -> US
    Application gate proves US Application is already source-backed accepted.
    """
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    application_gate = application_gate_builder(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        verify_source_files=verify_us_source_files,
        persistent_worker_running=persistent_worker_running,
    )
    if not (
        application_gate.get("status") == _APPLICATION_ACCEPTED_STATUS
        and application_gate.get("ready_for_us_application")
    ):
        return evaluate_transition(
            application_gate=application_gate,
            assignment_readiness=None,
            expected_history_parts=expected_history_parts,
        )

    assignment_readiness = assignment_readiness_builder(
        raw_root=raw_root,
        verify_sources=verify_assignment_sources,
    )
    return evaluate_transition(
        application_gate=application_gate,
        assignment_readiness=assignment_readiness,
        expected_history_parts=expected_history_parts,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only US Application to Assignment transition gate"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--raw-root", type=Path, default=None)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument("--verify-us-source-files", action="store_true")
    parser.add_argument("--verify-assignment-sources", action="store_true")
    parser.add_argument("--persistent-worker-running", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if args.expected_history_parts < 1:
        parser.error("--expected-history-parts must be at least 1")

    report = build_transition_gate(
        args.raw_root or get_settings().raw_data_root,
        expected_history_parts=args.expected_history_parts,
        deep_source_test=args.deep_source_test,
        verify_us_source_files=args.verify_us_source_files,
        verify_assignment_sources=args.verify_assignment_sources,
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
    if report["status"] in {"ASSIGNMENT_PHASE_UNLOCKED", "ASSIGNMENT_ACCEPTED"}:
        return 0
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
