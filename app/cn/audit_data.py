from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import json
from typing import Any

from app.cn.reader import iter_member_rows
from app.cn.text import application_number_parts, clean_text, strip_cn_id_mask_suffix
from app.cn.zipio import iter_package_members
from app.db import clickhouse_client, postgres_conn


STAGE_ROLE_MAP = {
    "markorbit_facts.cn_stage_basic": "basic",
    "markorbit_facts.cn_stage_applicant": "applicant",
    "markorbit_facts.cn_stage_goods": "goods",
    "markorbit_facts.cn_stage_agent": "agent",
    "markorbit_facts.cn_stage_priority": "priority",
    "markorbit_facts.cn_stage_madrid": "madrid",
    "markorbit_facts.cn_stage_coowner": "coowner",
}


def _json_default(value: Any) -> str:
    return str(value)


def _fetch_packages() -> list[dict[str, Any]]:
    sql = """
    SELECT package_id, file_name, package_kind, partition_dimension, partition_value,
           status, profile, archived_path, source_rank
    FROM control.source_package
    WHERE jurisdiction = 'CN'
    ORDER BY source_rank, file_name
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


def _fetch_package_files() -> list[dict[str, Any]]:
    sql = """
    SELECT sp.package_id, sp.file_name AS package_file, sp.partition_value,
           f.internal_name, f.file_role, f.content_encoding, f.physical_rows,
           f.logical_rows, f.continuation_rows, f.repaired_rows, f.failed_rows,
           f.replacement_chars, f.metrics
    FROM control.source_package_file AS f
    JOIN control.source_package AS sp ON sp.package_id = f.package_id
    WHERE sp.jurisdiction = 'CN'
    ORDER BY sp.source_rank, f.internal_name
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


