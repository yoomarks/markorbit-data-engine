from __future__ import annotations

import argparse
from datetime import date
import json
import re
from typing import Any

from app.us import audit_real_data as base


AUDIT_VERSION = "US_M13_REAL_DATA_ACCEPTANCE_V2_HISTORY_PARTS"
HISTORY_PARTITION_RE = re.compile(
    r"^(?P<start>\d{4}-\d{2}-\d{2})/(?P<end>\d{4}-\d{2}-\d{2})#(?P<part>\d+)$"
)


def _parse_history_partition(package: dict[str, Any]) -> dict[str, Any] | None:
    if package.get("package_kind") != "HISTORICAL_APPLICATIONS":
        return None
    value = str(package.get("partition_value") or "")
    match = HISTORY_PARTITION_RE.match(value)
    if not match:
        return {
            "valid": False,
            "package_id": str(package.get("package_id") or ""),
            "file_name": str(package.get("file_name") or ""),
            "partition_value": value,
        }
    try:
        start = date.fromisoformat(match.group("start"))
        end = date.fromisoformat(match.group("end"))
    except ValueError:
        return {
            "valid": False,
            "package_id": str(package.get("package_id") or ""),
            "file_name": str(package.get("file_name") or ""),
            "partition_value": value,
        }
    return {
        "valid": True,
        "package_id": str(package.get("package_id") or ""),
        "file_name": str(package.get("file_name") or ""),
        "status": str(package.get("status") or ""),
        "coverage_start": start,
        "coverage_end": end,
        "part": int(match.group("part")),
    }


def historical_part_completeness(
    packages: list[dict[str, Any]],
    *,
    expected_history_parts: int | None = None,
) -> dict[str, Any]:
    if expected_history_parts is not None and expected_history_parts < 1:
        raise ValueError("expected_history_parts must be at least 1")

    parsed = [
        parsed
        for package in packages
        if (parsed := _parse_history_partition(package)) is not None
    ]
    invalid = [item for item in parsed if not item["valid"]]
    valid = [item for item in parsed if item["valid"]]

    groups: dict[tuple[date, date], list[dict[str, Any]]] = {}
    for item in valid:
        key = (item["coverage_start"], item["coverage_end"])
        groups.setdefault(key, []).append(item)

    group_reports: list[dict[str, Any]] = []
    for (start, end), items in sorted(groups.items()):
        parts = sorted({int(item["part"]) for item in items})
        start_part = 0 if parts and parts[0] == 0 else 1
        observed_max = max(parts) if parts else None
        expected_through_observed = (
            set(range(start_part, observed_max + 1)) if observed_max is not None else set()
        )
        missing_through_observed = sorted(expected_through_observed - set(parts))
        group_reports.append(
            {
                "coverage_start": start.isoformat(),
                "coverage_end": end.isoformat(),
                "observed_parts": parts,
                "observed_part_count": len(parts),
                "numbering_start": start_part,
                "missing_through_observed_max": missing_through_observed,
                "statuses": {
                    status: sum(1 for item in items if item["status"] == status)
                    for status in sorted({item["status"] for item in items})
                },
            }
        )

    baseline = max(
        group_reports,
        key=lambda item: (item["coverage_end"], item["coverage_start"]),
        default=None,
    )
    missing_expected: list[int] = []
    expected_suffixes: list[int] | None = None
    if baseline is not None and expected_history_parts is not None:
        start_part = int(baseline["numbering_start"])
        if start_part == 0:
            expected_suffixes = list(range(0, expected_history_parts))
        else:
            expected_suffixes = list(range(1, expected_history_parts + 1))
        missing_expected = sorted(
            set(expected_suffixes) - set(int(value) for value in baseline["observed_parts"])
        )

    leading_or_interior_gap = bool(
        baseline and baseline["missing_through_observed_max"]
    )
    return {
        "expected_history_parts": expected_history_parts,
        "invalid_partition_count": len(invalid),
        "invalid_partitions": invalid,
        "coverage_group_count": len(group_reports),
        "coverage_groups": group_reports,
        "baseline_coverage": baseline,
        "expected_suffixes": expected_suffixes,
        "missing_expected_parts": missing_expected,
        "leading_or_interior_gap": leading_or_interior_gap,
        "tail_count_pinned": expected_history_parts is not None,
        "complete": bool(
            baseline
            and not invalid
            and not leading_or_interior_gap
            and expected_history_parts is not None
            and not missing_expected
        ),
    }


