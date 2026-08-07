from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

from app.cn.goods_lifecycle import GOODS_ITEM_IDENTITY_VERSION
from app.cn.ingest import StageBatchWriter, _cleanup_stage, _other_stage_row
from app.cn.reader import iter_member_rows
from app.cn.zipio import iter_package_members
from app.config import get_settings
from app.db import clickhouse_client


def _resolve_raw_package(file_name: str) -> Path:
    root = get_settings().raw_data_root
    candidates = [
        root / "incoming" / "cn" / file_name,
        root / "archive" / "cn" / file_name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"CN raw package not found under incoming/archive: {file_name}"
    )


def _strict_item_key_sql() -> str:
    return """
        hex(SHA256(concat(
            application_number, '|', toString(class_no),
            '|SEQ|', goods_sequence,
            '|GROUP|', similar_group,
            '|NAME|', lowerUTF8(goods_name)
        )))
    """.strip()


def _identity_summary_sql(package: str) -> str:
    item_key = _strict_item_key_sql()
    return f"""
        SELECT
            sum(source_rows_per_key) AS source_rows,
            count() AS logical_item_keys,
            sum(source_rows_per_key - 1) AS collapsed_source_rows,
            countIf(source_rows_per_key > 1 AND variant_count = 1) AS exact_duplicate_keys,
            sumIf(source_rows_per_key - 1, source_rows_per_key > 1 AND variant_count = 1)
                AS exact_duplicate_excess_rows,
            countIf(variant_count > 1) AS conflicting_identity_keys,
            sumIf(source_rows_per_key - 1, variant_count > 1)
                AS conflicting_excess_rows
        FROM
        (
            SELECT
                item_key_internal,
                count() AS source_rows_per_key,
                uniqExact(tuple(
                    goods_status_raw,
                    goods_status_bucket,
                    goods_status_reason
                )) AS variant_count
            FROM
            (
                SELECT
                    application_number,
                    class_no,
                    goods_sequence,
                    similar_group,
                    goods_name,
                    goods_status_raw,
                    goods_status_bucket,
                    goods_status_reason,
                    {item_key} AS item_key_internal
                FROM markorbit_facts.cn_stage_goods
                WHERE package_id = toUUID('{package}')
            ) AS prepared_rows
            GROUP BY item_key_internal
        ) AS identity_groups
    """


def _conflict_samples_sql(package: str) -> str:
    item_key = _strict_item_key_sql()
    return f"""
        SELECT
            application_number,
            class_no,
            goods_sequence,
            similar_group,
            goods_name,
            source_rows_per_key,
            variant_count,
            sample_status_variants
        FROM
        (
            SELECT
                any(application_number) AS application_number,
                any(class_no) AS class_no,
                any(goods_sequence) AS goods_sequence,
                any(similar_group) AS similar_group,
                any(goods_name) AS goods_name,
                item_key_internal,
                count() AS source_rows_per_key,
                uniqExact(tuple(
                    goods_status_raw,
                    goods_status_bucket,
                    goods_status_reason
                )) AS variant_count,
                groupUniqArray(5)(tuple(
                    goods_status_raw,
                    goods_status_bucket,
                    goods_status_reason
                )) AS sample_status_variants
            FROM
            (
                SELECT
                    application_number,
                    class_no,
                    goods_sequence,
                    similar_group,
                    goods_name,
                    goods_status_raw,
                    goods_status_bucket,
                    goods_status_reason,
                    {item_key} AS item_key_internal
                FROM markorbit_facts.cn_stage_goods
                WHERE package_id = toUUID('{package}')
            ) AS prepared_rows
            GROUP BY item_key_internal
            HAVING variant_count > 1
        ) AS conflicts
        ORDER BY variant_count DESC, source_rows_per_key DESC
        LIMIT 20
    """


def audit_goods_identity(file_name: str) -> dict[str, object]:
    path = _resolve_raw_package(file_name)
    package_uuid = uuid.uuid4()
    package = str(package_uuid)
    writer = StageBatchWriter()
    parsed_goods_rows = 0

    try:
        for member in iter_package_members(path):
            if member.schema is None or member.schema.role != "goods":
                continue
            _, rows = iter_member_rows(member)
            for parsed in rows:
                parsed_goods_rows += 1
                staged = _other_stage_row(
                    "goods",
                    package_uuid,
                    parsed.record,
                    member.internal_name,
                    parsed.source_start_line,
                    parsed.source_end_line,
                )
                if staged is None:
                    continue
                table, row, _, _ = staged
                writer.add(table, row)
        writer.close()

        client = clickhouse_client()
        row = client.query(_identity_summary_sql(package)).result_rows[0]
        samples = client.query(_conflict_samples_sql(package)).result_rows
        result = {
            "status": "PASS" if int(row[5] or 0) == 0 else "CONFLICT",
            "contract": "M1.6_GOODS_ITEM_IDENTITY_AUDIT",
            "identity_version": GOODS_ITEM_IDENTITY_VERSION,
            "file_name": file_name,
            "parsed_goods_rows": parsed_goods_rows,
            "staged_goods_rows": int(row[0] or 0),
            "logical_item_keys": int(row[1] or 0),
            "collapsed_source_rows": int(row[2] or 0),
            "exact_duplicate_keys": int(row[3] or 0),
            "exact_duplicate_excess_rows": int(row[4] or 0),
            "conflicting_identity_keys": int(row[5] or 0),
            "conflicting_excess_rows": int(row[6] or 0),
            "conflict_samples": [
                {
                    "application_number": str(item[0]),
                    "class_no": int(item[1]),
                    "goods_sequence": str(item[2]),
                    "similar_group": str(item[3]),
                    "goods_name": str(item[4]),
                    "source_rows": int(item[5]),
                    "variant_count": int(item[6]),
                    "status_variants": item[7],
                }
                for item in samples
            ],
        }
        return result
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            _cleanup_stage(package_uuid)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file_name", nargs="?", default="1999.zip")
    args = parser.parse_args()
    print(json.dumps(audit_goods_identity(args.file_name), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
