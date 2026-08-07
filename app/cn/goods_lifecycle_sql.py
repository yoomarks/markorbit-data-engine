from __future__ import annotations

import uuid


def incoming_goods_sql(package_uuid: uuid.UUID | str) -> str:
    """Build the M1.6 incoming-goods query with a strict alias boundary.

    ClickHouse 24.8 resolves SELECT aliases aggressively. Reusing permanent
    output names such as ``goods_name`` inside the same aggregate block can be
    rewritten as an aggregate-inside-aggregate expression (Code 184). The
    aggregate block therefore emits private ``agg_*`` names; a separate
    normalization block exposes the permanent names only after aggregation.

    Goods identity V2 deliberately includes sequence + similar group + normalized
    goods name. Sequence alone is not unique in real CN source data. Status is
    excluded so later status observations update the same logical item.
    """
    package = str(package_uuid)
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
                    argMax(goods_status_raw, toUInt64(stage_source_start_line))
                        AS agg_goods_status_raw,
                    argMax(goods_status_bucket, toUInt64(stage_source_start_line))
                        AS agg_goods_status_bucket,
                    argMax(goods_status_reason, toUInt64(stage_source_start_line))
                        AS agg_goods_status_reason,
                    argMax(goods_status_mapping_version, toUInt64(stage_source_start_line))
                        AS agg_goods_status_mapping_version,
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
                    WHERE package_id = toUUID('{package}')
                ) AS prepared
                GROUP BY case_id, application_number, class_no, goods_item_key
            ) AS aggregated
        ) AS normalized
    """
