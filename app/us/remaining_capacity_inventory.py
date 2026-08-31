from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.us.replay_executor import build_replay_plan


REPORT_VERSION = "US_REMAINING_CAPACITY_INVENTORY_V1"


def build_remaining_capacity_inventory(
    raw_root: Path,
    *,
    expected_history_parts: int,
    plan_builder: Callable[..., dict[str, Any]] = build_replay_plan,
) -> dict[str, Any]:
    """Return authoritative compressed bytes for the unfinished replay suffix.

    The deterministic replay plan remains the authority for prefix completion and
    source identity. This deliberately reuses the normal shallow source preflight;
    it never requests a deep source test and performs no ingest or storage mutation.
    """

    plan = plan_builder(
        raw_root,
        expected_history_parts=expected_history_parts,
        deep_source_test=False,
    )
    blockers = list(plan.get("blockers") or [])
    if not plan.get("safe_to_execute"):
        return {
            "inventory_version": REPORT_VERSION,
            "read_only": True,
            "status": "BLOCKED",
            "safe": False,
            "expected_history_parts": expected_history_parts,
            "remaining_count": int(plan.get("remaining_count") or 0),
            "remaining_raw_bytes": 0,
            "success_prefix_count": int(plan.get("success_prefix_count") or 0),
            "next_step": plan.get("next_step"),
            "blockers": blockers or ["replay_plan_not_safe"],
        }

    remaining = [
        step
        for step in plan.get("steps") or []
        if str(step.get("action") or "") != "SKIP_SUCCESS"
    ]
    size_issues: list[dict[str, Any]] = []
    remaining_raw_bytes = 0
    by_kind: dict[str, dict[str, int]] = {}

    for step in remaining:
        path = Path(str(step.get("path") or ""))
        if not path.is_file():
            size_issues.append(
                {
                    "type": "REMAINING_SOURCE_FILE_MISSING",
                    "sequence": int(step.get("sequence") or 0),
                    "file_name": str(step.get("file_name") or ""),
                    "path": str(path),
                }
            )
            continue
        size = int(path.stat().st_size)
        remaining_raw_bytes += size
        kind = str(step.get("package_kind") or "UNKNOWN")
        bucket = by_kind.setdefault(kind, {"package_count": 0, "raw_bytes": 0})
        bucket["package_count"] += 1
        bucket["raw_bytes"] += size

    if size_issues:
        return {
            "inventory_version": REPORT_VERSION,
            "read_only": True,
            "status": "BLOCKED",
            "safe": False,
            "expected_history_parts": expected_history_parts,
            "remaining_count": len(remaining),
            "remaining_raw_bytes": remaining_raw_bytes,
            "success_prefix_count": int(plan.get("success_prefix_count") or 0),
            "next_step": plan.get("next_step"),
            "by_package_kind": dict(sorted(by_kind.items())),
            "blockers": size_issues,
        }

    return {
        "inventory_version": REPORT_VERSION,
        "read_only": True,
        "status": "PASS",
        "safe": True,
        "expected_history_parts": expected_history_parts,
        "preflight_status": plan.get("preflight_status"),
        "remaining_count": len(remaining),
        "remaining_raw_bytes": remaining_raw_bytes,
        "success_prefix_count": int(plan.get("success_prefix_count") or 0),
        "next_step": plan.get("next_step"),
        "by_package_kind": dict(sorted(by_kind.items())),
        "source_bytes_already_present_on_raw_storage": True,
        "incremental_raw_copy_bytes_required_by_replay": 0,
        "deep_source_test_performed": False,
        "blockers": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only compressed-byte inventory for the unfinished US replay suffix"
    )
    parser.add_argument("--expected-history-parts", type=int, required=True)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    report = build_remaining_capacity_inventory(
        Path(settings.raw_data_path),
        expected_history_parts=args.expected_history_parts,
    )
    print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
    return 0 if report["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
