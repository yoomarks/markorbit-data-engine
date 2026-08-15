from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from app.cn.goods_lifecycle import ApplicationRange
from app.db import clickhouse_client


QUALITY_SUBTASK_TARGET_ROWS = 1_000_000
_ALLOWED_STAGE_TABLES = {
    "markorbit_facts.cn_stage_basic",
    "markorbit_facts.cn_stage_goods",
    "markorbit_facts.cn_stage_applicant",
}


@dataclass(frozen=True)
class QualitySubtaskResult:
    issues: list[dict[str, Any]]
    subtask_count: int
    range_counts: dict[str, int]


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _range_predicate(application_range: ApplicationRange, alias: str = "") -> str:
    column = f"{alias}.application_number" if alias else "application_number"
    return application_range.and_predicate(column)


def _range_label(application_range: ApplicationRange) -> str:
    return f"[{application_range.lower or '-inf'},{application_range.upper or '+inf'})"


def _run_query(
    client: Any,
    *,
    subtask: str,
    application_range: ApplicationRange | None,
    sql: str,
):
    try:
        return client.query(sql)
    except Exception as exc:
        range_text = _range_label(application_range) if application_range else "planner"
        raise RuntimeError(
            f"CN_M1.6 phase=STAGE_QUALITY subtask={subtask} range={range_text} failed: {exc}"
        ) from exc


def plan_application_ranges(
    package_uuid: uuid.UUID | str,
    table: str,
    *,
    client: Any | None = None,
    target_rows: int = QUALITY_SUBTASK_TARGET_ROWS,
) -> list[ApplicationRange]:
    """Plan whole-application windows bounded by source-table row count.

    The integrity audit used to DISTINCT/JOIN an entire package at once. A large
    monthly patch can contain hundreds of millions of staged rows, so that shape
    has no useful memory bound. The stage tables are keyed by package/application;
    walking application-number boundaries keeps every source row in exactly one
    deterministic subtask while allowing ClickHouse to prune each window.
    """
    if table not in _ALLOWED_STAGE_TABLES:
        raise ValueError(f"Unsupported stage table: {table}")
    if target_rows < 1:
        raise ValueError("target_rows must be positive")

    client = client or clickhouse_client()
    package = str(package_uuid)
    first_rows = _run_query(
        client,
        subtask=f"PLAN_{table.rsplit('_', 1)[-1].upper()}",
        application_range=None,
        sql=(
            "SELECT application_number "
            f"FROM {table} WHERE package_id = toUUID('{package}') "
            "ORDER BY application_number LIMIT 1"
        ),
    ).result_rows
    if not first_rows:
        return []

    ranges: list[ApplicationRange] = []
    lower: str | None = None
    while True:
        lower_sql = ""
        if lower is not None:
            lower_sql = f" AND application_number >= {_sql_string(lower)}"
        rows = _run_query(
            client,
            subtask=f"PLAN_{table.rsplit('_', 1)[-1].upper()}",
            application_range=ApplicationRange(lower=lower, upper=None),
            sql=(
                "SELECT application_number "
                f"FROM {table} WHERE package_id = toUUID('{package}')"
                f"{lower_sql} ORDER BY application_number "
                f"LIMIT 1 OFFSET {int(target_rows)}"
            ),
        ).result_rows
        if not rows:
            ranges.append(ApplicationRange(lower=lower, upper=None))
            break

        boundary = str(rows[0][0])
        if lower is not None and boundary <= lower:
            next_rows = _run_query(
                client,
                subtask=f"PLAN_{table.rsplit('_', 1)[-1].upper()}",
                application_range=ApplicationRange(lower=lower, upper=None),
                sql=(
                    "SELECT application_number "
                    f"FROM {table} WHERE package_id = toUUID('{package}') "
                    f"AND application_number > {_sql_string(lower)} "
                    "ORDER BY application_number LIMIT 1"
                ),
            ).result_rows
            if not next_rows:
                ranges.append(ApplicationRange(lower=lower, upper=None))
                break
            boundary = str(next_rows[0][0])

        ranges.append(ApplicationRange(lower=lower, upper=boundary))
        lower = boundary
    return ranges


def _append_examples(target: list[Any], values: Any, limit: int) -> None:
    for value in values or []:
        if len(target) >= limit:
            return
        target.append(value)


