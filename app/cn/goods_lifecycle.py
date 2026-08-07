from __future__ import annotations

from datetime import date
from typing import Any
import uuid

from app.db import clickhouse_client


REQUIRED_M16_GOODS_COLUMNS = {
    ("cn_goods_item_current", "goods_item_key"),
    ("cn_goods_item_current", "operational_effect"),
    ("cn_goods_item_current", "first_source_package_id"),
    ("cn_goods_item_observation", "transition_type"),
    ("cn_goods_scope_lifecycle_current", "all_known_goods_inactive"),
    ("cn_goods_scope_lifecycle_current", "code_2_item_count"),
}


def ensure_m16_goods_schema() -> None:
    client = clickhouse_client()
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


def incoming_goods_sql(package_uuid: uuid.UUID | str) -> str:
    package = str(package_uuid)
    return f"""
        SELECT
            aggregated.*,
            multiIf(
                goods_status_raw = '0', 'REVERSIBLE_OR_UNRESOLVED_RISK',
                goods_status_raw = '1', 'INACTIVE_HIGH_CONFIDENCE',
                goods_status_raw = '2', 'FINAL_INACTIVE',
                goods_status_reason = 'EXPLICIT_INACTIVE_TEXT', 'FINAL_INACTIVE_TEXT',
                goods_status_bucket = 'ACTIVE', 'NO_NEGATIVE_SIGNAL',
                'UNKNOWN'
            ) AS goods_status_semantic,
            multiIf(
                goods_status_raw = '0', 'REVERSIBLE',
                goods_status_raw = '1', 'SOURCE_NOT_FINALIZED',
                goods_status_raw = '2', 'FINAL',
                goods_status_reason = 'EXPLICIT_INACTIVE_TEXT', 'EXPLICIT_TEXT',
                goods_status_bucket = 'ACTIVE', 'OPEN',
                'UNKNOWN'
            ) AS goods_status_source_finality,
            multiIf(
                goods_status_raw = '0', 'EFFECTIVE_AT_RISK',
                goods_status_raw = '1', 'INACTIVE_HIGH_CONFIDENCE',
                goods_status_raw = '2', 'INACTIVE_CONFIRMED',
                goods_status_reason = 'EXPLICIT_INACTIVE_TEXT', 'INACTIVE_CONFIRMED',
                goods_status_bucket = 'ACTIVE', 'EFFECTIVE_UNLESS_CONTRADICTED',
                'UNKNOWN'
            ) AS operational_effect,
            multiIf(
                goods_status_raw IN ('0', '1', '2'), 'EMPIRICAL_DOMAIN_MAPPING',
                goods_status_reason IN ('EXPLICIT_INACTIVE_TEXT', 'EXPLICIT_ACTIVE_TEXT'),
                    'SOURCE_TEXT_MAPPING',
                'PIPELINE_MAPPING'
            ) AS evidence_label,
            hex(SHA256(concat(
                application_number, '|', toString(class_no), '|', goods_item_key, '|',
                goods_sequence, '|', similar_group, '|', goods_name, '|',
                goods_status_raw, '|', goods_status_bucket, '|', goods_status_reason, '|',
                goods_status_mapping_version
            ))) AS record_hash
        FROM
        (
            SELECT
                case_id,
                application_number,
                class_no,
                goods_item_key,
                argMax(goods_sequence, toUInt64(stage_source_start_line)) AS goods_sequence,
                argMax(goods_name, toUInt64(stage_source_start_line)) AS goods_name,
                lowerUTF8(argMax(goods_name, toUInt64(stage_source_start_line))) AS goods_name_norm,
                argMax(similar_group, toUInt64(stage_source_start_line)) AS similar_group,
                argMax(goods_status_raw, toUInt64(stage_source_start_line)) AS goods_status_raw,
                argMax(goods_status_bucket, toUInt64(stage_source_start_line)) AS goods_status_bucket,
                argMax(goods_status_reason, toUInt64(stage_source_start_line)) AS goods_status_reason,
                argMax(goods_status_mapping_version, toUInt64(stage_source_start_line))
                    AS goods_status_mapping_version,
                argMin(source_file, toUInt64(stage_source_start_line)) AS source_file,
                min(toUInt64(stage_source_start_line)) AS source_first_line,
                max(toUInt64(stage_source_end_line)) AS source_last_line,
                hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|')))
                    AS source_row_hash
            FROM
            (
                SELECT
                    package_id,
                    case_id,
                    application_number,
                    class_no,
                    similar_group,
                    goods_sequence,
                    goods_name,
                    goods_status_raw,
                    goods_status_bucket,
                    goods_status_reason,
                    goods_status_mapping_version,
                    if(
                        goods_sequence != '',
                        hex(SHA256(concat(
                            application_number, '|', toString(class_no), '|SEQ|', goods_sequence
                        ))),
                        hex(SHA256(concat(
                            application_number, '|', toString(class_no), '|NAME|',
                            lowerUTF8(goods_name), '|GROUP|', similar_group
                        )))
                    ) AS goods_item_key,
                    source_file,
                    source_start_line AS stage_source_start_line,
                    source_end_line AS stage_source_end_line,
                    row_hash
                FROM markorbit_facts.cn_stage_goods
                WHERE package_id = toUUID('{package}')
            ) AS prepared
            GROUP BY case_id, application_number, class_no, goods_item_key
        ) AS aggregated
    """


