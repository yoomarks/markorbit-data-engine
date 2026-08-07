from __future__ import annotations

from datetime import date
import json
import uuid

from app.cn.goods_lifecycle import (
    cleanup_goods_outputs,
    ensure_m16_goods_schema,
    publish_goods_lifecycle,
    scope_from_current_items_sql,
)
from app.cn.ingest import STAGE_COLUMNS, _cleanup_stage, _other_stage_row
from app.db import clickhouse_client


def _stage_goods(
    package_uuid: uuid.UUID,
    application_number: str,
    rows: list[tuple[str, str]],
) -> None:
    client = clickhouse_client()
    staged_rows: list[list[object]] = []
    for index, (sequence, status_code) in enumerate(rows, start=1):
        record = {
            "application_number": application_number,
            "class_no": "25",
            "similar_group": f"250{index}",
            "goods_sequence": sequence,
            "goods_name": f"M16 fixture goods {sequence}",
            "goods_status_raw": status_code,
        }
        staged = _other_stage_row(
            "goods",
            package_uuid,
            record,
            "M16_GOODS_FIXTURE.csv",
            index + 1,
            index + 1,
        )
        if staged is None:
            raise RuntimeError(f"fixture row failed staging: {record}")
        table, row, _, _ = staged
        if table != "markorbit_facts.cn_stage_goods":
            raise RuntimeError(f"unexpected stage table: {table}")
        staged_rows.append(row)
    client.insert(
        "markorbit_facts.cn_stage_goods",
        staged_rows,
        column_names=STAGE_COLUMNS["markorbit_facts.cn_stage_goods"],
    )


def main() -> None:
    ensure_m16_goods_schema()
    client = clickhouse_client()
    package_base = uuid.uuid4()
    package_patch = uuid.uuid4()
    application_number = f"99{uuid.uuid4().int % 10_000_000_000:010d}"

    base_meta = {
        "package_kind": "BASE_PARTITION",
        "source_rank": 1_000_000_000_000_101,
        "source_period_end": None,
    }
    patch_meta = {
        "package_kind": "MONTHLY_PATCH",
        "source_rank": 2_026_080_000_000_102,
        "source_period_end": date(2026, 8, 31),
    }

    try:
        _stage_goods(
            package_base,
            application_number,
            [("1", ""), ("2", ""), ("3", "")],
        )
        publish_goods_lifecycle(package_base, base_meta)

        baseline = client.query(
            f"""
            SELECT goods_sequence, goods_status_raw
            FROM markorbit_facts.cn_goods_item_current FINAL
            WHERE application_number = '{application_number}' AND class_no = 25
              AND is_deleted = 0
            ORDER BY goods_sequence
            """
        ).result_rows
        if baseline != [("1", ""), ("2", ""), ("3", "")]:
            raise AssertionError(f"baseline goods mismatch: {baseline}")

        # The monthly patch contains only one changed item. M1.6 must not erase
        # the two omitted baseline items.
        _stage_goods(package_patch, application_number, [("2", "2")])
        publish_goods_lifecycle(package_patch, patch_meta)

        current = client.query(
            f"""
            SELECT goods_sequence, goods_status_raw, operational_effect
            FROM markorbit_facts.cn_goods_item_current FINAL
            WHERE application_number = '{application_number}' AND class_no = 25
              AND is_deleted = 0
            ORDER BY goods_sequence
            """
        ).result_rows
        expected = [
            ("1", "", "EFFECTIVE_UNLESS_CONTRADICTED"),
            ("2", "2", "INACTIVE_CONFIRMED"),
            ("3", "", "EFFECTIVE_UNLESS_CONTRADICTED"),
        ]
        if current != expected:
            raise AssertionError(f"monthly delta erased or corrupted goods: {current}")

        scope = client.query(scope_from_current_items_sql(package_patch)).result_rows
        if len(scope) != 1:
            raise AssertionError(f"expected one touched scope, got {len(scope)}")
        # First columns: case_id, application_number, class_no, source_item_count,
        # active, inactive, unknown, risk ...
        row = scope[0]
        if int(row[3]) != 3 or int(row[4]) != 2 or int(row[5]) != 1 or int(row[6]) != 0:
            raise AssertionError(f"reconstructed scope counts are wrong: {row[:8]}")

        observation_count = int(
            client.query(
                f"""
                SELECT count()
                FROM markorbit_facts.cn_goods_item_observation FINAL
                WHERE application_number = '{application_number}'
                """
            ).result_rows[0][0]
        )
        if observation_count != 4:
            raise AssertionError(
                f"expected 4 item observations (3 base + 1 patch), got {observation_count}"
            )

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "M1.6",
                    "fixture": "base-three-items-monthly-one-item-delta",
                    "known_items_after_patch": 3,
                    "operational_effective_items": 2,
                    "final_inactive_items": 1,
                    "observations": observation_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        for package_uuid in (package_patch, package_base):
            try:
                cleanup_goods_outputs(package_uuid)
            except Exception:
                pass
            try:
                _cleanup_stage(package_uuid)
            except Exception:
                pass


if __name__ == "__main__":
    main()
