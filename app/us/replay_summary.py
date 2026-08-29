from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUMMARY_VERSION = "US_REPLAY_SUMMARY_V1"


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    mode = str(report.get("mode") or "")
    status = str(report.get("status") or "")
    processed = report.get("processed") or []
    if not isinstance(processed, list):
        raise ValueError("processed must be a list when present")

    next_step = report.get("next_step")
    if next_step is None:
        final_plan = report.get("final_plan") or {}
        if isinstance(final_plan, dict):
            next_step = final_plan.get("next_step")
    if next_step is not None and not isinstance(next_step, dict):
        raise ValueError("next_step must be an object when present")

    first_processed: dict[str, Any] | None = None
    if processed:
        row = processed[0]
        if not isinstance(row, dict):
            raise ValueError("processed entries must be objects")
        first_processed = {
            "sequence": row.get("sequence"),
            "package_id": str(row.get("package_id") or ""),
            "file_name": str(row.get("file_name") or ""),
            "sha256": str(row.get("sha256") or "").lower(),
            "retrying": bool(row.get("retrying")),
        }

    final_plan = report.get("final_plan") or {}
    remaining_count = report.get("remaining_count")
    if remaining_count is None and isinstance(final_plan, dict):
        remaining_count = final_plan.get("remaining_count")

    summary = {
        "summary_version": SUMMARY_VERSION,
        "executor_version": str(report.get("executor_version") or ""),
        "mode": mode,
        "status": status,
        "safe_to_execute": bool(report.get("safe_to_execute")),
        "processed_count": int(report.get("processed_count") or 0),
        "source_preflight_runs": int(report.get("source_preflight_runs") or 0),
        "remaining_count": None if remaining_count is None else int(remaining_count),
        "next_step": (
            None
            if next_step is None
            else {
                "sequence": next_step.get("sequence"),
                "file_name": str(next_step.get("file_name") or ""),
                "sha256": str(next_step.get("sha256") or "").lower(),
                "action": str(next_step.get("action") or ""),
            }
        ),
        "first_processed": first_processed,
        "blockers": [str(item) for item in (report.get("blockers") or [])],
        "error": str(report.get("error") or ""),
    }
    summary["dry_run_ready"] = bool(
        mode == "DRY_RUN"
        and status == "READY"
        and report.get("safe_to_execute") is True
        and summary["next_step"] is not None
    )
    summary["apply_one_package_ok"] = bool(
        mode == "APPLY"
        and status in {"PAUSED", "COMPLETE"}
        and summary["processed_count"] == 1
        and summary["source_preflight_runs"] == 1
        and first_processed is not None
    )
    return summary


def write_summary(input_path: Path, output_path: Path) -> dict[str, Any]:
    report = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("US replay report root must be an object")
    summary = build_summary(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact US replay summary sidecar")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    summary = write_summary(args.input_path, args.output_path)
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
