from __future__ import annotations

from datetime import date
import os
import uuid

import clickhouse_connect

from app.cn.legacy_snapshot_persist import (
    LegacySnapshotPersistClient,
    plan_application_ranges,
)


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


def _priority_insert(package: str, source_rank: int) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_priority_current
        SELECT
            application_number, class_no, priority_number,
            argMax(priority_type, toUInt64(source_start_line)),
            argMax(priority_date, toUInt64(source_start_line)),
            argMax(priority_goods, toUInt64(source_start_line)),
            argMax(priority_country_region, toUInt64(source_start_line)),
            argMin(source_file, toUInt64(source_start_line)),
            min(toUInt64(source_start_line)),
            max(toUInt64(source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|'))),
            hex(SHA256(concat(
                application_number, '|', toString(class_no), '|', priority_number, '|',
                argMax(priority_goods, toUInt64(source_start_line))
            ))), toUUID('{package}'), {source_rank}, now64(3), 0
        FROM markorbit_facts.cn_stage_priority
        WHERE package_id = toUUID('{package}')
        GROUP BY application_number, class_no, priority_number
    """


def _madrid_insert(package: str, source_rank: int) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_madrid_current
        SELECT
            application_number, international_registration_number,
            argMax(international_registration_date, toUInt64(source_start_line)),
            argMax(international_notification_date, toUInt64(source_start_line)),
            argMax(application_language, toUInt64(source_start_line)),
            argMax(application_type, toUInt64(source_start_line)),
            argMax(international_pub_issue, toUInt64(source_start_line)),
            argMax(international_pub_date, toUInt64(source_start_line)),
            argMax(subsequent_designation_date, toUInt64(source_start_line)),
            argMax(basic_registration_date, toUInt64(source_start_line)),
            argMin(source_file, toUInt64(source_start_line)),
            min(toUInt64(source_start_line)),
            max(toUInt64(source_end_line)),
            hex(SHA256(arrayStringConcat(arraySort(groupArray(toString(row_hash))), '|'))),
            hex(SHA256(concat(
                application_number, '|', international_registration_number, '|',
                ifNull(toString(argMax(
                    international_registration_date, toUInt64(source_start_line)
                )), '')
            ))), toUUID('{package}'), {source_rank}, now64(3), 0
        FROM markorbit_facts.cn_stage_madrid
        WHERE package_id = toUUID('{package}')
        GROUP BY application_number, international_registration_number
    """


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


def main() -> None:
    client = _client()
    package = str(uuid.uuid4())
    source_rank = 987_654_321
    hash_a = "a" * 64
    hash_b = "b" * 64
    hash_c = "c" * 64
    hash_d = "d" * 64

    try:
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

        priority_ranges = plan_application_ranges(
            package,
            client=client,
            stage_table="cn_stage_priority",
            target_rows=2,
        )
        madrid_ranges = plan_application_ranges(
            package,
            client=client,
            stage_table="cn_stage_madrid",
            target_rows=2,
        )
        if priority_ranges != [(None, "P200"), ("P200", None)]:
            raise AssertionError(f"unexpected priority ranges: {priority_ranges}")
        if madrid_ranges != [(None, "G200"), ("G200", None)]:
            raise AssertionError(f"unexpected Madrid ranges: {madrid_ranges}")

        bounded = LegacySnapshotPersistClient(
            client,
            package_uuid=package,
            agent_batches=[],
            priority_ranges=priority_ranges,
            madrid_ranges=madrid_ranges,
        )
        bounded.command(_priority_insert(package, source_rank))
        bounded.command(_madrid_insert(package, source_rank))
        bounded.assert_aux_persist_complete()

        if bounded.physical_priority_commands != 2:
            raise AssertionError(
                "expected two priority commands, got "
                f"{bounded.physical_priority_commands}"
            )
        if bounded.physical_madrid_commands != 2:
            raise AssertionError(
                f"expected two Madrid commands, got {bounded.physical_madrid_commands}"
            )

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
            ("P100", "LATEST", "latest goods", 10, 20, source_rank),
            ("P200", "NORMAL", "goods 2", 30, 30, source_rank),
            ("P300", "NORMAL", "goods 3", 40, 40, source_rank),
        ]:
            raise AssertionError(f"unexpected priority current rows: {priority_rows}")

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
            ("G100", "ZH", "LATEST", date(2019, 1, 5), 10, 20, source_rank),
            ("G200", "EN", "NORMAL", date(2020, 2, 1), 30, 30, source_rank),
            ("G300", "FR", "NORMAL", date(2021, 3, 1), 40, 40, source_rank),
        ]:
            raise AssertionError(f"unexpected Madrid current rows: {madrid_rows}")

        print(
            "bounded auxiliary snapshot fixture passed: "
            f"priority_chunks={len(priority_ranges)} madrid_chunks={len(madrid_ranges)}"
        )
    finally:
        _cleanup(client, package)


if __name__ == "__main__":
    main()
