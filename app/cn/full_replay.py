from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from app.cn.guarded_run_once import build_execution_guard
from app.jobs import ingest_pending_cn, scan_cn_incoming


RUNNER_VERSION = "CN_M16_FULL_REPLAY_V1"
Emit = Callable[[dict[str, Any]], None]
BeforePackage = Callable[[str], None]


def _default_emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), flush=True)


def _package_event(result: dict[str, Any], *, phase: str, processed_total: int) -> dict[str, Any]:
    return {
        "event": "CN_FULL_REPLAY_PACKAGE",
        "runner_version": RUNNER_VERSION,
        "phase": phase,
        "processed_total": processed_total,
        "attempted": int(result.get("attempted") or 0),
        "success": int(result.get("success") or 0),
        "failed": int(result.get("failed") or 0),
        "skipped_missing": int(result.get("skipped_missing") or 0),
        "busy": bool(result.get("busy")),
        "packages": result.get("packages") or [],
    }


def _result_failed(result: dict[str, Any]) -> bool:
    return bool(result.get("failed") or result.get("skipped_missing"))


def run_full_replay(
    *,
    resume_failed: bool = False,
    max_packages: int | None = None,
    trigger_type: str = "MANUAL_FULL_CORPUS",
    emit: Emit = _default_emit,
    before_package: BeforePackage | None = None,
    allow_clean_start: bool = True,
) -> tuple[int, dict[str, Any]]:
    """Run a deterministic CN corpus replay without rescanning raw files per package.

    The first clean execution performs the existing guarded preflight, scans/registers
    incoming sources once, then drains the registered queue by source_rank. A prior
    FAILED/MISSING_FILE package is a hard barrier unless ``resume_failed`` is explicitly
    enabled; when enabled, the runner repairs the failed package before advancing.

    ``before_package`` is an optional execution guard invoked immediately before each
    package attempt. Admin continuous replay uses it to re-check storage headroom for
    every package instead of trusting a single long-running preflight. ``allow_clean_start``
    lets Admin reuse this runner while keeping the first clean replay manual-only.
    """

    processed_total = 0
    guard = build_execution_guard()
    emit({"event": "CN_FULL_REPLAY_GUARD", "runner_version": RUNNER_VERSION, **guard})

    if guard.get("mode") == "CLEAN_RESET_FIRST_RUN":
        if not allow_clean_start:
            summary = {
                "status": "BLOCKED",
                "reason": "CLEAN_START_DISABLED",
                "processed_total": processed_total,
                "guard": guard,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 4, summary
        if not guard.get("allowed"):
            summary = {
                "status": "BLOCKED",
                "reason": "CLEAN_REPLAY_GATE_FAILED",
                "processed_total": processed_total,
                "guard": guard,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 4, summary

        scan = scan_cn_incoming(trigger_type=f"{trigger_type}_DISCOVERY")
        emit(
            {
                "event": "CN_FULL_REPLAY_DISCOVERY",
                "runner_version": RUNNER_VERSION,
                **scan,
            }
        )
        if int(scan.get("failed") or 0) > 0:
            summary = {
                "status": "FAILED",
                "reason": "SOURCE_REGISTRATION_FAILED",
                "processed_total": processed_total,
                "scan": scan,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 2, summary
        guard = build_execution_guard()
        emit({"event": "CN_FULL_REPLAY_POST_SCAN_GUARD", "runner_version": RUNNER_VERSION, **guard})

    while guard.get("mode") == "RETRY_REQUIRED":
        if not resume_failed:
            summary = {
                "status": "BLOCKED",
                "reason": "RETRY_REQUIRED",
                "processed_total": processed_total,
                "guard": guard,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 4, summary

        if before_package is not None:
            before_package("RETRY")
        repair = ingest_pending_cn(
            trigger_type=f"{trigger_type}_RETRY",
            include_failed=True,
            limit=1,
        )
        event = _package_event(repair, phase="RETRY", processed_total=processed_total)
        emit(event)
        if repair.get("busy"):
            summary = {
                "status": "BUSY",
                "reason": "INGESTION_LOCK_BUSY",
                "processed_total": processed_total,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 3, summary
        if _result_failed(repair) or int(repair.get("success") or 0) != 1:
            summary = {
                "status": "FAILED",
                "reason": "FAILED_PACKAGE_RETRY_FAILED",
                "processed_total": processed_total,
                "result": repair,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 2, summary

        processed_total += 1
        if max_packages is not None and processed_total >= max_packages:
            summary = {
                "status": "PARTIAL",
                "reason": "MAX_PACKAGES_REACHED",
                "processed_total": processed_total,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 0, summary

        guard = build_execution_guard()
        emit({"event": "CN_FULL_REPLAY_POST_RETRY_GUARD", "runner_version": RUNNER_VERSION, **guard})

    if not guard.get("allowed") or guard.get("mode") != "REGISTERED_REPLAY_CONTINUATION":
        summary = {
            "status": "BLOCKED",
            "reason": "EXECUTION_GUARD_BLOCKED",
            "processed_total": processed_total,
            "guard": guard,
        }
        emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
        return 4, summary

    while True:
        if before_package is not None:
            before_package("NORMAL")
        result = ingest_pending_cn(
            trigger_type=trigger_type,
            include_failed=False,
            limit=1,
        )
        emit(_package_event(result, phase="NORMAL", processed_total=processed_total))

        if result.get("busy"):
            summary = {
                "status": "BUSY",
                "reason": "INGESTION_LOCK_BUSY",
                "processed_total": processed_total,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 3, summary

        if _result_failed(result):
            summary = {
                "status": "FAILED",
                "reason": "PACKAGE_INGEST_FAILED",
                "processed_total": processed_total,
                "result": result,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 2, summary

        attempted = int(result.get("attempted") or 0)
        success = int(result.get("success") or 0)
        if attempted == 0:
            summary = {
                "status": "COMPLETE",
                "processed_total": processed_total,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 0, summary

        if success != attempted:
            summary = {
                "status": "FAILED",
                "reason": "UNEXPECTED_INGEST_RESULT",
                "processed_total": processed_total,
                "result": result,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 2, summary

        processed_total += success
        if max_packages is not None and processed_total >= max_packages:
            summary = {
                "status": "PARTIAL",
                "reason": "MAX_PACKAGES_REACHED",
                "processed_total": processed_total,
            }
            emit({"event": "CN_FULL_REPLAY_COMPLETE", **summary})
            return 0, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic CN M1.6 full-corpus replay/resume runner"
    )
    parser.add_argument(
        "--resume-failed",
        action="store_true",
        help="Explicitly retry the earliest FAILED/MISSING_FILE package before advancing",
    )
    parser.add_argument(
        "--max-packages",
        type=int,
        default=None,
        help="Optional successful-package limit for controlled runs",
    )
    args = parser.parse_args()
    if args.max_packages is not None and args.max_packages <= 0:
        parser.error("--max-packages must be greater than zero")

    try:
        code, _ = run_full_replay(
            resume_failed=args.resume_failed,
            max_packages=args.max_packages,
        )
        return code
    except Exception as exc:
        _default_emit(
            {
                "event": "CN_FULL_REPLAY_FATAL",
                "runner_version": RUNNER_VERSION,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
