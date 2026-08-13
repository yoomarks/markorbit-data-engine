from __future__ import annotations

from collections.abc import Callable
from typing import Any
import uuid

from app.cn.goods_lifecycle import ApplicationRange
from app.db import clickhouse_client


CASE_PUBLISH_TARGET_BASIC_ROWS = 250_000

_CASE_PUBLISH_STAGE_DDL = """
CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_case_publish
(
    package_id UUID,
    case_id UUID,
    family_root_case_id UUID,
    application_number String,
    case_family_root String,
    suffix_path String,
    filing_route LowCardinality(String),
    number_family LowCardinality(String),
    international_registration_number String,
    is_derived_case UInt8,
    relation_id UUID,
    mark_name_raw String,
    mark_type_raw String,
    mark_form_raw String,
    agent_code String,
    filing_date Nullable(Date32),
    prelim_pub_date Nullable(Date32),
    prelim_pub_issue String,
    registration_pub_date Nullable(Date32),
    registration_pub_issue String,
    exclusive_start_date Nullable(Date32),
    exclusive_end_date Nullable(Date32),
    exclusive_period String,
    design_description String,
    color_description String,
    exclusive_rights_disclaimer String,
    is_3d_mark UInt8,
    is_co_application UInt8,
    geo_indication_info String,
    color_mark_flag String,
    is_well_known_mark UInt8,
    classes Array(UInt8),
    data_quality_flags Array(String),
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE
"""


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def ensure_case_publish_schema(*, client: Any | None = None) -> None:
    (client or clickhouse_client()).command(_CASE_PUBLISH_STAGE_DDL)


def cleanup_case_publish_stage(
    package_uuid: uuid.UUID | str,
    *,
    client: Any | None = None,
) -> None:
    client = client or clickhouse_client()
    package = str(package_uuid)
    client.command(
        "ALTER TABLE markorbit_facts.cn_stage_case_publish "
        f"DELETE WHERE package_id = toUUID('{package}') SETTINGS mutations_sync = 1"
    )


def _plan_case_application_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any | None = None,
    target_rows: int = CASE_PUBLISH_TARGET_BASIC_ROWS,
) -> list[ApplicationRange]:
    if target_rows < 1:
        raise ValueError("target_rows must be positive")

    client = client or clickhouse_client()
    package = str(package_uuid)
    ranges: list[ApplicationRange] = []
    lower: str | None = None

    while True:
        lower_sql = ""
        if lower is not None:
            lower_sql = f" AND application_number >= {_sql_string(lower)}"

        rows = client.query(
            f"""
            SELECT application_number
            FROM markorbit_facts.cn_stage_basic
            WHERE package_id = toUUID('{package}'){lower_sql}
            ORDER BY application_number
            LIMIT 1 OFFSET {int(target_rows)}
            """
        ).result_rows

        if not rows:
            ranges.append(ApplicationRange(lower=lower, upper=None))
            break

        boundary = str(rows[0][0])
        if lower is not None and boundary <= lower:
            next_rows = client.query(
                f"""
                SELECT application_number
                FROM markorbit_facts.cn_stage_basic
                WHERE package_id = toUUID('{package}')
                  AND application_number > {_sql_string(lower)}
                ORDER BY application_number
                LIMIT 1
                """
            ).result_rows
            if not next_rows:
                ranges.append(ApplicationRange(lower=lower, upper=None))
                break
            boundary = str(next_rows[0][0])

        ranges.append(ApplicationRange(lower=lower, upper=boundary))
        lower = boundary

    return ranges


def bounded_case_aggregate_sql(
    package_uuid: uuid.UUID | str,
    application_range: ApplicationRange,
    aggregate_builder: Callable[[str], str],
) -> str:
    """Keep legacy CASE semantics while limiting one aggregate to a row range."""
    package = str(package_uuid)
    sql = aggregate_builder(package)
    source_range = application_range.and_predicate("application_number")

    old_source = "FROM markorbit_facts.cn_stage_basic\n            ) AS stage_basic"
    new_source = (
        "FROM markorbit_facts.cn_stage_basic\n"
        f"                WHERE package_id = toUUID('{package}'){source_range}\n"
        "            ) AS stage_basic"
    )
    if sql.count(old_source) != 1:
        raise RuntimeError(
            "Legacy case aggregate SQL shape changed; expected one cn_stage_basic source."
        )
    sql = sql.replace(old_source, new_source, 1)

    outer_guard = f"WHERE package_id = toUUID('{package}')"
    if sql.count(outer_guard) != 1:
        raise RuntimeError(
            "Legacy case aggregate SQL shape changed; expected one package guard."
        )
    sql = sql.replace(
        outer_guard,
        f"{outer_guard}{application_range.and_predicate('application_number')}",
        1,
    )
    return sql


