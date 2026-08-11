from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
import uuid

from app.cn.goods_lifecycle_sql import incoming_goods_sql
from app.db import clickhouse_client


GOODS_ITEM_IDENTITY_VERSION = "CN_GOODS_ITEM_ID_V2_STRICT_SOURCE_FIELDS"
GOODS_PUBLISH_TARGET_STAGE_ROWS = 1_000_000

_SCOPE_PUBLISH_STAGE_DDL = """
CREATE TABLE IF NOT EXISTS markorbit_facts.cn_stage_scope_publish
(
    package_id UUID,
    case_id UUID,
    application_number String,
    class_no UInt8,
    source_item_count UInt32,
    interpreted_active_item_count UInt32,
    interpreted_inactive_item_count UInt32,
    unmapped_status_item_count UInt32,
    effective_item_count Nullable(UInt32),
    interpretation_complete UInt8,
    scope_interpretation_status LowCardinality(String),
    goods_status_mapping_version String,
    observed_status_codes Array(String),
    goods_items_compact String,
    goods_text_search String,
    similar_groups Array(String),
    active_similar_groups Array(String),
    scope_hash FixedString(64),
    effective_scope_hash String,
    source_file String,
    source_first_line UInt64,
    source_last_line UInt64,
    source_row_hash FixedString(64),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (package_id, application_number, class_no)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY DELETE
"""

REQUIRED_M16_GOODS_COLUMNS = {
    ("cn_goods_item_current", "goods_item_key"),
    ("cn_goods_item_current", "operational_effect"),
    ("cn_goods_item_current", "first_source_package_id"),
    ("cn_goods_item_observation", "transition_type"),
    ("cn_goods_scope_lifecycle_current", "all_known_goods_inactive"),
    ("cn_goods_scope_lifecycle_current", "code_2_item_count"),
    ("cn_stage_scope_publish", "scope_hash"),
}


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


@dataclass(frozen=True)
class ApplicationRange:
    lower: str | None = None
    upper: str | None = None

    def predicate(self, column: str = "application_number") -> str:
        parts: list[str] = []
        if self.lower is not None:
            parts.append(f"{column} >= {_sql_string(self.lower)}")
        if self.upper is not None:
            parts.append(f"{column} < {_sql_string(self.upper)}")
        return " AND ".join(parts)

    def and_predicate(self, column: str = "application_number") -> str:
        predicate = self.predicate(column)
        return f" AND {predicate}" if predicate else ""


def ensure_m16_goods_schema() -> None:
    client = clickhouse_client()

    # This transient table is an additive runtime migration only. It changes no
    # permanent M1.6 fact semantics and lets an existing clean-replay volume pick
    # up the bounded publisher without another destructive reset.
    client.command(_SCOPE_PUBLISH_STAGE_DDL)

    rows = client.query(
        """
        SELECT table, name
        FROM system.columns
        WHERE database = 'markorbit_facts'
        """
    ).result_rows
    available = {(str(table), str(name)) for table, name in rows}
    missing = sorted(REQUIRED_M16_GOODS_COLUMNS - available)
    if missing:
        formatted = ", ".join(f"{table}.{column}" for table, column in missing)
        raise RuntimeError(
            "M1.6 CN goods lifecycle schema is not initialized. Missing: "
            f"{formatted}. Run scripts/apply-m16-goods-schema.ps1 or perform the "
            "clean M1.6 replay procedure before ingesting more CN packages."
        )


def ensure_m16_goods_replay_boundary() -> None:
    """Refuse to mix old class-only scopes with a newly empty item store.

    Existing M1.5 installations already contain class aggregates but discarded
    stage rows after successful ingest. M1.6 cannot safely invent item history
    from those aggregates. A clean replay/backfill is required once before the
    first M1.6 package is accepted.
    """
    client = clickhouse_client()
    scope_count = int(
        client.query(
            "SELECT count() FROM markorbit_facts.cn_case_scope_current FINAL WHERE is_deleted = 0"
        ).result_rows[0][0]
        or 0
    )
    item_count = int(
        client.query(
            "SELECT count() FROM markorbit_facts.cn_goods_item_current FINAL WHERE is_deleted = 0"
        ).result_rows[0][0]
        or 0
    )
    if scope_count > 0 and item_count == 0:
        raise RuntimeError(
            "M1.6 goods lifecycle requires one clean replay before new ingestion: "
            "existing cn_case_scope_current rows were built under M1.5 but the durable "
            "goods item store is empty. Do not mix the two models."
        )


