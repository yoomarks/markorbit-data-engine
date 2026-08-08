from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from app.cn.goods_lifecycle_sql import incoming_goods_sql
from app.db import clickhouse_client, postgres_conn

AUDIT_NAME = "CN_M16_MONTHLY_PATCH_ACCEPTANCE"
POLICY_VERSION = "CN_M16_MONTHLY_PATCH_POLICY_V4_CH24_SAFE_RECONCILIATION"


def _package(file_name: str) -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, file_name, package_kind, partition_dimension,
                       partition_value, status, source_rank
                FROM control.source_package
                WHERE jurisdiction = 'CN' AND file_name = %s
                ORDER BY source_rank DESC LIMIT 1
                """,
                (file_name,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"CN package is not registered: {file_name}")
            return dict(row)


def _dict(result: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(result.column_names, row, strict=True))


def _ints(result: Any) -> dict[str, int]:
    return {k: int(v or 0) for k, v in _dict(result, result.result_rows[0]).items()}


def build_audit(file_name: str) -> dict[str, Any]:
    package = _package(file_name)
    package_id = uuid.UUID(str(package["package_id"]))
    package_text = str(package_id)
    source_rank = int(package["source_rank"])
    rank_sql = f"toUInt64({source_rank})"
    incoming = incoming_goods_sql(package_id)
    client = clickhouse_client()

    coverage = _ints(client.query(f"""
        SELECT
            count() incoming_items,
            countIf(cur.application_number != '') current_key_hits,
            countIf(cur.application_number = '') missing_current_keys,
            countIf(cur.application_number != '' AND cur.first_source_rank < {rank_sql})
                cross_package_strict_key_matches,
            countIf(cur.application_number != '' AND cur.first_source_rank = {rank_sql})
                first_observed_in_patch,
            countIf(cur.application_number != '' AND cur.first_source_rank > {rank_sql})
                impossible_future_first_rank,
            countIf(cur.last_source_package_id = toUUID('{package_text}'))
                current_items_updated_by_patch,
            countIf(cur.source_rank = {rank_sql}) current_items_at_patch_rank
        FROM ({incoming}) inc
        LEFT JOIN markorbit_facts.cn_goods_item_current cur FINAL
          ON cur.application_number = inc.application_number
         AND cur.class_no = inc.class_no
         AND cur.goods_item_key = inc.goods_item_key
         AND cur.is_deleted = 0
    """))

    transition_result = client.query(f"""
        SELECT transition_type, count()
        FROM markorbit_facts.cn_goods_item_observation FINAL
        WHERE source_package_id = toUUID('{package_text}')
        GROUP BY transition_type ORDER BY transition_type
    """)
    transitions = {str(r[0]): int(r[1] or 0) for r in transition_result.result_rows}
    observation_count = sum(transitions.values())

    touched = f"""
        SELECT DISTINCT application_number, class_no
        FROM markorbit_facts.cn_stage_goods
        WHERE package_id = toUUID('{package_text}')
    """
    incoming_scope = f"""
        SELECT application_number, class_no, count() _incoming_count
        FROM ({incoming}) GROUP BY application_number, class_no
    """
    durable_scope = f"""
        SELECT item.application_number, item.class_no, count() _durable_count
        FROM markorbit_facts.cn_goods_item_current item FINAL
        INNER JOIN ({touched}) t
          ON t.application_number = item.application_number AND t.class_no = item.class_no
        WHERE item.is_deleted = 0
        GROUP BY item.application_number, item.class_no
    """

    touched_scopes = int(client.query(f"SELECT count() FROM ({touched})").result_rows[0][0] or 0)
    omission = _ints(client.query(f"""
        SELECT
            count() joined_scopes,
            sum(toInt64(d._durable_count)) durable_items_in_touched_scopes,
            sum(toInt64(i._incoming_count)) incoming_items_in_touched_scopes,
            sum(toInt64(d._durable_count) - toInt64(i._incoming_count)) omitted_items_preserved,
            countIf(d._durable_count > i._incoming_count) scopes_with_omitted_items_preserved,
            countIf(d._durable_count < i._incoming_count) scopes_with_impossible_item_loss
        FROM ({incoming_scope}) i
        INNER JOIN ({durable_scope}) d
          ON d.application_number = i.application_number AND d.class_no = i.class_no
    """))
    omission["touched_scopes"] = touched_scopes

    lifecycle = _ints(client.query(f"""
        SELECT
            count() joined_lifecycle_scopes,
            countIf(toUInt64(l.known_item_count) != d._durable_count)
                lifecycle_scope_count_mismatches,
            countIf(l.last_source_package_id != toUUID('{package_text}'))
                lifecycle_scope_package_mismatches,
            countIf(l.source_rank != {rank_sql}) lifecycle_scope_rank_mismatches,
            sum(toUInt64(l.unknown_item_count)) lifecycle_unknown_items
        FROM ({durable_scope}) d
        INNER JOIN markorbit_facts.cn_goods_scope_lifecycle_current l FINAL
          ON l.application_number = d.application_number
         AND l.class_no = d.class_no
         AND l.is_deleted = 0
    """))

    scope_check = _ints(client.query(f"""
        SELECT
            count() joined_case_scopes,
            countIf(toUInt64(s.source_item_count) != d._durable_count)
                case_scope_count_mismatches,
            countIf(s.last_source_package_id != toUUID('{package_text}'))
                case_scope_package_mismatches,
            countIf(s.source_rank != {rank_sql}) case_scope_rank_mismatches,
            sum(toUInt64(s.unmapped_status_item_count)) case_scope_unmapped_items
        FROM ({durable_scope}) d
        INNER JOIN markorbit_facts.cn_case_scope_current s FINAL
          ON s.application_number = d.application_number
         AND s.class_no = d.class_no
         AND s.is_deleted = 0
    """))
    scope = {**omission, **lifecycle, **scope_check}

    sample_result = client.query(f"""
        SELECT item.application_number, item.class_no, item.goods_item_key,
               item.goods_sequence, item.similar_group, item.goods_name,
               item.goods_status_raw, item.first_source_package_kind,
               item.first_source_rank, toString(item.last_source_package_id), item.source_rank
        FROM markorbit_facts.cn_goods_item_current item FINAL
        INNER JOIN ({touched}) t
          ON t.application_number = item.application_number AND t.class_no = item.class_no
        LEFT JOIN ({incoming}) inc
          ON inc.application_number = item.application_number
         AND inc.class_no = item.class_no
         AND inc.goods_item_key = item.goods_item_key
        WHERE item.is_deleted = 0 AND inc.application_number = ''
          AND item.source_rank < {rank_sql}
        ORDER BY item.application_number, item.class_no, item.goods_item_key LIMIT 12
    """)
    samples = [_dict(sample_result, row) for row in sample_result.result_rows]

    hard: list[str] = []
    warnings: list[str] = []
    if str(package["package_kind"]) != "MONTHLY_PATCH": hard.append("package_is_not_monthly_patch")
    if str(package["status"]) != "SUCCESS": hard.append("package_is_not_success")
    if coverage["missing_current_keys"]: hard.append("incoming_strict_keys_missing_from_current_store")
    if coverage["impossible_future_first_rank"]: hard.append("current_items_have_future_first_source_rank")
    if coverage["current_items_updated_by_patch"] != coverage["incoming_items"]: hard.append("incoming_items_not_current_at_patch_package")
    if coverage["current_items_at_patch_rank"] != coverage["incoming_items"]: hard.append("incoming_items_not_current_at_patch_rank")
    if observation_count != coverage["incoming_items"]: hard.append("observation_count_does_not_match_incoming_items")
    if omission["joined_scopes"] != touched_scopes: hard.append("incoming_or_durable_scope_missing")
    if omission["scopes_with_impossible_item_loss"]: hard.append("durable_scope_has_fewer_items_than_patch")
    if lifecycle["joined_lifecycle_scopes"] != touched_scopes: hard.append("lifecycle_scope_missing")
    if scope_check["joined_case_scopes"] != touched_scopes: hard.append("case_scope_missing")
    for field in (
        "lifecycle_scope_count_mismatches", "case_scope_count_mismatches",
        "lifecycle_scope_package_mismatches", "case_scope_package_mismatches",
        "lifecycle_scope_rank_mismatches", "case_scope_rank_mismatches",
        "lifecycle_unknown_items", "case_scope_unmapped_items",
    ):
        if scope[field]: hard.append(field)
    if scope["incoming_items_in_touched_scopes"] != coverage["incoming_items"]:
        hard.append("scope_incoming_count_does_not_match_strict_item_count")
    if coverage["cross_package_strict_key_matches"] == 0:
        warnings.append("no_cross_package_strict_key_match_observed")
    if scope["omitted_items_preserved"] == 0:
        warnings.append("no_omitted_item_preservation_observed")

    status = "FAIL" if hard else ("PASS_WITH_WARNINGS" if warnings else "PASS")
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
        "hard_fail_reasons": hard,
        "warning_reasons": warnings,
        "strict_key_reconciliation": coverage,
        "observations": {"total": observation_count, "transition_types": transitions},
        "scope_reconciliation": scope,
        "omission_preservation_samples": samples,
        "policy_note": "Monthly omission is not deletion; touched scopes must rebuild from durable strict-key item state.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file_name")
    args = parser.parse_args()
    print(json.dumps(build_audit(args.file_name), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
