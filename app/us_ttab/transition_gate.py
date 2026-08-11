from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.us_assignment.transition_gate import build_transition_gate as build_assignment_gate
from app.us_ttab.readiness import build_readiness as build_ttab_readiness


TRANSITION_VERSION = "US_ASSIGNMENT_TO_TTAB_TRANSITION_V1"
_ASSIGNMENT_ACCEPTED_STATUS = "ASSIGNMENT_ACCEPTED"


def evaluate_transition(
    *,
    assignment_gate: dict[str, Any],
    ttab_readiness: dict[str, Any] | None,
    expected_history_parts: int,
) -> dict[str, Any]:
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    assignment_status = str(assignment_gate.get("status") or "UNKNOWN")
    assignment_accepted = (
        assignment_status == _ASSIGNMENT_ACCEPTED_STATUS
        and bool(assignment_gate.get("assignment_ready"))
    )
    common = {
        "transition_version": TRANSITION_VERSION,
        "read_only": True,
        "expected_history_parts": expected_history_parts,
        "assignment_gate_status": assignment_status,
        "assignment_gate_passed": assignment_accepted,
        "assignment_gate": assignment_gate,
    }

    if not assignment_accepted:
        return {
            **common,
            "status": "BLOCKED_BY_US_ASSIGNMENT",
            "ready_for_ttab_phase": False,
            "ttab_readiness_evaluated": False,
            "ttab_ready": False,
            "reason_codes": ["us_assignment_not_source_backed_accepted"],
            "ttab_readiness": None,
            "next_action": assignment_gate.get("next_action")
            or {
                "code": "COMPLETE_US_ASSIGNMENT",
                "description": (
                    "Complete and source-back accept US Assignment before evaluating TTAB."
                ),
            },
        }

    if ttab_readiness is None:
        return {
            **common,
            "status": "TTAB_READINESS_REPORT_MISSING",
            "ready_for_ttab_phase": True,
            "ttab_readiness_evaluated": False,
            "ttab_ready": False,
            "reason_codes": ["ttab_readiness_report_missing"],
            "ttab_readiness": None,
            "next_action": {
                "code": "RECHECK_TTAB_TRANSITION",
                "description": "Re-run the read-only US TTAB transition gate.",
            },
        }

    ttab_state = str(ttab_readiness.get("state") or "UNKNOWN")
    ttab_ready = bool(ttab_readiness.get("ready"))
    status = "TTAB_ACCEPTED" if ttab_ready else "TTAB_PHASE_UNLOCKED"

    return {
        **common,
        "status": status,
        "ready_for_ttab_phase": True,
        "ttab_readiness_evaluated": True,
        "ttab_ready": ttab_ready,
        "ttab_state": ttab_state,
        "reason_codes": list(ttab_readiness.get("reason_codes") or []),
        "ttab_readiness": ttab_readiness,
        "next_action": ttab_readiness.get("next_action") or {},
    }


def build_transition_gate(
    raw_root: Path,
    *,
    expected_history_parts: int,
    deep_source_test: bool = False,
    verify_us_source_files: bool = False,
    verify_assignment_sources: bool = False,
    verify_ttab_sources: bool = False,
    persistent_worker_running: bool = False,
    assignment_gate_builder: Callable[..., dict[str, Any]] = build_assignment_gate,
    ttab_readiness_builder: Callable[..., dict[str, Any]] = build_ttab_readiness,
) -> dict[str, Any]:
    """Return the read-only US Assignment -> TTAB transition decision.

    The TTAB readiness builder is not called until the chained upstream gate
    proves Assignment is accepted. This preserves CN -> Application ->
    Assignment -> TTAB ordering without mutating any corpus.
    """
    if expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    assignment_gate = assignment_gate_builder(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=deep_source_test,
        verify_us_source_files=verify_us_source_files,
        verify_assignment_sources=verify_assignment_sources,
        persistent_worker_running=persistent_worker_running,
    )
    if not (
        assignment_gate.get("status") == _ASSIGNMENT_ACCEPTED_STATUS
        and assignment_gate.get("assignment_ready")
    ):
        return evaluate_transition(
            assignment_gate=assignment_gate,
            ttab_readiness=None,
            expected_history_parts=expected_history_parts,
        )

    ttab_readiness = ttab_readiness_builder(
        raw_root=raw_root,
        verify_sources=verify_ttab_sources,
    )
    return evaluate_transition(
        assignment_gate=assignment_gate,
        ttab_readiness=ttab_readiness,
        expected_history_parts=expected_history_parts,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only US Assignment to TTAB transition gate"
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

    report = build_transition_gate(
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
    if report["status"] in {"TTAB_PHASE_UNLOCKED", "TTAB_ACCEPTED"}:
        return 0
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