def _nullable_date(value: date | None) -> str:
    if value is None:
        return "CAST(NULL, 'Nullable(Date32)')"
    return f"toDate32('{value.isoformat()}')"


def _plan_goods_application_ranges(
    package_uuid: uuid.UUID | str,
    *,
    client: Any | None = None,
    target_rows: int = GOODS_PUBLISH_TARGET_STAGE_ROWS,
) -> list[ApplicationRange]:
    """Split a staged package into contiguous, whole-application ranges.

    ``cn_stage_goods`` is ordered by ``(package_id, application_number, ...)``.
    Boundary lookup therefore walks the physical order instead of hash-bucketing
    and rescanning the full stage table. The boundary application is excluded
    from the preceding chunk and included in the next one, so all rows for one
    application always stay together. If one application alone is larger than
    the target, that application becomes one intentionally oversized chunk.
    """
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
            FROM markorbit_facts.cn_stage_goods
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
                FROM markorbit_facts.cn_stage_goods
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


def touched_scope_sql(
    package_uuid: uuid.UUID | str,
    application_lower: str | None = None,
    application_upper: str | None = None,
) -> str:
    package = str(package_uuid)
    application_range = ApplicationRange(application_lower, application_upper)
    return f"""
        SELECT DISTINCT application_number, class_no
        FROM markorbit_facts.cn_stage_goods
        WHERE package_id = toUUID('{package}')
          {application_range.and_predicate('application_number')}
    """.replace("\n           AND", "\n          AND")