def _fetch_quality_issues() -> list[dict[str, Any]]:
    sql = """
    SELECT sp.file_name AS package_file, sp.partition_value,
           q.issue_type, q.severity, q.occurrence_count, q.source_file,
           q.source_row, q.raw_excerpt, q.details
    FROM control.data_quality_issue AS q
    JOIN control.source_package AS sp ON sp.package_id = q.package_id
    WHERE q.jurisdiction = 'CN'
    ORDER BY sp.source_rank, q.issue_type, q.source_file
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


def _package_contract(packages: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total_dropped = 0
    total_failed = 0
    total_replacements = 0
    for package in packages:
        profile = package.get("profile") or {}
        totals = profile.get("totals") or {}
        role_counts = totals.get("role_counts") or {}
        stage_counts = totals.get("stage_counts") or {}
        staged_by_role: dict[str, int] = defaultdict(int)
        for table, count in stage_counts.items():
            role = STAGE_ROLE_MAP.get(table)
            if role:
                staged_by_role[role] += int(count or 0)
        deltas = {
            role: int(role_counts.get(role, 0) or 0) - int(staged_by_role.get(role, 0) or 0)
            for role in sorted(set(role_counts) | set(staged_by_role))
        }
        deltas = {role: value for role, value in deltas.items() if value != 0}
        dropped = sum(value for value in deltas.values() if value > 0)
        failed = int(totals.get("failed_rows", 0) or 0)
        replacements = int(totals.get("replacement_chars", 0) or 0)
        total_dropped += dropped
        total_failed += failed
        total_replacements += replacements
        publish = totals.get("publish") or {}
        rows.append(
            {
                "file_name": package["file_name"],
                "partition_value": package.get("partition_value"),
                "status": package["status"],
                "parsed_role_counts": role_counts,
                "staged_role_counts": dict(staged_by_role),
                "parsed_to_stage_delta": deltas,
                "failed_rows": failed,
                "replacement_chars": replacements,
                "publish": publish,
            }
        )
    return {
        "total_parsed_to_stage_dropped": total_dropped,
        "total_failed_rows": total_failed,
        "total_replacement_chars": total_replacements,
        "packages": rows,
    }


def _file_encoding_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
    problem_files = []
    for row in files:
        replacements = int(row.get("replacement_chars") or 0)
        failed = int(row.get("failed_rows") or 0)
        metrics = row.get("metrics") or {}
        mojibake_fixed = int(metrics.get("mojibake_cells_repaired", 0) or 0)
        if replacements or failed or mojibake_fixed:
            problem_files.append(
                {
                    "package_file": row["package_file"],
                    "internal_name": row["internal_name"],
                    "role": row["file_role"],
                    "encoding": row["content_encoding"],
                    "logical_rows": int(row.get("logical_rows") or 0),
                    "failed_rows": failed,
                    "replacement_chars": replacements,
                    "mojibake_cells_repaired": mojibake_fixed,
                }
            )
    return {
        "problem_file_count": len(problem_files),
        "problem_files": problem_files,
    }


def _quality_summary(issues: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: Counter[str] = Counter()
    unmapped_codes: Counter[str] = Counter()
    for row in issues:
        count = int(row.get("occurrence_count") or 0)
        issue_type = str(row.get("issue_type") or "")
        by_type[issue_type] += count
        if issue_type == "UNMAPPED_GOODS_STATUS_CODE":
            unmapped_codes[str(row.get("raw_excerpt") or "<BLANK>")] += count
    return {
        "occurrences_by_type": dict(by_type.most_common()),
        "unmapped_goods_status_codes": dict(unmapped_codes.most_common()),
    }


def _clickhouse_integrity() -> dict[str, Any]:
    client = clickhouse_client()
    replacement = client.query(
        """
        SELECT
            countIf(position(mark_name_raw, '�') > 0) AS case_mark_name,
            countIf(position(design_description, '�') > 0) AS case_design_description,
            countIf(position(color_description, '�') > 0) AS case_color_description,
            countIf(position(exclusive_rights_disclaimer, '�') > 0) AS case_disclaimer
        FROM markorbit_facts.cn_case_current FINAL
        WHERE is_deleted = 0
        """
    ).result_rows[0]
    party_replacement = client.query(
        """
        SELECT
            countIf(position(raw_name, '�') > 0) AS party_name,
            countIf(position(raw_address, '�') > 0) AS party_address
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0 AND is_current = 1
        """
    ).result_rows[0]
    scope_replacement = client.query(
        """
        SELECT countIf(position(goods_items_compact, '�') > 0)
        FROM markorbit_facts.cn_case_scope_current FINAL
        WHERE is_deleted = 0
        """
    ).result_rows[0][0]

    uniqueness = client.query(
        """
        SELECT
            (SELECT count() - uniqExact(application_number)
             FROM markorbit_facts.cn_case_current FINAL WHERE is_deleted = 0) AS duplicate_cases,
            (SELECT count() - uniqExact(tuple(application_number, class_no))
             FROM markorbit_facts.cn_case_scope_current FINAL WHERE is_deleted = 0) AS duplicate_scopes,
            (SELECT count() - uniqExact(tuple(application_number, role, relation_key))
             FROM markorbit_facts.cn_case_party_current FINAL
             WHERE is_deleted = 0 AND is_current = 1) AS duplicate_current_parties
        """
    ).result_rows[0]

    orphan_counts = client.query(
        """
        SELECT
            (SELECT count()
             FROM markorbit_facts.cn_case_scope_current AS s FINAL
             LEFT JOIN markorbit_facts.cn_case_current AS c FINAL
               ON c.application_number = s.application_number AND c.is_deleted = 0
             WHERE s.is_deleted = 0 AND c.application_number = '') AS scope_without_case,
            (SELECT count()
             FROM markorbit_facts.cn_case_party_current AS p FINAL
             LEFT JOIN markorbit_facts.cn_case_current AS c FINAL
               ON c.application_number = p.application_number AND c.is_deleted = 0
             WHERE p.is_deleted = 0 AND p.is_current = 1 AND c.application_number = '') AS party_without_case
        """
    ).result_rows[0]

    replacement_samples = client.query(
        """
        SELECT application_number, mark_name_raw, source_file, source_first_line
        FROM markorbit_facts.cn_case_current FINAL
        WHERE is_deleted = 0
          AND (position(mark_name_raw, '�') > 0
            OR position(design_description, '�') > 0
            OR position(color_description, '�') > 0
            OR position(exclusive_rights_disclaimer, '�') > 0)
        LIMIT 10
        """
    ).result_rows
    party_samples = client.query(
        """
        SELECT application_number, role, raw_name, raw_address, source_file, source_first_line
        FROM markorbit_facts.cn_case_party_current FINAL
        WHERE is_deleted = 0 AND is_current = 1
          AND (position(raw_name, '�') > 0 OR position(raw_address, '�') > 0)
        LIMIT 10
        """
    ).result_rows
    goods_samples = client.query(
        """
        SELECT application_number, class_no, source_file, source_first_line
        FROM markorbit_facts.cn_case_scope_current FINAL
        WHERE is_deleted = 0 AND position(goods_items_compact, '�') > 0
        LIMIT 10
        """
    ).result_rows

    return {
        "replacement_character_rows": {
            "case_mark_name": int(replacement[0] or 0),
            "case_design_description": int(replacement[1] or 0),
            "case_color_description": int(replacement[2] or 0),
            "case_disclaimer": int(replacement[3] or 0),
            "party_name": int(party_replacement[0] or 0),
            "party_address": int(party_replacement[1] or 0),
            "goods_scope": int(scope_replacement or 0),
        },
        "replacement_samples": {
            "cases": replacement_samples,
            "parties": party_samples,
            "goods_scopes": goods_samples,
        },
        "duplicates_after_final": {
            "cases": int(uniqueness[0] or 0),
            "scopes": int(uniqueness[1] or 0),
            "current_parties": int(uniqueness[2] or 0),
        },
        "orphans": {
            "scope_without_case": int(orphan_counts[0] or 0),
            "party_without_case": int(orphan_counts[1] or 0),
        },
    }


def _party_drop_reason(role: str, record: dict[str, str]) -> str | None:
    parts = application_number_parts(record.get("application_number"))
    if not parts.full:
        return "INVALID_OR_EMPTY_APPLICATION_NUMBER"
    if role == "applicant":
        raw_name = record.get("owner_name_cn") or record.get("owner_name_foreign") or ""
    else:
        raw_name = record.get("coowner_name_cn") or record.get("coowner_name_foreign") or ""
    if not strip_cn_id_mask_suffix(raw_name):
        return "EMPTY_PARTY_NAME"
    return None


def _deep_raw_scan(packages: list[dict[str, Any]], files: list[dict[str, Any]]) -> dict[str, Any]:
    replacement_problem_members: dict[str, set[str]] = defaultdict(set)
    for row in files:
        if int(row.get("replacement_chars") or 0) > 0:
            replacement_problem_members[str(row["package_id"])].add(str(row["internal_name"]))

    results: list[dict[str, Any]] = []
    for package in packages:
        profile = package.get("profile") or {}
        totals = profile.get("totals") or {}
        role_counts = totals.get("role_counts") or {}
        stage_counts = totals.get("stage_counts") or {}
        staged_by_role: dict[str, int] = defaultdict(int)
        for table, count in stage_counts.items():
            role = STAGE_ROLE_MAP.get(table)
            if role:
                staged_by_role[role] += int(count or 0)
        roles_with_drop = {
            role
            for role, parsed in role_counts.items()
            if int(parsed or 0) > int(staged_by_role.get(role, 0) or 0)
        }
        target_members = replacement_problem_members.get(str(package["package_id"]), set())
        if not roles_with_drop and not target_members:
            continue
        archive = package.get("archived_path")
        if not archive or not Path(str(archive)).exists():
            results.append(
                {
                    "file_name": package["file_name"],
                    "error": f"archive not accessible: {archive}",
                }
            )
            continue

        drop_counts: Counter[str] = Counter()
        drop_examples: list[dict[str, Any]] = []
        replacement_rows = 0
        replacement_examples: list[dict[str, Any]] = []
        for member in iter_package_members(Path(str(archive))):
            if member.schema is None:
                continue
            role = member.schema.role
            inspect_drop = role in roles_with_drop and role in {"applicant", "coowner"}
            inspect_replacement = member.internal_name in target_members
            if not inspect_drop and not inspect_replacement:
                continue
            _, rows = iter_member_rows(member)
            for parsed in rows:
                if inspect_drop:
                    reason = _party_drop_reason(role, parsed.record)
                    if reason:
                        drop_counts[f"{role}:{reason}"] += 1
                        if len(drop_examples) < 30:
                            drop_examples.append(
                                {
                                    "member": member.internal_name,
                                    "role": role,
                                    "line": parsed.source_start_line,
                                    "application_number": clean_text(parsed.record.get("application_number")),
                                    "reason": reason,
                                }
                            )
                if inspect_replacement:
                    bad_fields = [
                        key for key, value in parsed.record.items()
                        if isinstance(value, str) and "�" in value
                    ]
                    if bad_fields:
                        replacement_rows += 1
                        if len(replacement_examples) < 30:
                            replacement_examples.append(
                                {
                                    "member": member.internal_name,
                                    "role": role,
                                    "line": parsed.source_start_line,
                                    "application_number": clean_text(parsed.record.get("application_number")),
                                    "fields": bad_fields,
                                    "values": {key: parsed.record.get(key) for key in bad_fields},
                                }
                            )
        results.append(
            {
                "file_name": package["file_name"],
                "party_drop_reasons": dict(drop_counts),
                "party_drop_examples": drop_examples,
                "rows_with_replacement_after_parse": replacement_rows,
                "replacement_examples": replacement_examples,
            }
        )
    return {"packages": results}


def build_audit(deep: bool = False) -> dict[str, Any]:
    packages = _fetch_packages()
    files = _fetch_package_files()
    issues = _fetch_quality_issues()
    package_contract = _package_contract(packages)
    encoding = _file_encoding_summary(files)
    quality = _quality_summary(issues)
    clickhouse = _clickhouse_integrity()

    hard_fail = (
        package_contract["total_failed_rows"] > 0
        or any(clickhouse["duplicates_after_final"].values())
        or any(clickhouse["orphans"].values())
    )
    warnings = (
        package_contract["total_parsed_to_stage_dropped"] > 0
        or package_contract["total_replacement_chars"] > 0
        or bool(quality["unmapped_goods_status_codes"])
        or any(clickhouse["replacement_character_rows"].values())
    )
    status = "FAIL" if hard_fail else ("WARN" if warnings else "PASS")

    result: dict[str, Any] = {
        "status": status,
        "audit": "CN_M1_DATA_INTEGRITY",
        "package_contract": package_contract,
        "encoding": encoding,
        "quality": quality,
        "clickhouse": clickhouse,
    }
    if deep:
        result["deep_raw_scan"] = _deep_raw_scan(packages, files)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = build_audit(deep=args.deep)
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=_json_default)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
