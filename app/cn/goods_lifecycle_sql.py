from __future__ import annotations

import uuid


INTRA_PACKAGE_STATUS_RESOLUTION_VERSION = "CN_GOODS_STATUS_RESOLUTION_V1_STRONGEST_SIGNAL"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _application_range_predicate(
    application_lower: str | None,
    application_upper: str | None,
    *,
    column: str = "application_number",
) -> str:
    predicates: list[str] = []
    if application_lower is not None:
        predicates.append(f"{column} >= {_sql_string(application_lower)}")
    if application_upper is not None:
        predicates.append(f"{column} < {_sql_string(application_upper)}")
    if not predicates:
        return ""
    return "\n                      AND " + "\n                      AND ".join(predicates)


def incoming_goods_sql(
    package_uuid: uuid.UUID | str,
    application_lower: str | None = None,
    application_upper: str | None = None,
) -> str:
    """Build the M1.6 incoming-goods query with a strict alias boundary.

    ClickHouse 24.8 resolves SELECT aliases aggressively. Reusing permanent
    output names such as ``goods_name`` inside the same aggregate block can be
    rewritten as an aggregate-inside-aggregate expression (Code 184). The
    aggregate block therefore emits private ``agg_*`` names; a separate
    normalization block exposes the permanent names only after aggregation.

    Goods identity V2 deliberately includes sequence + similar group + normalized
    goods name. Sequence alone is not unique in real CN source data. Status is
    excluded so later status observations update the same logical item.

    A real package can contain more than one row for that same strict item with
    different status signals. Source-line order is not legal precedence, so the
    winner must not be chosen by ``argMax(..., source_start_line)`` alone. Within
    one package we conservatively keep the strongest observed source signal:
    code 2 > code 1 > code 0 > explicit inactive > unknown > explicit active >
    ordinary active/blank. The line number is only a deterministic tie-breaker
    between rows of equal signal strength. This rule is package-local; a later
    package still participates through the normal source-rank lifecycle logic.

    Large base partitions may contain tens of millions of goods rows. Optional
    application-number bounds are applied directly to the physically sorted
    stage table so callers can publish one contiguous application range at a
    time without changing item identity or package-local status precedence.
    """
    package = str(package_uuid)
    range_predicate = _application_range_predicate(
        application_lower,
        application_upper,
    )
    return f"""
        SELECT
            normalized.*,
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
                aggregated.case_id,
                aggregated.application_number,
                aggregated.class_no,
                aggregated.goods_item_key,
                aggregated.agg_goods_sequence AS goods_sequence,
                aggregated.agg_goods_name AS goods_name,
                lowerUTF8(aggregated.agg_goods_name) AS goods_name_norm,
                aggregated.agg_similar_group AS similar_group,
                aggregated.agg_goods_status_raw AS goods_status_raw,
                aggregated.agg_goods_status_bucket AS goods_status_bucket,
                aggregated.agg_goods_status_reason AS goods_status_reason,
                aggregated.agg_goods_status_mapping_version AS goods_status_mapping_version,
                aggregated.agg_source_file AS source_file,
                aggregated.agg_source_first_line AS source_first_line,
                aggregated.agg_source_last_line AS source_last_line,
                aggregated.agg_source_row_hash AS source_row_hash
            FROM
            (
                SELECT
                    case_id,
                    application_number,
                    class_no,
                    goods_item_key,
                    argMax(goods_sequence, toUInt64(stage_source_start_line))
                        AS agg_goods_sequence,
                    argMax(goods_name, toUInt64(stage_source_start_line))
                        AS agg_goods_name,
                    argMax(similar_group, toUInt64(stage_source_start_line))
                        AS agg_similar_group,
                    argMax(
                        goods_status_raw,
                        tuple(status_precedence, toUInt64(stage_source_start_line))
                    ) AS agg_goods_status_raw,
                    argMax(
                        goods_status_bucket,
                        tuple(status_precedence, toUInt64(stage_source_start_line))
                    ) AS agg_goods_status_bucket,
                    argMax(
                        goods_status_reason,
                        tuple(status_precedence, toUInt64(stage_source_start_line))
                    ) AS agg_goods_status_reason,
                    argMax(
                        goods_status_mapping_version,
                        tuple(status_precedence, toUInt64(stage_source_start_line))
                    ) AS agg_goods_status_mapping_version,
                    argMin(source_file, toUInt64(stage_source_start_line))
                        AS agg_source_file,
                    min(toUInt64(stage_source_start_line)) AS agg_source_first_line,
                    max(toUInt64(stage_source_end_line)) AS agg_source_last_line,
                    hex(SHA256(arrayStringConcat(
                        arraySort(groupArray(toString(row_hash))), '|'
                    ))) AS agg_source_row_hash
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
                        multiIf(
                            goods_status_raw = '2', toUInt8(70),
                            goods_status_raw = '1', toUInt8(60),
                            goods_status_raw = '0', toUInt8(50),
                            goods_status_reason = 'EXPLICIT_INACTIVE_TEXT', toUInt8(40),
                            goods_status_bucket = 'UNKNOWN', toUInt8(30),
                            goods_status_reason = 'EXPLICIT_ACTIVE_TEXT', toUInt8(20),
                            goods_status_bucket = 'ACTIVE', toUInt8(10),
                            toUInt8(0)
                        ) AS status_precedence,
                        hex(SHA256(concat(
                            application_number, '|', toString(class_no),
                            '|SEQ|', goods_sequence,
                            '|GROUP|', similar_group,
                            '|NAME|', lowerUTF8(goods_name)
                        ))) AS goods_item_key,
                        source_file,
                        source_start_line AS stage_source_start_line,
                        source_end_line AS stage_source_end_line,
                        row_hash
                    FROM markorbit_facts.cn_stage_goods
                    WHERE package_id = toUUID('{package}'){range_predicate}
                ) AS prepared
                GROUP BY case_id, application_number, class_no, goods_item_key
            ) AS aggregated
        ) AS normalized
    """