def scope_from_current_items_sql(
    package_uuid: uuid.UUID | str,
    application_lower: str | None = None,
    application_upper: str | None = None,
) -> str:
    """Reconstruct touched class scopes from the complete durable item set.

    This is the central M1.6 invariant: the monthly package identifies which
    classes are touched, but the scope is rebuilt from *all current items* for
    those classes. Omission from a monthly patch is therefore never deletion.
    Intra-package duplicate status variants have already been resolved by the
    single production ``incoming_goods_sql`` builder before durable state is
    written, so this scope layer never invents a precedence of its own.
    """
    touched = touched_scope_sql(package_uuid, application_lower, application_upper)
    application_range = ApplicationRange(application_lower, application_upper)
    item_range = application_range.and_predicate("item.application_number")
    return f"""
        SELECT
            aggregated.*,
            if(
                unmapped_status_item_count = 0,
                toNullable(toUInt32(interpreted_active_item_count)),
                CAST(NULL, 'Nullable(UInt32)')
            ) AS effective_item_count,
            if(unmapped_status_item_count = 0, 1, 0) AS interpretation_complete,
            multiIf(
                unmapped_status_item_count > 0, 'UNKNOWN_CODES_PRESENT',
                interpreted_inactive_item_count = source_item_count,
                    'ALL_KNOWN_GOODS_INACTIVE',
                interpreted_inactive_item_count > 0, 'PARTIAL_GOODS_INACTIVE',
                risk_item_count > 0, 'GOODS_RISK_SIGNAL_PRESENT',
                'COMPLETE'
            ) AS scope_interpretation_status,
            hex(SHA256(concat(
                application_number, '|', toString(class_no), '|', goods_items_compact, '|',
                arrayStringConcat(similar_groups, ','), '|',
                arrayStringConcat(observed_status_codes, ',')
            ))) AS scope_hash,
            if(
                unmapped_status_item_count = 0,
                hex(SHA256(concat(
                    application_number, '|', toString(class_no), '|',
                    toString(interpreted_active_item_count), '|',
                    arrayStringConcat(active_similar_groups, ',')
                ))),
                ''
            ) AS effective_scope_hash
        FROM
        (
            SELECT
                any(item.case_id) AS case_id,
                item.application_number AS application_number,
                item.class_no AS class_no,
                toUInt32(count()) AS source_item_count,
                toUInt32(countIf(item.operational_effect IN (
                    'EFFECTIVE_UNLESS_CONTRADICTED', 'EFFECTIVE_AT_RISK'
                ))) AS interpreted_active_item_count,
                toUInt32(countIf(item.operational_effect IN (
                    'INACTIVE_HIGH_CONFIDENCE', 'INACTIVE_CONFIRMED'
                ))) AS interpreted_inactive_item_count,
                toUInt32(countIf(item.operational_effect = 'UNKNOWN'))
                    AS unmapped_status_item_count,
                toUInt32(countIf(item.operational_effect = 'EFFECTIVE_AT_RISK'))
                    AS risk_item_count,
                argMax(
                    item.goods_status_mapping_version,
                    tuple(item.source_rank, item.source_first_line)
                ) AS goods_status_mapping_version,
                arraySort(groupUniqArray(if(
                    item.goods_status_raw = '', '<BLANK>', item.goods_status_raw
                ))) AS observed_status_codes,
                toJSONString(arraySort(groupArray((
                    item.goods_sequence,
                    item.similar_group,
                    item.goods_name,
                    item.goods_status_raw,
                    item.goods_status_bucket,
                    item.goods_status_reason
                )))) AS goods_items_compact,
                arrayStringConcat(arraySort(groupArray(item.goods_name)), ' ')
                    AS goods_text_search,
                arraySort(arrayFilter(
                    x -> x != '', groupUniqArray(item.similar_group)
                )) AS similar_groups,
                arraySort(arrayFilter(
                    x -> x != '',
                    groupUniqArrayIf(
                        item.similar_group,
                        item.operational_effect IN (
                            'EFFECTIVE_UNLESS_CONTRADICTED', 'EFFECTIVE_AT_RISK'
                        )
                    )
                )) AS active_similar_groups,
                argMax(
                    item.source_file,
                    tuple(item.source_rank, item.source_first_line)
                ) AS source_file,
                argMax(
                    item.source_first_line,
                    tuple(item.source_rank, item.source_first_line)
                ) AS source_first_line,
                argMax(
                    item.source_last_line,
                    tuple(item.source_rank, item.source_first_line)
                ) AS source_last_line,
                hex(SHA256(arrayStringConcat(
                    arraySort(groupArray(toString(item.record_hash))), '|'
                ))) AS source_row_hash
            FROM markorbit_facts.cn_goods_item_current AS item FINAL
            INNER JOIN ({touched}) AS touched
              ON touched.application_number = item.application_number
             AND touched.class_no = item.class_no
            WHERE item.is_deleted = 0{item_range}
            GROUP BY item.application_number, item.class_no
        ) AS aggregated
    """


