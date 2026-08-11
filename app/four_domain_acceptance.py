from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Callable

from app.config import get_settings


AUDIT_VERSION = "MARKORBIT_FOUR_DOMAIN_ACCEPTANCE_V1"
EXPECTED_AUDITS = {
    "cn": ("audit", "CN_M16_ACCEPTANCE_INTEGRITY"),
    "application": ("audit_version", "US_M14_REAL_DATA_ACCEPTANCE_V2_HISTORY_PARTS"),
    "assignment": ("audit", "US_ASSIGNMENT_MANIFEST_ACCEPTANCE_V1"),
    "ttab": ("audit", "US_TTAB_MANIFEST_ACCEPTANCE_V1"),
}
APPLICATION_ALLOWED_WARNINGS = {"source_sha_verification_not_requested"}
TTAB_ALLOWED_WARNINGS = {
    "malformed_ttab_property_serials_present",
    "some_ttab_property_serials_not_present_in_us_case_current",
    "some_or_all_ttab_snapshots_have_no_docket_rows",
}


def _archive_summary(
    packages: list[dict[str, Any]],
    *,
    archive_root: Path,
) -> dict[str, Any]:
    status_counts = Counter(str(row.get("status") or "UNKNOWN") for row in packages)
    non_success: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    outside_archive: list[dict[str, Any]] = []
    archive_root_resolved = archive_root.resolve()

    for row in packages:
        status = str(row.get("status") or "UNKNOWN")
        base = {
            "file_name": str(row.get("file_name") or ""),
            "status": status,
            "archived_path": str(row.get("archived_path") or ""),
        }
        if status != "SUCCESS":
            non_success.append(base)
            continue
        archived_value = row.get("archived_path")
        if not archived_value:
            missing.append(base)
            continue
        archived = Path(str(archived_value))
        if not archived.is_file():
            missing.append(base)
            continue
        try:
            archived.resolve().relative_to(archive_root_resolved)
        except ValueError:
            outside_archive.append(base)

    return {
        "registered_count": len(packages),
        "success_count": int(status_counts.get("SUCCESS", 0)),
        "status_counts": dict(sorted(status_counts.items())),
        "all_registered_success": bool(packages) and not non_success,
        "non_success_count": len(non_success),
        "missing_archive_count": len(missing),
        "outside_raw_archive_count": len(outside_archive),
        "non_success_samples": non_success[:25],
        "missing_archive_samples": missing[:25],
        "outside_raw_archive_samples": outside_archive[:25],
    }


def build_archive_evidence() -> dict[str, Any]:
    # Imports are intentionally local so policy unit tests remain pure and database-free.
    from app.cn.audit_followup import _packages as list_cn_packages
    from app.us.audit_real_data import _package_rows as list_application_packages
    from app.us_assignment.repository import list_assignment_packages
    from app.us_ttab.repository import list_ttab_packages

    archive_root = get_settings().raw_data_root / "archive"
    package_loaders: dict[str, Callable[[], list[dict[str, Any]]]] = {
        "cn": list_cn_packages,
        "application": list_application_packages,
        "assignment": list_assignment_packages,
        "ttab": list_ttab_packages,
    }
    return {
        domain: _archive_summary(loader(), archive_root=archive_root)
        for domain, loader in package_loaders.items()
    }


def _reason(prefix: str, detail: str) -> str:
    return f"{prefix}:{detail}"


def _check_report_consistency(
    domain: str,
    report: dict[str, Any],
    hard: list[str],
) -> None:
    identity_field, expected_identity = EXPECTED_AUDITS[domain]
    if str(report.get(identity_field) or "") != expected_identity:
        hard.append(_reason(domain, "unexpected_acceptance_report_version"))
    if report.get("hard_fail_reasons"):
        hard.append(_reason(domain, "acceptance_report_has_hard_fail_reasons"))
    if report.get("not_ready_reasons"):
        hard.append(_reason(domain, "acceptance_report_has_not_ready_reasons"))