def _recompute_status(report: dict[str, Any]) -> None:
    if report["hard_fail_reasons"]:
        report["status"] = "FAIL"
    elif report["not_ready_reasons"]:
        report["status"] = "NOT_READY"
    elif report["warning_reasons"]:
        report["status"] = "PASS_WITH_WARNINGS"
    else:
        report["status"] = "PASS"


def augment_report(
    report: dict[str, Any],
    packages: list[dict[str, Any]],
    *,
    expected_history_parts: int | None = None,
) -> dict[str, Any]:
    completeness = historical_part_completeness(
        packages,
        expected_history_parts=expected_history_parts,
    )
    report = dict(report)
    report["audit_version"] = AUDIT_VERSION
    report["historical_part_completeness"] = completeness
    report["hard_fail_reasons"] = list(report.get("hard_fail_reasons", []))
    report["not_ready_reasons"] = list(report.get("not_ready_reasons", []))
    report["warning_reasons"] = list(report.get("warning_reasons", []))

    if completeness["invalid_partition_count"]:
        report["not_ready_reasons"].append("unrecognized_historical_partition_identity")
    if completeness["baseline_coverage"] is not None and completeness["leading_or_interior_gap"]:
        report["not_ready_reasons"].append("historical_part_sequence_incomplete")
    if completeness["missing_expected_parts"]:
        report["not_ready_reasons"].append("expected_historical_parts_missing")
    if (
        completeness["baseline_coverage"] is not None
        and not completeness["leading_or_interior_gap"]
        and not completeness["invalid_partition_count"]
        and expected_history_parts is None
    ):
        report["warning_reasons"].append("historical_tail_part_count_not_pinned")

    report["not_ready_reasons"] = list(dict.fromkeys(report["not_ready_reasons"]))
    report["warning_reasons"] = list(dict.fromkeys(report["warning_reasons"]))
    _recompute_status(report)
    report["acceptance_note"] = (
        "Strict US M1.3 acceptance also checks historical coverage-part continuity. A visible "
        "leading/interior gap is NOT_READY. Because a filename suffix does not disclose the total "
        "number of trailing parts, strict PASS requires --expected-history-parts to pin the expected "
        "part count for the latest historical coverage range."
    )
    return report


def build_audit(
    *,
    verify_source_files: bool = False,
    expected_history_parts: int | None = None,
) -> dict[str, Any]:
    packages = base._package_rows()
    report = base.evaluate_acceptance(
        packages=packages,
        postgres_schema_version=base._postgres_schema_version(),
        clickhouse_schema_versions=base._clickhouse_schema_versions(),
        table_metrics=base._table_metrics(),
        orphan_counts=base._orphan_counts(),
        lineage_metrics=base._lineage_metrics(),
        source_kind_case_counts=base._source_kind_case_counts(),
        source_file_verification=(
            base._verify_source_files(packages) if verify_source_files else None
        ),
    )
    return augment_report(
        report,
        packages,
        expected_history_parts=expected_history_parts,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strict read-only US M1.3 real-data acceptance audit"
    )
    parser.add_argument(
        "--verify-source-files",
        action="store_true",
        help="Re-hash every successful authoritative US source file.",
    )
    parser.add_argument(
        "--expected-history-parts",
        type=int,
        default=None,
        help=(
            "Expected number of parts for the latest historical coverage range. Required for "
            "strict PASS because the filename does not encode the trailing part count."
        ),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build_audit(
                verify_source_files=args.verify_source_files,
                expected_history_parts=args.expected_history_parts,
            ),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