def _lifecycle_scope_sql(
    package_uuid: uuid.UUID | str,
    application_lower: str | None = None,
    application_upper: str | None = None,
) -> str:
    touched = touched_scope_sql(package_uuid, application_lower, application_upper)
    application_range = ApplicationRange(application_lower, application_upper)
    item_range = application_range.and_predicate("item.application_number")
    return f"""
        SELECT
            any(item.case_id) AS case_id,
            item.application_number,
            item.class_no,
            toUInt32(count()) AS known_item_count,
            toUInt32(countIf(item.operational_effect IN (
                'EFFECTIVE_UNLESS_CONTRADICTED', 'EFFECTIVE_AT_RISK'
            ))) AS operational_effective_item_count,
            toUInt32(countIf(item.operational_effect = 'EFFECTIVE_AT_RISK'))
                AS risk_item_count,
            toUInt32(countIf(item.operational_effect = 'INACTIVE_HIGH_CONFIDENCE'))
                AS inactive_high_confidence_item_count,
            toUInt32(countIf(item.operational_effect = 'INACTIVE_CONFIRMED'))
                AS final_inactive_item_count,
            toUInt32(countIf(item.operational_effect = 'UNKNOWN')) AS unknown_item_count,
            toUInt32(countIf(item.goods_status_raw = '0')) AS code_0_item_count,
            toUInt32(countIf(item.goods_status_raw = '1')) AS code_1_item_count,
            toUInt32(countIf(item.goods_status_raw = '2')) AS code_2_item_count,
            toUInt8(countIf(item.operational_effect IN (
                'INACTIVE_HIGH_CONFIDENCE', 'INACTIVE_CONFIRMED'
            )) > 0) AS some_goods_inactive,
            toUInt8(
                count() > 0
                AND countIf(item.operational_effect IN (
                    'INACTIVE_HIGH_CONFIDENCE', 'INACTIVE_CONFIRMED'
                )) = count()
            ) AS all_known_goods_inactive,
            toUInt8(countIf(item.operational_effect = 'INACTIVE_CONFIRMED') > 0)
                AS some_goods_final_inactive,
            toUInt8(
                count() > 0
                AND countIf(item.operational_effect = 'INACTIVE_CONFIRMED') = count()
            ) AS all_known_goods_final_inactive,
            toUInt8(countIf(item.operational_effect = 'EFFECTIVE_AT_RISK') > 0)
                AS goods_risk_signal_present,
            argMax(
                item.goods_status_mapping_version,
                tuple(item.source_rank, item.source_first_line)
            ) AS goods_status_mapping_version
        FROM markorbit_facts.cn_goods_item_current AS item FINAL
        INNER JOIN ({touched}) AS touched
          ON touched.application_number = item.application_number
         AND touched.class_no = item.class_no
        WHERE item.is_deleted = 0{item_range}
        GROUP BY item.application_number, item.class_no
    """


def scope_publish_stage_sql(package_uuid: uuid.UUID | str) -> str:
    package = str(package_uuid)
    return f"""
        SELECT
            case_id,
            application_number,
            class_no,
            source_item_count,
            interpreted_active_item_count,
            interpreted_inactive_item_count,
            unmapped_status_item_count,
            effective_item_count,
            interpretation_complete,
            scope_interpretation_status,
            goods_status_mapping_version,
            observed_status_codes,
            goods_items_compact,
            goods_text_search,
            similar_groups,
            active_similar_groups,
            scope_hash,
            effective_scope_hash,
            source_file,
            source_first_line,
            source_last_line,
            source_row_hash
        FROM markorbit_facts.cn_stage_scope_publish
        WHERE package_id = toUUID('{package}')
    """


def _current_items_for_range_sql(application_range: ApplicationRange) -> str:
    return f"""
        SELECT *
        FROM markorbit_facts.cn_goods_item_current FINAL
        WHERE is_deleted = 0{application_range.and_predicate('application_number')}
    """


def _insert_scope_publish_stage(
    client: Any,
    package_uuid: uuid.UUID,
    scope_sql: str,
) -> None:
    package = str(package_uuid)
    client.command(f"""
        INSERT INTO markorbit_facts.cn_stage_scope_publish
        (
            package_id, case_id, application_number, class_no,
            source_item_count, interpreted_active_item_count,
            interpreted_inactive_item_count, unmapped_status_item_count,
            effective_item_count, interpretation_complete,
            scope_interpretation_status, goods_status_mapping_version,
            observed_status_codes, goods_items_compact, goods_text_search,
            similar_groups, active_similar_groups, scope_hash,
            effective_scope_hash, source_file, source_first_line,
            source_last_line, source_row_hash, ingested_at
        )
        SELECT
            toUUID('{package}'), scope.case_id, scope.application_number,
            scope.class_no, scope.source_item_count,
            scope.interpreted_active_item_count,
            scope.interpreted_inactive_item_count,
            scope.unmapped_status_item_count, scope.effective_item_count,
            scope.interpretation_complete, scope.scope_interpretation_status,
            scope.goods_status_mapping_version, scope.observed_status_codes,
            scope.goods_items_compact, scope.goods_text_search,
            scope.similar_groups, scope.active_similar_groups,
            scope.scope_hash, scope.effective_scope_hash, scope.source_file,
            scope.source_first_line, scope.source_last_line,
            scope.source_row_hash, now64(3)
        FROM ({scope_sql}) AS scope
    """)


