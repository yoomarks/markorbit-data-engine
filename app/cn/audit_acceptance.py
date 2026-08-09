from __future__ import annotations

import json
from typing import Any

from app.cn.audit_data import build_audit
from app.cn.audit_followup import _orphan_parties, _packages, _scan_goods_drops


POLICY_VERSION = "CN_M16_ACCEPTANCE_POLICY_V1_SOURCE_BACKED_INCOMPLETE_WARN"


def _int_sum(values: dict[str, Any]) -> int:
    return sum(int(value or 0) for value in values.values())


def evaluate_acceptance(
    audit: dict[str, Any],
    orphan_parties: list[dict[str, Any]],
    goods_drop_scan: list[dict[str, Any]],
) -> dict[str, Any]:
    package_contract = audit.get("package_contract") or {}
    clickhouse = audit.get("clickhouse") or {}
    quality = audit.get("quality") or {}
    deep = audit.get("deep_raw_scan") or {}

    total_dropped = int(package_contract.get("total_parsed_to_stage_dropped") or 0)
    total_failed_rows = int(package_contract.get("total_failed_rows") or 0)
    source_replacement_chars = int(package_contract.get("total_replacement_chars") or 0)

    party_drop_explained = 0
    rows_with_replacement_after_parse = 0
    deep_scan_errors: list[dict[str, Any]] = []
    for package in deep.get("packages") or []:
        if package.get("error"):
            deep_scan_errors.append(package)
        party_drop_explained += _int_sum(package.get("party_drop_reasons") or {})
        rows_with_replacement_after_parse += int(package.get("rows_with_replacement_after_parse") or 0)

    goods_drop_expected = 0
    goods_drop_explained = 0
    goods_drop_errors: list[dict[str, Any]] = []
    for row in goods_drop_scan:
        expected = int(row.get("expected_drop") or 0)
        explained = int(row.get("explained_drop") or 0)
        goods_drop_expected += expected
        goods_drop_explained += explained
        if row.get("error") or expected != explained:
            goods_drop_errors.append(row)

    explained_total_dropped = party_drop_explained + goods_drop_explained
    unexplained_dropped = total_dropped - explained_total_dropped

    duplicates = clickhouse.get("duplicates_after_final") or {}
    replacement_rows = clickhouse.get("replacement_character_rows") or {}
    orphans = clickhouse.get("orphans") or {}
    scope_without_case = int(orphans.get("scope_without_case") or 0)
    party_without_case = int(orphans.get("party_without_case") or 0)

    traceable_orphans: list[dict[str, Any]] = []
    untraceable_orphans: list[dict[str, Any]] = []
    for row in orphan_parties:
        traceable = bool(
            row.get("package_file")
            and not row.get("unregistered_source_package")
            and row.get("source_file")
            and row.get("source_first_line")
        )
        if traceable:
            traceable_orphans.append(row)
        else:
            untraceable_orphans.append(row)

    orphan_inventory_complete = party_without_case == len(orphan_parties)
    final_replacement_rows = _int_sum(replacement_rows)
    final_duplicate_rows = _int_sum(duplicates)
    unmapped_goods_status_codes = quality.get("unmapped_goods_status_codes") or {}

    hard_fail_reasons: list[str] = []
    if total_failed_rows:
        hard_fail_reasons.append("parser_failed_rows")
    if deep_scan_errors:
        hard_fail_reasons.append("deep_raw_scan_errors")
    if goods_drop_errors:
        hard_fail_reasons.append("unexplained_goods_parse_to_stage_drops")
    if unexplained_dropped != 0:
        hard_fail_reasons.append("unreconciled_parse_to_stage_drops")
    if rows_with_replacement_after_parse:
        hard_fail_reasons.append("replacement_characters_survive_parser")
    if final_replacement_rows:
        hard_fail_reasons.append("replacement_characters_in_final_tables")
    if final_duplicate_rows:
        hard_fail_reasons.append("duplicates_after_final")
    if scope_without_case:
        hard_fail_reasons.append("scope_without_case")
    if not orphan_inventory_complete:
        hard_fail_reasons.append("orphan_party_inventory_incomplete")
    if untraceable_orphans:
        hard_fail_reasons.append("untraceable_party_without_case")
    if unmapped_goods_status_codes:
        hard_fail_reasons.append("unmapped_goods_status_codes")

    warning_reasons: list[str] = []
    if total_dropped:
        warning_reasons.append("source_rows_filtered_by_documented_validation")
    if source_replacement_chars:
        warning_reasons.append("source_invalid_bytes_repaired_before_final_publish")
    if traceable_orphans:
        warning_reasons.append("source_backed_incomplete_official_party_records")
    if quality.get("occurrences_by_type"):
        warning_reasons.append("source_cross_file_or_date_quality_anomalies")

    status = "FAIL" if hard_fail_reasons else ("PASS_WITH_WARNINGS" if warning_reasons else "PASS")

    return {
        "status": status,
        "audit": "CN_M16_ACCEPTANCE_INTEGRITY",
        "policy_version": POLICY_VERSION,
        "legacy_audit_status": audit.get("status"),
        "hard_fail_reasons": hard_fail_reasons,
        "warning_reasons": warning_reasons,
        "reconciliation": {
            "parsed_to_stage_dropped": total_dropped,
            "party_drops_explained": party_drop_explained,
            "goods_drops_expected": goods_drop_expected,
            "goods_drops_explained": goods_drop_explained,
            "explained_total_dropped": explained_total_dropped,
            "unexplained_dropped": unexplained_dropped,
            "parser_failed_rows": total_failed_rows,
            "source_replacement_chars": source_replacement_chars,
            "rows_with_replacement_after_parse": rows_with_replacement_after_parse,
            "final_replacement_rows": final_replacement_rows,
            "final_duplicate_rows": final_duplicate_rows,
            "unmapped_goods_status_codes": unmapped_goods_status_codes,
        },
        "orphan_policy": {
            "scope_without_case": scope_without_case,
            "party_without_case": party_without_case,
            "inventory_complete": orphan_inventory_complete,
            "source_backed_party_without_case": len(traceable_orphans),
            "untraceable_party_without_case": len(untraceable_orphans),
            "source_backed_samples": traceable_orphans[:25],
            "untraceable_samples": untraceable_orphans[:25],
        },
        "source_quality_occurrences": quality.get("occurrences_by_type") or {},
        "policy_note": (
            "Source-backed incomplete official records are warnings, not engine failures. "
            "FAIL is reserved for unreconciled row loss, parser/final corruption, final duplicates, "
            "scope orphans, untraceable party orphans, unmapped goods status codes, or incomplete evidence inventory."
        ),
    }


def build_acceptance_audit() -> dict[str, Any]:
    audit = build_audit(deep=True)
    packages = _packages()
    package_names = {str(row["package_id"]): str(row["file_name"]) for row in packages}
    orphan_parties = _orphan_parties(package_names)
    goods_drop_scan = _scan_goods_drops(packages)
    return evaluate_acceptance(audit, orphan_parties, goods_drop_scan)


def main() -> None:
    print(json.dumps(build_acceptance_audit(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
