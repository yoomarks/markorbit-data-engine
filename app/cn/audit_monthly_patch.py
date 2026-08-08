from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from app.db import clickhouse_client, postgres_conn

AUDIT_NAME = "CN_M16_MONTHLY_PATCH_ACCEPTANCE"
POLICY_VERSION = "CN_M16_MONTHLY_PATCH_POLICY_V5_DURABLE_OBSERVATION_RECONCILIATION"


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
    return {key: int(value or 0) for key, value in _dict(result, result.result_rows[0]).items()}


def build_audit(file_name: str) -> dict[str, Any]:
    package = _package(file_name)
    package_id = uuid.UUID(str(package["package_id"]))
    package_text = str(package_id)
    source_rank = int(package["source_rank"])
    rank_sql = f"toUInt64({source_rank})"
    client = clickhouse_client()

    # Stage tables are intentionally transient and may be empty immediately after
    # a successful package run. The durable goods observation table is the exact
    # post-publish record of the resolved strict items that arrived in this patch,
    # so monthly acceptance must reconcile from observations, not staging rows.
    patch_items = f"""
        SELECT application_number, class_no, goods_item_key
        FROM markorbit_facts.cn_goods_item_observation FINAL
        WHERE source_package_id = toUUID('{package_text}')
    """

    coverage = _ints(client.query(f"""
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
                AS current_items_at_patch_rank,
            countIf(cur.application_number != '' AND cur.source_rank >= {rank_sql})
                AS current_items_at_or_after_patch_rank,
            countIf(cur.application_number != '' AND cur.source_rank < {rank_sql})
                AS current_items_older_than_patch_rank
        FROM ({patch_items}) AS patch
        LEFT JOIN markorbit_facts.cn_goods_item_current AS cur FINAL
          ON cur.application_number = patch.application_number
         AND cur.class_no = patch.class_no
         AND cur.goods_item_key = patch.goods_item_key
         AND cur.is_deleted = 0
    """))

    transition_result = client.query(f"""
        SELECT transition_type, count()
        FROM markorbit_facts.cn_goods_item_observation FINAL
        WHERE source_package_id = toUUID('{package_text}')
        GROUP BY transition_type
        ORDER BY transition_type
    """)
    transitions = {str(row[0]): int(row[1] or 0) for row in transition_result.result_rows}
    observation_count = sum(transitions.values())
    first_observed_count = int(transitions.get("FIRST_OBSERVED", 0))
    previously_observed_count = observation_count - first_observed_count

    touched = f"""
        SELECT DISTINCT application_number, class_no
        FROM ({patch_items})
    """
    incoming_scope = f"""
        SELECT application_number, class_no, count() AS _incoming_count
        FROM ({patch_items})
        GROUP BY application_number, class_no
    """
    durable_scope = f"""
        SELECT item.application_number, item.class_no, count() AS _durable_count
        FROM markorbit_facts.cn_goods_item_current AS item FINAL
        INNER JOIN ({touched}) AS touched_scope
          ON touched_scope.application_number = item.application_number
         AND touched_scope.class_no = item.class_no
        WHERE item.is_deleted = 0
        GROUP BY item.application_number, item.class_no
    """

    touched_scopes = int(
        client.query(f"SELECT count() FROM ({incoming_scope})").result_rows[0][0] or 0
    )
    omission = _ints(client.query(f"""
        SELECT
            count() AS joined_scopes,
            sum(toInt64(durable._durable_count)) AS durable_items_in_touched_scopes,
            sum(toInt64(incoming._incoming_count)) AS incoming_items_in_touched_scopes,
            sum(toInt64(durable._durable_count) - toInt64(incoming._incoming_count))
                AS omitted_items_preserved,
            countIf(durable._durable_count > incoming._incoming_count)
                AS scopes_with_omitted_items_preserved,
            countIf(durable._durable_count < incoming._incoming_count)
                AS scopes_with_impossible_item_loss
        FROM ({incoming_scope}) AS incoming
        INNER JOIN ({durable_scope}) AS durable
          ON durable.application_number = incoming.application_number
         AND durable.class_no = incoming.class_no
    """))
    omission["touched_scopes"] = touched_scopes

    lifecycle = _ints(client.query(f"""
        SELECT
            count() AS joined_lifecycle_scopes,
            countIf(toUInt64(life.known_item_count) != durable._durable_count)
                AS lifecycle_scope_count_mismatches,
            countIf(life.last_source_package_id != toUUID('{package_text}'))
                AS lifecycle_scope_package_mismatches,
            countIf(life.source_rank != {rank_sql})
                AS lifecycle_scope_rank_mismatches,
            sum(toUInt64(life.unknown_item_count)) AS lifecycle_unknown_items
        FROM ({durable_scope}) AS durable
        INNER JOIN markorbit_facts.cn_goods_scope_lifecycle_current AS life FINAL
          ON life.application_number = durable.application_number
         AND life.class_no = durable.class_no
         AND life.is_deleted = 0
    """))

    scope_check = _ints(client.query(f"""
        SELECT
            count() AS joined_case_scopes,
            countIf(toUInt64(scope.source_item_count) != durable._durable_count)
                AS case_scope_count_mismatches,
            countIf(scope.last_source_package_id != toUUID('{package_text}'))
                AS case_scope_package_mismatches,
            countIf(scope.source_rank != {rank_sql})
                AS case_scope_rank_mismatches,
            sum(toUInt64(scope.unmapped_status_item_count)) AS case_scope_unmapped_items
        FROM ({durable_scope}) AS durable
        INNER JOIN markorbit_facts.cn_case_scope_current AS scope FINAL
          ON scope.application_number = durable.application_number
         AND scope.class_no = durable.class_no
         AND scope.is_deleted = 0
    """))
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
        LEFT JOIN ({patch_items}) AS patch
          ON patch.application_number = item.application_number
         AND patch.class_no = item.class_no
         AND patch.goods_item_key = item.goods_item_key
        WHERE item.is_deleted = 0
          AND patch.application_number = ''
          AND item.source_rank < {rank_sql}
        ORDER BY item.application_number, item.class_no, item.goods_item_key
        LIMIT 12
    """)
    samples = [_dict(sample_result, row) for row in sample_result.result_rows]

    hard: list[str] = []
    warnings: list[str] = []

    if str(package["package_kind"]) != "MONTHLY_PATCH":
        hard.append("package_is_not_monthly_patch")
    if str(package["status"]) != "SUCCESS":
        hard.append("package_is_not_success")
    if observation_count == 0:
        hard.append("package_has_no_durable_goods_observations")
    if coverage["missing_current_keys"]:
        hard.append("observed_strict_keys_missing_from_current_store")
    if coverage["impossible_future_first_rank"]:
        hard.append("current_items_have_future_first_source_rank")
    if coverage["current_items_older_than_patch_rank"]:
        hard.append("observed_items_regressed_below_patch_rank")
    if coverage["current_items_at_or_after_patch_rank"] != coverage["incoming_items"]:
        hard.append("observed_items_not_current_at_or_after_patch_rank")
    if observation_count != coverage["incoming_items"]:
        hard.append("observation_count_does_not_match_patch_item_count")
    if coverage["first_observed_in_patch"] != first_observed_count:
        hard.append("first_source_lineage_disagrees_with_first_observed_transitions")
    if coverage["cross_package_strict_key_matches"] != previously_observed_count:
        hard.append("first_source_lineage_disagrees_with_existing_item_transitions")

    if omission["joined_scopes"] != touched_scopes:
        hard.append("incoming_or_durable_scope_missing")
    if omission["scopes_with_impossible_item_loss"]:
        hard.append("durable_scope_has_fewer_items_than_patch")
    if lifecycle["joined_lifecycle_scopes"] != touched_scopes:
        hard.append("lifecycle_scope_missing")
    if scope_check["joined_case_scopes"] != touched_scopes:
        hard.append("case_scope_missing")

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
        if scope[field]:
            hard.append(field)

    if scope["incoming_items_in_touched_scopes"] != coverage["incoming_items"]:
        hard.append("scope_incoming_count_does_not_match_patch_item_count")
    if previously_observed_count == 0:
        warnings.append("patch_contains_no_previously_observed_strict_items")
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
        "observations": {
            "total": observation_count,
            "first_observed": first_observed_count,
            "previously_observed": previously_observed_count,
            "transition_types": transitions,
        },
        "scope_reconciliation": scope,
        "omission_preservation_samples": samples,
        "policy_note": (
            "Monthly acceptance is reconstructed from durable goods observations because staging "
            "is transient. Existing strict items must retain first-source lineage, and touched "
            "scopes must equal the complete durable item set so omission never implies deletion."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file_name")
    args = parser.parse_args()
    print(json.dumps(build_audit(args.file_name), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
