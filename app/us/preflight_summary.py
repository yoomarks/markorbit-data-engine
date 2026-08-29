from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SUMMARY_VERSION = "US_SOURCE_PREFLIGHT_SUMMARY_V1"
EXPECTED_DISCOVERY_NOT_READY = ["historical_tail_part_count_not_pinned"]


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    inventory = report.get("source_inventory") or {}
    hard_issue_types = [str(item) for item in report.get("hard_issue_types") or []]
    not_ready_reasons = [str(item) for item in report.get("not_ready_reasons") or []]
    warning_reasons = [str(item) for item in report.get("warning_reasons") or []]
    status = str(report.get("status") or "")
    safe_to_replay = bool(report.get("safe_to_replay"))

    discovery_only_not_ready = (
        status == "NOT_READY"
        and not hard_issue_types
        and not_ready_reasons == EXPECTED_DISCOVERY_NOT_READY
    )
    pinned_pass = (
        status in {"PASS", "PASS_WITH_WARNINGS"}
        and safe_to_replay
        and not hard_issue_types
        and not not_ready_reasons
    )

    return {
        "summary_version": SUMMARY_VERSION,
        "preflight_version": str(report.get("preflight_version") or ""),
        "status": status,
        "safe_to_replay": safe_to_replay,
        "physical_source_count": int(inventory.get("physical_source_count") or 0),
        "semantic_source_count": int(inventory.get("semantic_source_count") or 0),
        "history_source_count": int(inventory.get("history_source_count") or 0),
        "daily_source_count": int(inventory.get("daily_source_count") or 0),
        "historical_baseline_end": report.get("historical_baseline_end"),
        "archive_staging_required_count": int(
            report.get("archive_staging_required_count") or 0
        ),
        "hard_issue_types": hard_issue_types,
        "not_ready_reasons": not_ready_reasons,
        "warning_reasons": warning_reasons,
        "discovery_only_not_ready": discovery_only_not_ready,
        "pinned_pass": pinned_pass,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a full US source preflight report into a PowerShell-safe gate."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    summary = build_summary(report)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