def evaluate_four_domain_acceptance(
    bundle: dict[str, Any],
    archive_evidence: dict[str, Any],
) -> dict[str, Any]:
    reports = bundle.get("reports") or {}
    policy = bundle.get("policy") or {}
    report_files = bundle.get("report_files") or {}
    hard: list[str] = []
    warnings: list[str] = []
    domain_summary: dict[str, Any] = {}

    expected_history_parts = int(policy.get("expected_application_history_parts") or 0)
    expected_daily_through = str(policy.get("expected_application_daily_through") or "")
    if expected_history_parts < 1:
        hard.append("policy:expected_application_history_parts_not_pinned")
    if not expected_daily_through:
        hard.append("policy:expected_application_daily_through_not_pinned")

    for domain in EXPECTED_AUDITS:
        report = reports.get(domain)
        if not isinstance(report, dict):
            hard.append(_reason(domain, "acceptance_report_missing"))
            continue
        _check_report_consistency(domain, report, hard)
        report_status = str(report.get("status") or "")
        report_warnings = set(str(value) for value in (report.get("warning_reasons") or []))

        if domain == "cn":
            if report_status not in {"PASS", "PASS_WITH_WARNINGS"}:
                hard.append(_reason(domain, f"status_{report_status or 'MISSING'}_not_acceptable"))
            registry = report.get("package_registry") or {}
            if not registry.get("all_success") or int(registry.get("non_success_count") or 0):
                hard.append(_reason(domain, "registered_corpus_not_all_success"))
            if report_status == "PASS_WITH_WARNINGS":
                warnings.extend(_reason(domain, value) for value in sorted(report_warnings))

        elif domain == "application":
            if report_status not in {"PASS", "PASS_WITH_WARNINGS"}:
                hard.append(_reason(domain, f"status_{report_status or 'MISSING'}_not_acceptable"))
            unexpected = report_warnings - APPLICATION_ALLOWED_WARNINGS
            if unexpected:
                hard.append(_reason(domain, "unexpected_acceptance_warnings"))
            completeness = report.get("historical_part_completeness") or {}
            if not completeness.get("complete"):
                hard.append(_reason(domain, "historical_parts_not_complete"))
            if int(completeness.get("expected_history_parts") or 0) != expected_history_parts:
                hard.append(_reason(domain, "historical_part_count_does_not_match_pinned_policy"))
            actual_daily_end = str((report.get("coverage") or {}).get("daily_end") or "")
            if expected_daily_through and actual_daily_end != expected_daily_through:
                hard.append(_reason(domain, "daily_coverage_end_does_not_match_pinned_policy"))
            if report_status == "PASS_WITH_WARNINGS":
                warnings.extend(_reason(domain, value) for value in sorted(report_warnings))

        elif domain == "assignment":
            if report_status != "PASS":
                hard.append(_reason(domain, f"status_{report_status or 'MISSING'}_not_formal_pass"))
            if report_warnings:
                hard.append(_reason(domain, "formal_pass_contains_warnings"))
            registry = report.get("manifest_registry") or {}
            if (
                registry.get("missing_registry_sha256")
                or registry.get("extra_registry_sha256")
                or registry.get("incomplete_sha256")
                or registry.get("metadata_mismatches")
            ):
                hard.append(_reason(domain, "manifest_registry_identity_not_exact"))
            if report.get("legal_ownership_conclusion") is not False:
                hard.append(_reason(domain, "ownership_semantics_guard_missing"))

        elif domain == "ttab":
            if report_status not in {"PASS", "PASS_WITH_WARNINGS"}:
                hard.append(_reason(domain, f"status_{report_status or 'MISSING'}_not_acceptable"))
            unexpected = report_warnings - TTAB_ALLOWED_WARNINGS
            if unexpected:
                hard.append(_reason(domain, "warnings_outside_allowed_coverage_set"))
            registry = report.get("manifest_registry") or {}
            if (
                registry.get("missing_registry_sha256")
                or registry.get("extra_registry_sha256")
                or registry.get("incomplete_sha256")
                or registry.get("metadata_mismatches")
            ):
                hard.append(_reason(domain, "manifest_registry_identity_not_exact"))
            if (
                report.get("deadline_validity_inference") is not False
                or report.get("legal_outcome_conclusion") is not False
                or report.get("substantive_rights_conclusion") is not False
            ):
                hard.append(_reason(domain, "procedural_semantics_guard_missing"))
            if report_status == "PASS_WITH_WARNINGS":
                warnings.extend(_reason(domain, value) for value in sorted(report_warnings))

        archive = archive_evidence.get(domain) or {}
        if not archive.get("all_registered_success"):
            hard.append(_reason(domain, "archive_check_registered_corpus_not_all_success"))
        if int(archive.get("missing_archive_count") or 0):
            hard.append(_reason(domain, "successful_source_archive_missing"))
        if int(archive.get("outside_raw_archive_count") or 0):
            hard.append(_reason(domain, "successful_source_outside_raw_archive"))

        identity_field, _ = EXPECTED_AUDITS[domain]
        domain_summary[domain] = {
            "status": report_status,
            "acceptance_identity": report.get(identity_field),
            "warning_reasons": sorted(report_warnings),
            "archive_evidence": archive,
            "report_file": report_files.get(domain),
        }

    hard = sorted(set(hard))
    warnings = sorted(set(warnings))
    status = "FAIL" if hard else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    return {
        "audit": AUDIT_VERSION,
        "status": status,
        "hard_fail_reasons": hard,
        "warning_reasons": warnings,
        "policy": {
            "expected_application_history_parts": expected_history_parts,
            "expected_application_daily_through": expected_daily_through,
            "application_allowed_warnings": sorted(APPLICATION_ALLOWED_WARNINGS),
            "ttab_allowed_warnings": sorted(TTAB_ALLOWED_WARNINGS),
        },
        "domains": domain_summary,
        "lineage_policy": "EACH_DOMAIN_MUST_PASS_ITS_OWN_SOURCE_LINEAGE_AND_PRECEDENCE_GATES",
        "source_archive_policy": "ALL_REGISTERED_SUCCESS_SOURCES_MUST_EXIST_UNDER_RAW_ARCHIVE",
        "semantics": {
            "cross_domain_legal_event_order_inference": False,
            "assignment_recordation_is_ownership_conclusion": False,
            "ttab_procedure_is_substantive_outcome_conclusion": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate formal CN/Application/Assignment/TTAB acceptance reports"
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the acceptance-report bundle as JSON from stdin.",
    )
    args = parser.parse_args()
    if not args.stdin:
        parser.error("--stdin is required")
    bundle = json.load(sys.stdin)
    report = evaluate_four_domain_acceptance(bundle, build_archive_evidence())
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] in {"PASS", "PASS_WITH_WARNINGS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