def _issue(
    package_uuid: uuid.UUID,
    run_id: uuid.UUID,
    issue_type: str,
    occurrence_count: int,
    examples: list[Any],
    *,
    raw_excerpt: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(details or {})
    payload["examples"] = examples
    return {
        "package_id": package_uuid,
        "run_id": run_id,
        "issue_type": issue_type,
        "severity": "WARNING",
        "occurrence_count": int(occurrence_count),
        "source_file": None,
        "source_row": None,
        "raw_excerpt": raw_excerpt,
        "details": payload,
    }


def _integrity_sql(
    package: str,
    source_table: str,
    target_table: str,
    application_range: ApplicationRange,
    source_alias: str,
    target_alias: str,
) -> str:
    source_range = _range_predicate(application_range)
    target_range = _range_predicate(application_range)
    return f"""
        SELECT count(), groupArray(10)(tuple(src.application_number, src.class_no))
        FROM
        (
            SELECT application_number, class_no
            FROM {source_table}
            WHERE package_id = toUUID('{package}'){source_range}
            GROUP BY application_number, class_no
        ) AS src
        LEFT JOIN
        (
            SELECT application_number, class_no
            FROM {target_table}
            WHERE package_id = toUUID('{package}'){target_range}
            GROUP BY application_number, class_no
        ) AS dst USING (application_number, class_no)
        WHERE dst.application_number = ''
        SETTINGS max_threads = 1
    """


def collect_stage_quality_issues_bounded(
    package_uuid: uuid.UUID,
    run_id: uuid.UUID,
    *,
    client: Any | None = None,
    target_rows: int = QUALITY_SUBTASK_TARGET_ROWS,
) -> QualitySubtaskResult:
    client = client or clickhouse_client()
    package = str(package_uuid)
    issues: list[dict[str, Any]] = []
    subtask_count = 0

    basic_ranges = plan_application_ranges(
        package_uuid,
        "markorbit_facts.cn_stage_basic",
        client=client,
        target_rows=target_rows,
    )
    goods_ranges = plan_application_ranges(
        package_uuid,
        "markorbit_facts.cn_stage_goods",
        client=client,
        target_rows=target_rows,
    )
    applicant_ranges = plan_application_ranges(
        package_uuid,
        "markorbit_facts.cn_stage_applicant",
        client=client,
        target_rows=target_rows,
    )

    date_totals: dict[str, int] = {}
    date_examples: dict[str, list[Any]] = {}
    for application_range in basic_ranges:
        rows = _run_query(
            client,
            subtask="DATE_FLAGS",
            application_range=application_range,
            sql=f"""
                SELECT
                    flag,
                    count() AS occurrence_count,
                    groupArray(5)(tuple(application_number, source_file, source_start_line))
                        AS examples
                FROM
                (
                    SELECT application_number, source_file, source_start_line,
                           arrayJoin(date_quality_flags) AS flag
                    FROM markorbit_facts.cn_stage_basic
                    WHERE package_id = toUUID('{package}')
                      {_range_predicate(application_range)}
                )
                GROUP BY flag
            """,
        ).result_rows
        subtask_count += 1
        for flag, occurrence_count, examples in rows:
            key = str(flag)
            date_totals[key] = date_totals.get(key, 0) + int(occurrence_count or 0)
            bucket = date_examples.setdefault(key, [])
            _append_examples(bucket, examples, 5)

    for flag, occurrence_count in date_totals.items():
        issues.append(
            _issue(
                package_uuid,
                run_id,
                flag,
                occurrence_count,
                date_examples.get(flag, []),
            )
        )

    status_totals: dict[str, int] = {}
    status_examples: dict[str, list[Any]] = {}
    for application_range in goods_ranges:
        rows = _run_query(
            client,
            subtask="UNKNOWN_GOODS_STATUS",
            application_range=application_range,
            sql=f"""
                SELECT
                    if(goods_status_raw = '', '<BLANK>', goods_status_raw) AS raw_code,
                    count() AS occurrence_count,
                    groupArray(5)(tuple(application_number, class_no, source_file, source_start_line))
                        AS examples
                FROM markorbit_facts.cn_stage_goods
                WHERE package_id = toUUID('{package}')
                  AND goods_status_bucket = 'UNKNOWN'
                  {_range_predicate(application_range)}
                GROUP BY raw_code
            """,
        ).result_rows
        subtask_count += 1
        for raw_code, occurrence_count, examples in rows:
            key = str(raw_code)
            status_totals[key] = status_totals.get(key, 0) + int(occurrence_count or 0)
            bucket = status_examples.setdefault(key, [])
            _append_examples(bucket, examples, 5)

    for raw_code, occurrence_count in sorted(
        status_totals.items(), key=lambda item: item[1], reverse=True
    ):
        issues.append(
            _issue(
                package_uuid,
                run_id,
                "UNMAPPED_GOODS_STATUS_CODE",
                occurrence_count,
                status_examples.get(raw_code, []),
                raw_excerpt=raw_code,
                details={"raw_code": raw_code},
            )
        )

    integrity_specs = (
        (
            "GOODS_WITHOUT_BASIC",
            goods_ranges,
            "markorbit_facts.cn_stage_goods",
            "markorbit_facts.cn_stage_basic",
        ),
        (
            "BASIC_WITHOUT_GOODS",
            basic_ranges,
            "markorbit_facts.cn_stage_basic",
            "markorbit_facts.cn_stage_goods",
        ),
        (
            "APPLICANT_WITHOUT_BASIC",
            applicant_ranges,
            "markorbit_facts.cn_stage_applicant",
            "markorbit_facts.cn_stage_basic",
        ),
    )
    for issue_type, ranges, source_table, target_table in integrity_specs:
        total = 0
        examples: list[Any] = []
        for application_range in ranges:
            row = _run_query(
                client,
                subtask=issue_type,
                application_range=application_range,
                sql=_integrity_sql(
                    package,
                    source_table,
                    target_table,
                    application_range,
                    "src",
                    "dst",
                ),
            ).result_rows[0]
            subtask_count += 1
            total += int(row[0] or 0)
            _append_examples(examples, row[1], 10)
        if total:
            issues.append(
                _issue(package_uuid, run_id, issue_type, total, examples)
            )

    return QualitySubtaskResult(
        issues=issues,
        subtask_count=subtask_count,
        range_counts={
            "basic": len(basic_ranges),
            "goods": len(goods_ranges),
            "applicant": len(applicant_ranges),
        },
    )