def case_publish_stage_sql(package_uuid: uuid.UUID | str) -> str:
    package = str(package_uuid)
    return f"""
        SELECT
            case_id, family_root_case_id, application_number, case_family_root,
            suffix_path, filing_route, number_family,
            international_registration_number, is_derived_case, relation_id,
            mark_name_raw, mark_type_raw, mark_form_raw, agent_code, filing_date,
            prelim_pub_date, prelim_pub_issue, registration_pub_date,
            registration_pub_issue, exclusive_start_date, exclusive_end_date,
            exclusive_period, design_description, color_description,
            exclusive_rights_disclaimer, is_3d_mark, is_co_application,
            geo_indication_info, color_mark_flag, is_well_known_mark, classes,
            data_quality_flags, source_file, source_first_line, source_last_line,
            source_row_hash, record_hash
        FROM markorbit_facts.cn_stage_case_publish
        WHERE package_id = toUUID('{package}')
    """


def materialize_case_publish_stage(
    package_uuid: uuid.UUID | str,
    aggregate_builder: Callable[[str], str],
    *,
    client: Any | None = None,
    target_rows: int = CASE_PUBLISH_TARGET_BASIC_ROWS,
) -> dict[str, int]:
    """Aggregate CASE facts once, in bounded whole-application chunks."""
    client = client or clickhouse_client()
    package = str(package_uuid)
    ensure_case_publish_schema(client=client)
    cleanup_case_publish_stage(package_uuid, client=client)

    application_ranges = _plan_case_application_ranges(
        package_uuid,
        client=client,
        target_rows=target_rows,
    )

    for application_range in application_ranges:
        case_sql = bounded_case_aggregate_sql(
            package_uuid,
            application_range,
            aggregate_builder,
        )
        client.command(f"""
            INSERT INTO markorbit_facts.cn_stage_case_publish
            (
                package_id, case_id, family_root_case_id, application_number,
                case_family_root, suffix_path, filing_route, number_family,
                international_registration_number, is_derived_case, relation_id,
                mark_name_raw, mark_type_raw, mark_form_raw, agent_code, filing_date,
                prelim_pub_date, prelim_pub_issue, registration_pub_date,
                registration_pub_issue, exclusive_start_date, exclusive_end_date,
                exclusive_period, design_description, color_description,
                exclusive_rights_disclaimer, is_3d_mark, is_co_application,
                geo_indication_info, color_mark_flag, is_well_known_mark, classes,
                data_quality_flags, source_file, source_first_line, source_last_line,
                source_row_hash, record_hash
            )
            SELECT
                toUUID('{package}'), incoming.case_id, incoming.family_root_case_id,
                incoming.application_number, incoming.case_family_root,
                incoming.suffix_path, incoming.filing_route, incoming.number_family,
                incoming.international_registration_number, incoming.is_derived_case,
                incoming.relation_id, incoming.mark_name_raw, incoming.mark_type_raw,
                incoming.mark_form_raw, incoming.agent_code, incoming.filing_date,
                incoming.prelim_pub_date, incoming.prelim_pub_issue,
                incoming.registration_pub_date, incoming.registration_pub_issue,
                incoming.exclusive_start_date, incoming.exclusive_end_date,
                incoming.exclusive_period, incoming.design_description,
                incoming.color_description, incoming.exclusive_rights_disclaimer,
                incoming.is_3d_mark, incoming.is_co_application,
                incoming.geo_indication_info, incoming.color_mark_flag,
                incoming.is_well_known_mark, incoming.classes,
                incoming.data_quality_flags, incoming.source_file,
                incoming.source_first_line, incoming.source_last_line,
                incoming.source_row_hash, incoming.record_hash
            FROM ({case_sql}) AS incoming
        """)

    row_count = int(
        client.query(
            "SELECT count() FROM markorbit_facts.cn_stage_case_publish "
            f"WHERE package_id = toUUID('{package}')"
        ).result_rows[0][0]
        or 0
    )
    return {
        "case_publish_rows": row_count,
        "case_publish_chunk_count": len(application_ranges),
    }
