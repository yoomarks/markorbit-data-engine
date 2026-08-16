from __future__ import annotations

from datetime import date
import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn.native_aux_snapshot import NativeAuxSnapshotExecutor
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a143")
SOURCE_RANK = 987_654_321
SOURCE_SHA = "143" + "a" * 61


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondPriorityInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._priority_inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def query(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.query(sql, *args, **kwargs)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_priority_current" in sql:
            self._priority_inserts += 1
            if self._priority_inserts == 2:
                raise RuntimeError("fixture native priority interruption")
        return self._delegate.command(sql, *args, **kwargs)


def _ensure_source_package() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM control.source_package WHERE package_id = %s",
                (str(PACKAGE_ID),),
            )
            cur.execute(
                """
                INSERT INTO control.source_package
                (
                    package_id, jurisdiction, file_name, file_path, file_size,
                    sha256, package_kind, partition_dimension, partition_value,
                    source_rank, status
                )
                VALUES (%s, 'CN', 'native-aux-fixture.zip', '/fixture/native-aux.zip', 0,
                        %s, 'MONTHLY_PATCH', 'MONTH', 'native-aux', %s, 'REGISTERED')
                """,
                (str(PACKAGE_ID), SOURCE_SHA, SOURCE_RANK),
            )
        conn.commit()


def _cleanup(client, package: str) -> None:
    predicates = (
        ("cn_stage_priority", f"package_id = toUUID('{package}')"),
        ("cn_stage_madrid", f"package_id = toUUID('{package}')"),
        ("cn_priority_current", f"last_source_package_id = toUUID('{package}')"),
        ("cn_madrid_current", f"last_source_package_id = toUUID('{package}')"),
    )
    for table, predicate in predicates:
        client.command(
            f"ALTER TABLE markorbit_facts.{table} DELETE WHERE {predicate}",
            settings={"mutations_sync": 1},
        )


def _stage_fixture_rows(client, package: str) -> None:
    hash_a = "a" * 64
    hash_b = "b" * 64
    hash_c = "c" * 64
    hash_d = "d" * 64
    client.insert(
        "cn_stage_priority",
        [
            [
                package, "P100", 1, "US-1", "EARLY", date(2020, 1, 1),
                "old goods", "US", "priority-a.xml", 10, 10, hash_a,
            ],
            [
                package, "P100", 1, "US-1", "LATEST", date(2020, 1, 2),
                "latest goods", "US", "priority-b.xml", 20, 20, hash_b,
            ],
            [
                package, "P200", 2, "JP-2", "NORMAL", date(2021, 2, 3),
                "goods 2", "JP", "priority-c.xml", 30, 30, hash_c,
            ],
            [
                package, "P300", 3, "DE-3", "NORMAL", date(2022, 3, 4),
                "goods 3", "DE", "priority-d.xml", 40, 40, hash_d,
            ],
        ],
        column_names=[
            "package_id",
            "application_number",
            "class_no",
            "priority_number",
            "priority_type",
            "priority_date",
            "priority_goods",
            "priority_country_region",
            "source_file",
            "source_start_line",
            "source_end_line",
            "row_hash",
        ],
    )
    client.insert(
        "cn_stage_madrid",
        [
            [
                package, "G100", "IR100", date(2019, 1, 1), date(2019, 1, 2),
                "EN", "EARLY", "1", date(2019, 1, 3), date(2019, 1, 4),
                date(2018, 12, 1), "madrid-a.xml", 10, 10, hash_a,
            ],
            [
                package, "G100", "IR100", date(2019, 1, 5), date(2019, 1, 6),
                "ZH", "LATEST", "2", date(2019, 1, 7), date(2019, 1, 8),
                date(2018, 12, 2), "madrid-b.xml", 20, 20, hash_b,
            ],
            [
                package, "G200", "IR200", date(2020, 2, 1), date(2020, 2, 2),
                "EN", "NORMAL", "3", date(2020, 2, 3), date(2020, 2, 4),
                date(2020, 1, 1), "madrid-c.xml", 30, 30, hash_c,
            ],
            [
                package, "G300", "IR300", date(2021, 3, 1), date(2021, 3, 2),
                "FR", "NORMAL", "4", date(2021, 3, 3), date(2021, 3, 4),
                date(2021, 2, 1), "madrid-d.xml", 40, 40, hash_d,
            ],
        ],
        column_names=[
            "package_id",
            "application_number",
            "international_registration_number",
            "international_registration_date",
            "international_notification_date",
            "application_language",
            "application_type",
            "international_pub_issue",
            "international_pub_date",
            "subsequent_designation_date",
            "basic_registration_date",
            "source_file",
            "source_start_line",
            "source_end_line",
            "row_hash",
        ],
    )


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _stage_fixture_rows(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        interrupted = NativeAuxSnapshotExecutor(
            client=_FailSecondPriorityInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            target_rows=2,
        )
        try:
            interrupted.execute("PRIORITY_CURRENT")
        except RuntimeError as exc:
            if "fixture native priority interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native priority interruption did not fire")

        partial_priority = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_priority_current FINAL
            WHERE last_source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if not 0 < int(partial_priority) < 3:
            raise AssertionError(f"expected partial priority snapshot, got {partial_priority}")

        resumed = NativeAuxSnapshotExecutor(
            client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            target_rows=2,
        )
        priority_result = resumed.execute("PRIORITY_CURRENT")
        madrid_result = resumed.execute("MADRID_CURRENT")
        if priority_result.range_count != 2 or priority_result.skipped != 1:
            raise AssertionError(priority_result)
        if priority_result.executed != 1:
            raise AssertionError(priority_result)
        if madrid_result.range_count != 2 or madrid_result.executed != 2:
            raise AssertionError(madrid_result)

        priority_rows = client.query(
            f"""
            SELECT application_number, priority_type, priority_goods, source_first_line,
                   source_last_line, source_rank
            FROM markorbit_facts.cn_priority_current FINAL
            WHERE last_source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        if priority_rows != [
            ("P100", "LATEST", "latest goods", 10, 20, SOURCE_RANK),
            ("P200", "NORMAL", "goods 2", 30, 30, SOURCE_RANK),
            ("P300", "NORMAL", "goods 3", 40, 40, SOURCE_RANK),
        ]:
            raise AssertionError(f"unexpected native priority current rows: {priority_rows}")

        madrid_rows = client.query(
            f"""
            SELECT application_number, application_language, application_type,
                   international_registration_date, source_first_line, source_last_line,
                   source_rank
            FROM markorbit_facts.cn_madrid_current FINAL
            WHERE last_source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        if madrid_rows != [
            ("G100", "ZH", "LATEST", date(2019, 1, 5), 10, 20, SOURCE_RANK),
            ("G200", "EN", "NORMAL", date(2020, 2, 1), 30, 30, SOURCE_RANK),
            ("G300", "FR", "NORMAL", date(2021, 3, 1), 40, 40, SOURCE_RANK),
        ]:
            raise AssertionError(f"unexpected native Madrid current rows: {madrid_rows}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native auxiliary snapshot fixture passed: "
            f"priority_ranges={priority_result.range_count} "
            f"priority_skipped={priority_result.skipped} "
            f"madrid_ranges={madrid_result.range_count}"
        )
    finally:
        try:
            clear_publish_checkpoint(PACKAGE_ID)
        finally:
            _cleanup(client, package)
            with postgres_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM control.source_package WHERE package_id = %s",
                        (str(PACKAGE_ID),),
                    )
                conn.commit()


if __name__ == "__main__":
    main()