def publish_goods_lifecycle(
    package_uuid: uuid.UUID,
    package_meta: dict[str, Any],
) -> dict[str, int]:
    """Publish M1.6 goods with bounded memory and unchanged source semantics."""
    ensure_m16_goods_schema()
    client = clickhouse_client()
    package = str(package_uuid)
    package_kind = str(package_meta["package_kind"])
    source_rank = int(package_meta["source_rank"])
    effective_expr = _nullable_date(package_meta.get("source_period_end"))
    application_ranges = _plan_goods_application_ranges(package_uuid, client=client)

    metrics = {
        "goods_lifecycle_touched_items": 0,
        "goods_lifecycle_touched_scopes": 0,
        "goods_lifecycle_code_0_items_in_touched_scopes": 0,
        "goods_lifecycle_code_1_items_in_touched_scopes": 0,
        "goods_lifecycle_code_2_items_in_touched_scopes": 0,
        "goods_publish_chunk_count": len(application_ranges),
    }

    for application_range in application_ranges:
        incoming = incoming_goods_sql(
            package_uuid,
            application_range.lower,
            application_range.upper,
        )
        current_items = _current_items_for_range_sql(application_range)

        client.command(f"""
            INSERT INTO markorbit_facts.cn_goods_item_observation
            SELECT
                generateUUIDv4(),
                incoming.case_id,
                incoming.application_number,
                incoming.class_no,
                incoming.goods_item_key,
                incoming.goods_sequence,
                incoming.goods_name,
                incoming.similar_group,
                if(cur.application_number = '', '', cur.goods_status_raw),
                if(cur.application_number = '', '', cur.goods_status_semantic),
                if(cur.application_number = '', '', cur.operational_effect),
                incoming.goods_status_raw,
                incoming.goods_status_semantic,
                incoming.goods_status_source_finality,
                incoming.operational_effect,
                multiIf(
                    cur.application_number = '', 'FIRST_OBSERVED',
                    cur.goods_status_raw != incoming.goods_status_raw, 'STATUS_CHANGED',
                    cur.record_hash != incoming.record_hash, 'ITEM_DETAILS_CHANGED',
                    'REOBSERVED'
                ),
                incoming.evidence_label,
                toUUID('{package}'),
                '{package_kind}',
                {effective_expr},
                incoming.source_file,
                incoming.source_first_line,
                incoming.source_last_line,
                incoming.source_row_hash,
                {source_rank},
                hex(SHA256(concat(
                    incoming.application_number, '|', toString(incoming.class_no), '|',
                    incoming.goods_item_key, '|',
                    if(cur.application_number = '', '', cur.goods_status_raw), '|',
                    incoming.goods_status_raw, '|', incoming.record_hash, '|',
                    toString({source_rank})
                ))),
                now64(3)
            FROM ({incoming}) AS incoming
            LEFT JOIN ({current_items}) AS cur
              ON cur.application_number = incoming.application_number
             AND cur.class_no = incoming.class_no
             AND cur.goods_item_key = incoming.goods_item_key
            WHERE cur.application_number = '' OR cur.source_rank <= {source_rank}
        """)

        client.command(f"""
            INSERT INTO markorbit_facts.cn_goods_item_current
            SELECT
                incoming.case_id,
                incoming.application_number,
                incoming.class_no,
                incoming.goods_item_key,
                incoming.goods_sequence,
                incoming.goods_name,
                incoming.goods_name_norm,
                incoming.similar_group,
                incoming.goods_status_raw,
                incoming.goods_status_bucket,
                incoming.goods_status_reason,
                incoming.goods_status_semantic,
                incoming.goods_status_source_finality,
                incoming.operational_effect,
                incoming.goods_status_mapping_version,
                incoming.evidence_label,
                if(cur.application_number = '', toUUID('{package}'), cur.first_source_package_id),
                if(cur.application_number = '', '{package_kind}', cur.first_source_package_kind),
                if(cur.application_number = '', {source_rank}, cur.first_source_rank),
                '{package_kind}',
                {effective_expr},
                incoming.source_file,
                incoming.source_first_line,
                incoming.source_last_line,
                incoming.source_row_hash,
                toUUID('{package}'),
                incoming.record_hash,
                {source_rank},
                now64(3),
                toUInt8(0)
            FROM ({incoming}) AS incoming
            LEFT JOIN ({current_items}) AS cur
              ON cur.application_number = incoming.application_number
             AND cur.class_no = incoming.class_no
             AND cur.goods_item_key = incoming.goods_item_key
            WHERE cur.application_number = '' OR cur.source_rank <= {source_rank}
        """)

        scope_sql = scope_from_current_items_sql(
            package_uuid,
            application_range.lower,
            application_range.upper,
        )
        _insert_scope_publish_stage(client, package_uuid, scope_sql)

        lifecycle_scope = _lifecycle_scope_sql(
            package_uuid,
            application_range.lower,
            application_range.upper,
        )
        client.command(f"""
            INSERT INTO markorbit_facts.cn_goods_scope_lifecycle_current
            SELECT
                scope.case_id,
                scope.application_number,
                scope.class_no,
                scope.known_item_count,
                scope.operational_effective_item_count,
                scope.risk_item_count,
                scope.inactive_high_confidence_item_count,
                scope.final_inactive_item_count,
                scope.unknown_item_count,
                scope.code_0_item_count,
                scope.code_1_item_count,
                scope.code_2_item_count,
                scope.some_goods_inactive,
                scope.all_known_goods_inactive,
                scope.some_goods_final_inactive,
                scope.all_known_goods_final_inactive,
                scope.goods_risk_signal_present,
                scope.goods_status_mapping_version,
                'DERIVED_FROM_DURABLE_GOODS_ITEM_STATE',
                '{package_kind}',
                {effective_expr},
                toUUID('{package}'),
                {source_rank},
                now64(3),
                toUInt8(0)
            FROM ({lifecycle_scope}) AS scope
        """)

        item_count = int(
            client.query(f"""
                SELECT count()
                FROM markorbit_facts.cn_goods_item_current FINAL
                WHERE is_deleted = 0
                  AND last_source_package_id = toUUID('{package}')
                  {application_range.and_predicate('application_number')}
            """).result_rows[0][0]
            or 0
        )
        lifecycle_row = client.query(f"""
            SELECT
                count(),
                sum(code_0_item_count),
                sum(code_1_item_count),
                sum(code_2_item_count)
            FROM markorbit_facts.cn_goods_scope_lifecycle_current FINAL
            WHERE is_deleted = 0
              AND last_source_package_id = toUUID('{package}')
              {application_range.and_predicate('application_number')}
        """).result_rows[0]

        metrics["goods_lifecycle_touched_items"] += item_count
        metrics["goods_lifecycle_touched_scopes"] += int(lifecycle_row[0] or 0)
        metrics["goods_lifecycle_code_0_items_in_touched_scopes"] += int(
            lifecycle_row[1] or 0
        )
        metrics["goods_lifecycle_code_1_items_in_touched_scopes"] += int(
            lifecycle_row[2] or 0
        )
        metrics["goods_lifecycle_code_2_items_in_touched_scopes"] += int(
            lifecycle_row[3] or 0
        )

    return metrics


def cleanup_scope_publish_stage(package_uuid: uuid.UUID) -> None:
    client = clickhouse_client()
    package = str(package_uuid)
    client.command(
        "ALTER TABLE markorbit_facts.cn_stage_scope_publish "
        f"DELETE WHERE package_id = toUUID('{package}') SETTINGS mutations_sync = 1"
    )


def cleanup_goods_outputs(package_uuid: uuid.UUID) -> None:
    client = clickhouse_client()
    package = str(package_uuid)
    filters = {
        "markorbit_facts.cn_goods_item_observation": "source_package_id",
        "markorbit_facts.cn_goods_item_current": "last_source_package_id",
        "markorbit_facts.cn_goods_scope_lifecycle_current": "last_source_package_id",
    }
    for table, column in filters.items():
        client.command(
            f"ALTER TABLE {table} DELETE WHERE {column} = toUUID('{package}') "
            "SETTINGS mutations_sync = 1"
        )
    cleanup_scope_publish_stage(package_uuid)
