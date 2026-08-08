from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from app.cn.goods_lifecycle_sql import incoming_goods_sql
from app.db import clickhouse_client, postgres_conn


AUDIT_NAME = "CN_M16_MONTHLY_PATCH_ACCEPTANCE"
POLICY_VERSION = "CN_M16_MONTHLY_PATCH_POLICY_V2_CH24_TYPED_RECONCILIATION"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _package(file_name: str) -> dict[str, Any]:
    sql = """
    SELECT package_id, file_name, package_kind, partition_dimension, partition_value,
           status, source_rank
    FROM control.source_package
    WHERE jurisdiction = 'CN' AND file_name = %s
    ORDER BY source_rank DESC
    LIMIT 1
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (file_name,))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"CN package is not registered: {file_name}")
            return dict(row)


def _row_dict(result: Any, values: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(result.column_names, values, strict=True))


def _ints(row: dict[str, Any]) -> dict[str, int]:
    return {key: int(value or 0) for key, value in row.items()}


def build_audit(file_name: str) -> dict[str, Any]:
    package = _package(file_name)
    package_id = uuid.UUID(str(package["package_id"]))
    package_text = str(package_id)
    source_rank = int(package["source_rank"])
    rank_sql = f"toUInt64({source_rank})"
    incoming = incoming_goods_sql(package_id)
    client = clickhouse_client()

    coverage_result = client.query(f"""
        SELECT
            count() AS incoming_items,
            countIf(cur.application_number != '') AS current_key_hits,
            countIf(cur.application_number = '') AS missing_current_keys,
            countIf(cur.application_number != '' AND cur.first_source_rank < {rank_sql})
                AS cross_package_strict_key_matches,
            countIf(cur.application_number != '' AND cur.first_source_rank = {rank_sql})
                AS first_observed_in_patch,
            countIf(cur.application_number != '' AND cur.first_source_rank > {rank_sql})
                AS impossible_future_first_rank,
            countIf(
                cur.application_number != ''
                AND cur.last_source_package_id = toUUID('{package_text}')
            ) AS current_items_updated_by_patch,
            countIf(cur.application_number != '' AND cur.source_rank = {rank_sql})
                AS current_items_at_patch_rank
        FROM ({incoming}) AS inc
        LEFT JOIN markorbit_facts.cn_goods_item_current AS cur FINAL
          ON cur.application_number = inc.application_number
         AND cur.class_no = inc.class_no
         AND cur.goods_item_key = inc.goods_item_key
         AND cur.is_deleted = 0
    """)
    coverage = _ints(_row_dict(coverage_result, coverage_result.result_rows[0]))

    transition_result = client.query(f"""
        SELECT transition_type, count() AS item_count
        FROM markorbit_facts.cn_goods_item_observation FINAL
        WHERE source_package_id = toUUID('{package_text}')
        GROUP BY transition_type
        ORDER BY transition_type
    """)
    transitions = {str(row[0]): int(row[1] or 0) for row in transition_result.result_rows}
    observation_count = sum(transitions.values())

    touched = f"""
        SELECT DISTINCT application_number, class_no
        FROM markorbit_facts.cn_stage_goods
        WHERE package_id = toUUID('{package_text}')
    """
    incoming_by_scope = f"""
        SELECT application_number, class_no, count() AS _incoming_count
        FROM ({incoming})
        GROUP BY application_number, class_no
    """
    durable_by_scope = f"""
        SELECT item.application_number, item.class_no, count() AS _durable_count
        FROM markorbit_facts.cn_goods_item_current AS item FINAL
        INNER JOIN ({touched}) AS touched_scope
          ON touched_scope.application_number = item.application_number
         AND touched_scope.class_no = item.class_no
        WHERE item.is_deleted = 0
        GROUP BY item.application_number, item.class_no
    """

    # ClickHouse 24.8 has no exact common supertype for UInt64 count() and an
    # untyped integer literal in ifNull(). With join_use_nulls=0 (the default),
    # unmatched LEFT JOIN numeric fields already materialize as typed zero, so
    # this reconciliation deliberately avoids ifNull(..., 0) altogether.
    omission_result = client.query(f"""
        SELECT
            count() AS touched_scopes,
            sum(d._durable_count) AS durable_items_in_touched_scopes,
            sum(i._incoming_count) AS incoming_items_in_touched_scopes,
            sum(if(
                d._durable_count > i._incoming_count,
                d._durable_count - i._incoming_count,
                toUInt64(0)
            )) AS omitted_items_preserved,
            countIf(d._durable_count > i._incoming_count)
                AS scopes_with_omitted_items_preserved
        FROM ({touched}) AS t
        LEFT JOIN ({incoming_by_scope}) AS i
          ON i.application_number = t.application_number AND i.class_no = t.class_no
        LEFT JOIN ({durable_by_scope}) AS d
          ON d.application_number = t.application_number AND d.class_no = t.class_no
    """)
    omission = _ints(_row_dict(omission_result, omission_result.result_rows[0]))

    lifecycle_result = client.query(f"""
        SELECT
            countIf(toUInt64(l.known_item_count) != d._durable_count)
                AS lifecycle_scope_count_mismatches,
            countIf(l.last_source_package_id != toUUID('{package_text}'))
                AS lifecycle_scope_package_mismatches,
            countIf(l.source_rank != {rank_sql})
                AS lifecycle_scope_rank_mismatches,
            sum(toUInt64(l.unknown_item_count)) AS lifecycle_unknown_items
        FROM ({touched}) AS t
        LEFT JOIN ({durable_by_scope}) AS d
          ON d.application_number = t.application_number AND d.class_no = t.class_no
        LEFT JOIN markorbit_facts.cn_goods_scope_lifecycle_current AS l FINAL
          ON l.application_number = t.application_number
         AND l.class_no = t.class_no
         AND l.is_deleted = 0
    """)
    lifecycle = _ints(_row_dict(lifecycle_result, lifecycle_result.result_rows[0]))

    scope_result = client.query(f"""
        SELECT
            countIf(toUInt64(s.source_item_count) != d._durable_count)
                AS case_scope_count_mismatches,
            countIf(s.last_source_package_id != toUUID('{package_text}'))
                AS case_scope_package_mismatches,
            countIf(s.source_rank != {rank_sql})
                AS case_scope_rank_mismatches,
            sum(toUInt64(s.unmapped_status_item_count)) AS case_scope_unmapped_items
        FROM ({touched}) AS t
        LEFT JOIN ({durable_by_scope}) AS d
          ON d.application_number = t.application_number AND d.class_no = t.class_no
        LEFT JOIN markorbit_facts.cn_case_scope_current AS s FINAL
          ON s.application_number = t.application_number
         AND s.class_no = t.class_no
         AND s.is_deleted = 0
    """)
    scope_check = _ints(_row_dict(scope_result, scope_result.result_rows[0]))

    scope = {**omission, **lifecycle, **scope_check}

    sample_result = client.query(f"""
        SELECT
            item.application_number,
            item.class_no,
            item.goods_item_key,
            item.goods_sequence,
            item.similar_group,
            item.goods_name,
            item.goods_status_raw,
            item.first_source_package_kind,
            item.first_source_rank,
            toString(item.last_source_package_id) AS last_source_package_id,
            item.source_rank
        FROM markorbit_facts.cn_goods_item_current AS item FINAL
        INNER JOIN ({touched}) AS touched_scope
          ON touched_scope.application_number = item.application_number
         AND touched_scope.class_no = item.class_no
        LEFT JOIN ({incoming}) AS inc
          ON inc.application_number = item.application_number
         AND inc.class_no = item.class_no
         AND inc.goods_item_key = item.goods_item_key
        WHERE item.is_deleted = 0
          AND inc.application_number = ''
          AND item.source_rank < {rank_sql}
        ORDER BY item.application_number, item.class_no, item.goods_item_key
        LIMIT 12
    """)
    preserved_samples = [_row_dict(sample_result, values) for values in sample_result.result_rows]

    hard_fail_reasons: list[str] = []
    warnings: list[str] = []

    if str(package.get("package_kind")) != "MONTHLY_PATCH":
        hard_fail_reasons.append("package_is_not_monthly_patch")
    if str(package.get("status")) != "SUCCESS":
        hard_fail_reasons.append("package_is_not_success")
    if coverage["missing_current_keys"] != 0:
        hard_fail_reasons.append("incoming_strict_keys_missing_from_current_store")
    if coverage["impossible_future_first_rank"] != 0:
        hard_fail_reasons.append("current_items_have_future_first_source_rank")
    if coverage["current_items_updated_by_patch"] != coverage["incoming_items"]:
        hard_fail_reasons.append("incoming_items_not_current_at_patch_package")
    if coverage["current_items_at_patch_rank"] != coverage["incoming_items"]:
        hard_fail_reasons.append("incoming_items_not_current_at_patch_rank")
    if observation_count != coverage["incoming_items"]:
        hard_fail_reasons.append("observation_count_does_not_match_incoming_items")

    for field in (
        "lifecycle_scope_count_mismatches",
        "case_scope_count_mismatches",
        "lifecycle_scope_package_mismatches",
        "case_scope_package_mismatches",
        "lifecycle_scope_rank_mismatches",
        "case_scope_rank_mismatches",
        "lifecycle_unknown_items",
        "case_scope_unmapped_items",
    ):
        if scope[field] != 0:
            hard_fail_reasons.append(field)

    if scope["incoming_items_in_touched_scopes"] != coverage["incoming_items"]:
        hard_fail_reasons.append("scope_incoming_count_does_not_match_strict_item_count")
    if coverage["cross_package_strict_key_matches"] == 0:
        warnings.append("no_cross_package_strict_key_match_observed")
    if scope["omitted_items_preserved"] == 0:
        warnings.append("no_omitted_item_preservation_observed")

    status = "FAIL" if hard_fail_reasons else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    return {
        "status": status,
        "audit": AUDIT_NAME,
        "policy_version": POLICY_VERSION,
        "package": {
            "package_id": package_text,
            "file_name": str(package["file_name"]),
            "package_kind": str(package["package_kind"]),
            "partition_dimension": str(package["partition_dimension"]),
            "partition_value": str(package["partition_value"]),
            "source_rank": source_rank,
            "source_status": str(package["status"]),
        },
        "hard_fail_reasons": hard_fail_reasons,
        "warning_reasons": warnings,
        "strict_key_reconciliation": coverage,
        "observations": {"total": observation_count, "transition_types": transitions},
        "scope_reconciliation": scope,
        "omission_preservation_samples": preserved_samples,
        "policy_note": (
            "A monthly patch may update only changed goods. Strict-key matches must retain the "
            "original first-source lineage, every incoming item must become current at the patch "
            "rank, and touched scopes must be rebuilt from the complete durable item store. "
            "Items omitted from the monthly package must remain present when they were previously "
            "known and not explicitly superseded by a later observation."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file_name")
    args = parser.parse_args()
    print(json.dumps(build_audit(args.file_name), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
