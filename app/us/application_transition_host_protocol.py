from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.us.application_transition_gate import TRANSITION_VERSION, build_transition_gate

HOST_PROTOCOL_VERSION = "US_APPLICATION_TRANSITION_HOST_V1"
SUMMARY_PREFIX = "MARKORBIT_US_APPLICATION_TRANSITION_SUMMARY\t"
EVIDENCE_PREFIX = "MARKORBIT_US_APPLICATION_TRANSITION_EVIDENCE\t"


def build_host_summary(report: dict[str, Any]) -> dict[str, Any]:
    next_action = report.get("next_action") or {}
    return {
        "host_protocol_version": HOST_PROTOCOL_VERSION,
        "transition_version": str(report.get("transition_version") or ""),
        "status": str(report.get("status") or "UNKNOWN"),
        "expected_history_parts": int(report.get("expected_history_parts") or 0),
        "cn_checkpoint_status": str(report.get("cn_checkpoint_status") or "UNKNOWN"),
        "cn_gate_passed": bool(report.get("cn_gate_passed")),
        "us_pipeline_evaluated": bool(report.get("us_pipeline_evaluated")),
        "us_pipeline_state": str(report.get("us_pipeline_state") or ""),
        "ready_for_us_application": bool(report.get("ready_for_us_application")),
        "safe_to_start_us_replay": bool(report.get("safe_to_start_us_replay")),
        "reason_codes": [str(value) for value in report.get("reason_codes") or []],
        "next_action_code": str(next_action.get("code") or ""),
    }


def exit_code_for_report(report: dict[str, Any]) -> int:
    status = str(report.get("status") or "")
    if status in {
        "READY_FOR_US_APPLICATION_REPLAY",
        "US_APPLICATION_ALREADY_ACCEPTED",
    }:
        return 0
    if status == "US_APPLICATION_NOT_READY":
        return 3
    return 4


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PowerShell-safe host protocol for the US Application transition gate"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--raw-root", type=Path, default=None)
    parser.add_argument("--deep-source-test", action="store_true")
    parser.add_argument("--verify-source-files", action="store_true")
    parser.add_argument("--persistent-worker-running", action="store_true")
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
    summary = build_host_summary(report)
    if summary["transition_version"] != TRANSITION_VERSION:
        raise RuntimeError("Transition version mismatch while building host protocol summary.")

    print(
        SUMMARY_PREFIX
        + json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    print(
        EVIDENCE_PREFIX
        + json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    return exit_code_for_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