def touched_scope_sql(package_uuid: uuid.UUID | str) -> str:
    package = str(package_uuid)
    return f"""
        SELECT DISTINCT application_number, class_no
        FROM markorbit_facts.cn_stage_goods
        WHERE package_id = toUUID('{package}')
    """


def scope_from_current_items_sql(package_uuid: uuid.UUID | str) -> str:
    """Reconstruct touched class scopes from the complete durable item set.

    This is the central M1.6 invariant: the monthly package identifies which
    classes are touched, but the scope is rebuilt from *all current items* for
    those classes. Omission from a monthly patch is therefore never deletion.
    """
    touched = touched_scope_sql(package_uuid)
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
            WHERE item.is_deleted = 0
            GROUP BY item.application_number, item.class_no
        ) AS aggregated
    """


def _lifecycle_scope_sql(package_uuid: uuid.UUID | str) -> str:
    touched = touched_scope_sql(package_uuid)
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
        WHERE item.is_deleted = 0
        GROUP BY item.application_number, item.class_no
    """


def publish_goods_lifecycle(
    package_uuid: uuid.UUID,
    package_meta: dict[str, Any],
) -> dict[str, int]:
    ensure_m16_goods_schema()
    client = clickhouse_client()
    package = str(package_uuid)
    package_kind = str(package_meta["package_kind"])
    source_rank = int(package_meta["source_rank"])
    effective_expr = _nullable_date(package_meta.get("source_period_end"))
    incoming = incoming_goods_sql(package_uuid)

    # Record what the source package said about each touched item before replacing
    # its current representation. The observation preserves raw source code and
    # never assigns a trademark-level status or legal cause.
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
        LEFT JOIN markorbit_facts.cn_goods_item_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.class_no = incoming.class_no
         AND cur.goods_item_key = incoming.goods_item_key
        WHERE cur.application_number = '' OR cur.source_rank <= {source_rank}
    """)

    # Delta merge: only touched items are replaced. Missing monthly rows never
    # close/delete items that were established by a base package.
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
            if(cur.application_number = '', toUInt64({source_rank}), cur.first_source_rank),
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
            0
        FROM ({incoming}) AS incoming
        LEFT JOIN markorbit_facts.cn_goods_item_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.class_no = incoming.class_no
         AND cur.goods_item_key = incoming.goods_item_key
        WHERE cur.application_number = '' OR cur.source_rank <= {source_rank}
    """)

    lifecycle_scope = _lifecycle_scope_sql(package_uuid)
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
            'EMPIRICAL_DOMAIN_MAPPING',
            '{package_kind}',
            {effective_expr},
            toUUID('{package}'),
            {source_rank},
            now64(3),
            0
        FROM ({lifecycle_scope}) AS scope
    """)

    row = client.query(f"""
        SELECT
            (SELECT count() FROM ({incoming})) AS touched_items,
            (SELECT count() FROM ({touched_scope_sql(package_uuid)})) AS touched_scopes,
            (SELECT sum(code_0_item_count) FROM ({lifecycle_scope})) AS code_0_items,
            (SELECT sum(code_1_item_count) FROM ({lifecycle_scope})) AS code_1_items,
            (SELECT sum(code_2_item_count) FROM ({lifecycle_scope})) AS code_2_items
    """).result_rows[0]
    return {
        "goods_lifecycle_touched_items": int(row[0] or 0),
        "goods_lifecycle_touched_scopes": int(row[1] or 0),
        "goods_lifecycle_code_0_items_in_touched_scopes": int(row[2] or 0),
        "goods_lifecycle_code_1_items_in_touched_scopes": int(row[3] or 0),
        "goods_lifecycle_code_2_items_in_touched_scopes": int(row[4] or 0),
    }


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
