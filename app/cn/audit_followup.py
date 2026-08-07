from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from app.cn.reader import iter_member_rows
from app.cn.text import application_number_parts, clean_text, parse_class
from app.cn.zipio import iter_package_members
from app.db import clickhouse_client, postgres_conn


FOCUS_ISSUES = {
    "GOODS_WITHOUT_BASIC",
    "APPLICANT_WITHOUT_BASIC",
    "BASIC_WITHOUT_GOODS",
}


def _packages() -> list[dict[str, Any]]:
    sql = """
    SELECT package_id, file_name, file_path, archived_path, status,
           partition_value, profile, error_message, source_rank
    FROM control.source_package
    WHERE jurisdiction = 'CN'
    ORDER BY source_rank, file_name
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


def _quality_examples() -> list[dict[str, Any]]:
    sql = """
    SELECT sp.file_name AS package_file, q.issue_type, q.occurrence_count,
           q.source_file, q.source_row, q.raw_excerpt, q.details
    FROM control.data_quality_issue AS q
    JOIN control.source_package AS sp ON sp.package_id = q.package_id
    WHERE q.jurisdiction = 'CN'
      AND q.issue_type = ANY(%s)
    ORDER BY sp.source_rank, q.issue_type
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (list(FOCUS_ISSUES),))
            return [dict(row) for row in cur.fetchall()]


def _orphan_parties(package_names: dict[str, str]) -> list[dict[str, Any]]:
    client = clickhouse_client()
    result = client.query(
        """
        SELECT
            p.application_number,
            p.role,
            p.raw_name,
            p.raw_address,
            toString(p.last_source_package_id) AS source_package_id,
            p.source_file,
            p.source_first_line,
            p.source_rank
        FROM markorbit_facts.cn_case_party_current AS p FINAL
        LEFT JOIN markorbit_facts.cn_case_current AS c FINAL
          ON c.application_number = p.application_number AND c.is_deleted = 0
        WHERE p.is_deleted = 0
          AND p.is_current = 1
          AND c.application_number = ''
        ORDER BY p.application_number, p.role, p.raw_name
        LIMIT 100
        """
    )
    rows: list[dict[str, Any]] = []
    for values in result.result_rows:
        row = dict(zip(result.column_names, values, strict=True))
        source_package_id = str(row.get("source_package_id") or "")
        row["package_file"] = package_names.get(source_package_id)
        row["unregistered_source_package"] = source_package_id not in package_names
        rows.append(row)
    return rows


def _role_deltas(package: dict[str, Any]) -> dict[str, int]:
    profile = package.get("profile") or {}
    totals = profile.get("totals") or {}
    role_counts = totals.get("role_counts") or {}
    stage_counts = totals.get("stage_counts") or {}
    staged = {
        "basic": int(stage_counts.get("markorbit_facts.cn_stage_basic", 0) or 0),
        "applicant": int(stage_counts.get("markorbit_facts.cn_stage_applicant", 0) or 0),
        "goods": int(stage_counts.get("markorbit_facts.cn_stage_goods", 0) or 0),
        "agent": int(stage_counts.get("markorbit_facts.cn_stage_agent", 0) or 0),
        "priority": int(stage_counts.get("markorbit_facts.cn_stage_priority", 0) or 0),
        "madrid": int(stage_counts.get("markorbit_facts.cn_stage_madrid", 0) or 0),
        "coowner": int(stage_counts.get("markorbit_facts.cn_stage_coowner", 0) or 0),
    }
    return {
        role: int(parsed or 0) - staged.get(role, 0)
        for role, parsed in role_counts.items()
        if int(parsed or 0) - staged.get(role, 0) > 0
    }


def _goods_drop_reason(record: dict[str, str]) -> str | None:
    parts = application_number_parts(record.get("application_number"))
    if not parts.full:
        return "INVALID_OR_EMPTY_APPLICATION_NUMBER"
    if parse_class(record.get("class_no")) is None:
        return "INVALID_OR_EMPTY_CLASS"
    return None


def _scan_goods_drops(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for package in packages:
        deltas = _role_deltas(package)
        expected_drop = int(deltas.get("goods", 0) or 0)
        if expected_drop <= 0:
            continue

        source_path = package.get("archived_path") or package.get("file_path")
        if not source_path or not Path(str(source_path)).exists():
            output.append(
                {
                    "file_name": package["file_name"],
                    "expected_drop": expected_drop,
                    "error": f"source package not accessible: {source_path}",
                }
            )
            continue

        counts: Counter[str] = Counter()
        examples: list[dict[str, Any]] = []
        for member in iter_package_members(Path(str(source_path))):
            if member.schema is None or member.schema.role != "goods":
                continue
            _, rows = iter_member_rows(member)
            for parsed in rows:
                reason = _goods_drop_reason(parsed.record)
                if not reason:
                    continue
                counts[reason] += 1
                if len(examples) < 50:
                    examples.append(
                        {
                            "member": member.internal_name,
                            "line": parsed.source_start_line,
                            "application_number": clean_text(parsed.record.get("application_number")),
                            "class_raw": clean_text(parsed.record.get("class_no")),
                            "goods_sequence": clean_text(parsed.record.get("goods_sequence")),
                            "goods_name": clean_text(parsed.record.get("goods_name"), preserve_newlines=True)[:300],
                            "reason": reason,
                        }
                    )
        output.append(
            {
                "file_name": package["file_name"],
                "expected_drop": expected_drop,
                "explained_drop": sum(counts.values()),
                "reasons": dict(counts),
                "examples": examples,
            }
        )
    return output


def main() -> None:
    packages = _packages()
    package_names = {str(row["package_id"]): str(row["file_name"]) for row in packages}
    failed_packages = [
        {
            "file_name": row["file_name"],
            "partition_value": row.get("partition_value"),
            "status": row["status"],
            "file_path": row.get("file_path"),
            "archived_path": row.get("archived_path"),
            "error_message": row.get("error_message"),
        }
        for row in packages
        if row["status"] == "FAILED"
    ]

    result = {
        "audit": "CN_M1_INTEGRITY_FOLLOWUP",
        "failed_packages": failed_packages,
        "orphan_parties": _orphan_parties(package_names),
        "goods_parse_to_stage_drops": _scan_goods_drops(packages),
        "cross_file_quality_examples": _quality_examples(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
