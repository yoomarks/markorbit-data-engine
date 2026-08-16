from __future__ import annotations

import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import party_publish
from app.cn.native_case_party_current import NativeCasePartyCutoverClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a149")
SOURCE_RANK = 987_654_327
SOURCE_SHA = "149" + "a" * 61
PREFIX = "CP149-"


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondPartyInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_case_party_current" in sql:
            self._inserts += 1
            if self._inserts == 2:
                raise RuntimeError("fixture native case-party interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native CASE_PARTY_CURRENT SQL leaked to compatibility delegate")

    def assert_final_publish_complete(self) -> dict[str, int]:
        self.final_assertions += 1
        return {"SUCCESS": 1, "RUNNING": 0, "FAILED": 0}


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
                    source_period_end, source_rank, status
                )
                VALUES (%s, 'CN', 'native-case-party-current-fixture.zip',
                        '/fixture/native-case-party-current.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-case-party-current',
                        DATE '2026-01-31', %s, 'REGISTERED')
                """,
                (str(PACKAGE_ID), SOURCE_SHA, SOURCE_RANK),
            )
        conn.commit()


def _cleanup(client: Any, package: str) -> None:
    client.command(
        "ALTER TABLE markorbit_facts.cn_stage_party_publish "
        f"DELETE WHERE package_id = toUUID('{package}')",
        settings={"mutations_sync": 1},
    )
    client.command(
        "ALTER TABLE markorbit_facts.cn_case_party_current "
        f"DELETE WHERE startsWith(application_number, '{PREFIX}')",
        settings={"mutations_sync": 1},
    )
    client.command(
        "ALTER TABLE markorbit_facts.cn_case_current "
        f"DELETE WHERE startsWith(application_number, '{PREFIX}')",
        settings={"mutations_sync": 1},
    )


def _seed_case_current(client: Any) -> None:
    source_package = "00000000-0000-0000-0000-00000000b149"
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_case_current
        (
            case_id, application_number, case_family_root, suffix_path,
            filing_route, number_family, filing_date, classes,
            source_package_kind, source_file, source_first_line, source_last_line,
            source_row_hash, last_source_package_id, record_hash, source_rank,
            is_deleted
        ) VALUES
        (
            toUUID('00000000-0000-0000-0000-000000001491'),
            '{PREFIX}A100', '{PREFIX}A100', '', 'DIRECT_CN', 'OTHER',
            toDate32('2025-01-10'), [9], 'MONTHLY_PATCH', 'case-a100.xml',
            1, 1, '{'1' * 64}', toUUID('{source_package}'), '{'A' * 64}',
            {SOURCE_RANK}, 0
        ),
        (
            toUUID('00000000-0000-0000-0000-000000001492'),
            '{PREFIX}A200', '{PREFIX}A200', '', 'DIRECT_CN', 'OTHER',
            toDate32('2025-02-20'), [], 'MONTHLY_PATCH', 'case-a200.xml',
            2, 2, '{'2' * 64}', toUUID('{source_package}'), '{'B' * 64}',
            {SOURCE_RANK}, 0
        ),
        (
            toUUID('00000000-0000-0000-0000-000000001493'),
            '{PREFIX}A300', '{PREFIX}A300', '', 'DIRECT_CN', 'OTHER',
            toDate32('2025-03-30'), [25], 'MONTHLY_PATCH', 'case-a300.xml',
            3, 3, '{'3' * 64}', toUUID('{source_package}'), '{'C' * 64}',
            {SOURCE_RANK}, 0
        )
        """
    )


def _seed_party_current(client: Any) -> None:
    old_package = "00000000-0000-0000-0000-00000000c149"
    rows = (
        (
            "00000000-0000-0000-0000-000000004911",
            "00000000-0000-0000-0000-000000001491",
            f"{PREFIX}A100",
            "OWNER",
            "1" * 64,
            "00000000-0000-0000-0000-000000005911",
            "Old Owner",
            [9],
            SOURCE_RANK - 1,
            "a",
        ),
        (
            "00000000-0000-0000-0000-000000004912",
            "00000000-0000-0000-0000-000000001492",
            f"{PREFIX}A200",
            "AGENT",
            "2" * 64,
            "00000000-0000-0000-0000-000000005912",
            "Old Agent",
            [12],
            SOURCE_RANK,
            "b",
        ),
        (
            "00000000-0000-0000-0000-000000004913",
            "00000000-0000-0000-0000-000000001493",
            f"{PREFIX}A300",
            "APPLICANT",
            "3" * 64,
            "00000000-0000-0000-0000-000000005913",
            "Newer Applicant",
            [25],
            SOURCE_RANK + 1,
            "c",
        ),
    )
    values = []
    for relation_id, case_id, application, role, key, mention_id, name, classes, rank, hash_char in rows:
        classes_sql = "[" + ",".join(str(value) for value in classes) + "]"
        values.append(
            f"""
            (
                toUUID('{relation_id}'), toUUID('{case_id}'), '{application}',
                '{role}', '{key}', toUUID('{mention_id}'),
                CAST(NULL, 'Nullable(UUID)'), '', '{name}', lowerUTF8('{name}'),
                '', '', '', '', '', {classes_sql}, 0.90,
                toDate32('2025-01-01'), CAST(NULL, 'Nullable(Date32)'), 1,
                'OBSERVED_CURRENT', 'CASE_ROLE_REPLACE', 'MONTHLY_PATCH',
                toDate32('2025-12-31'), 'old-{application}.xml', 1, 1,
                '{hash_char * 64}', toUUID('{old_package}'), '{hash_char.upper() * 64}',
                {rank}, 0
            )
            """
        )
    client.command(
        """
        INSERT INTO markorbit_facts.cn_case_party_current
        (
            relation_id, case_id, application_number, role, relation_key,
            mention_id, entity_id, agent_code, raw_name, normalized_name,
            raw_address, normalized_address, country_code, region_code, city,
            class_nos, confidence_score, valid_from, valid_to, is_current,
            relation_status, replacement_mode, source_package_kind,
            source_effective_date, source_file, source_first_line,
            source_last_line, source_row_hash, last_source_package_id,
            record_hash, source_rank, is_deleted
        ) VALUES
        """
        + ",\n".join(values)
    )


def _seed_stage(client: Any, package: str) -> None:
    rows = (
        (
            "00000000-0000-0000-0000-000000006911",
            "00000000-0000-0000-0000-000000001491",
            f"{PREFIX}A100",
            "OWNER",
            "1" * 64,
            "00000000-0000-0000-0000-000000007911",
            "New Owner",
            [99],
            "d",
        ),
        (
            "00000000-0000-0000-0000-000000006912",
            "00000000-0000-0000-0000-000000001492",
            f"{PREFIX}A200",
            "AGENT",
            "2" * 64,
            "00000000-0000-0000-0000-000000007912",
            "New Agent",
            [12],
            "e",
        ),
        (
            "00000000-0000-0000-0000-000000006913",
            "00000000-0000-0000-0000-000000001493",
            f"{PREFIX}A300",
            "APPLICANT",
            "3" * 64,
            "00000000-0000-0000-0000-000000007913",
            "Incoming Applicant",
            [25],
            "f",
        ),
    )
    values = []
    for relation_id, case_id, application, role, key, mention_id, name, classes, hash_char in rows:
        classes_sql = "[" + ",".join(str(value) for value in classes) + "]"
        agent_code = "AG200" if role == "AGENT" else ""
        values.append(
            f"""
            (
                toUUID('{package}'), toUUID('{relation_id}'), toUUID('{case_id}'),
                '{application}', '{role}', '{key}', toUUID('{mention_id}'),
                CAST(NULL, 'Nullable(UUID)'), '{agent_code}', '{name}',
                lowerUTF8('{name}'), 'Address {application}', 'address {application}',
                'CN', 'BJ', 'Beijing', {classes_sql}, 0.95,
                'party-{application}.xml', 10, 12, '{hash_char * 64}',
                '{hash_char.upper() * 64}'
            )
            """
        )
    client.command(
        """
        INSERT INTO markorbit_facts.cn_stage_party_publish
        (
            package_id, relation_id, case_id, application_number, role,
            relation_key, mention_id, entity_id, agent_code, raw_name,
            normalized_name, raw_address, normalized_address, country_code,
            region_code, city, class_nos, confidence_score, source_file,
            source_first_line, source_last_line, source_row_hash, record_hash
        ) VALUES
        """
        + ",\n".join(values)
    )


def _placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_party_current
        SELECT incoming.relation_id, 'OBSERVED_CURRENT'
        FROM markorbit_facts.cn_stage_party_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    party_publish.ensure_party_publish_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _seed_case_current(client)
        _seed_party_current(client)
        _seed_stage(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first = NativeCasePartyCutoverClient(
            first_delegate,
            execution_client=_FailSecondPartyInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        try:
            first.command(_placeholder(package))
        except RuntimeError as exc:
            if "fixture native case-party interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native CASE_PARTY_CURRENT interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_party_current
            WHERE last_source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed party row before failure, got {partial}")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeCasePartyCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed.native_case_party_enabled:
            raise AssertionError("persisted CASE_PARTY_CURRENT marker was not reused")
        result = resumed.command(_placeholder(package))
        resumed.assert_final_publish_complete()
        if result.range_count != 3 or result.skipped != 1 or result.executed != 2:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("CASE_PARTY_CURRENT compatibility boundary was not preserved")

        inserted = client.query(
            f"""
            SELECT application_number, role, relation_key, raw_name, class_nos,
                   toString(valid_from), valid_to, is_current,
                   relation_status, replacement_mode, source_package_kind,
                   toString(source_effective_date), source_file,
                   source_first_line, source_last_line, source_row_hash,
                   record_hash, source_rank
            FROM markorbit_facts.cn_case_party_current
            WHERE last_source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        expected = [
            (
                f"{PREFIX}A100", "OWNER", b"1" * 64, "New Owner", [9],
                "2025-01-10", None, 1, "OBSERVED_CURRENT",
                "CASE_ROLE_REPLACE", "MONTHLY_PATCH", "2026-01-31",
                f"party-{PREFIX}A100.xml", 10, 12, b"d" * 64,
                b"D" * 64, SOURCE_RANK,
            ),
            (
                f"{PREFIX}A200", "AGENT", b"2" * 64, "New Agent", [12],
                "2026-01-31", None, 1, "OBSERVED_CURRENT",
                "CASE_ROLE_REPLACE", "MONTHLY_PATCH", "2026-01-31",
                f"party-{PREFIX}A200.xml", 10, 12, b"e" * 64,
                b"E" * 64, SOURCE_RANK,
            ),
        ]
        if inserted != expected:
            raise AssertionError(f"unexpected native CASE_PARTY_CURRENT rows: {inserted}")

        blocked = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_party_current
            WHERE last_source_package_id = toUUID('{package}')
              AND application_number = '{PREFIX}A300'
            """
        ).result_rows[0][0]
        if int(blocked) != 0:
            raise AssertionError("newer CASE_PARTY_CURRENT row was incorrectly overwritten")

        final_a100 = client.query(
            f"""
            SELECT raw_name, source_rank
            FROM markorbit_facts.cn_case_party_current FINAL
            WHERE application_number = '{PREFIX}A100'
              AND role = 'OWNER' AND relation_key = '{'1' * 64}'
            """
        ).result_rows
        if final_a100 != [("New Owner", SOURCE_RANK)]:
            raise AssertionError(f"older CASE_PARTY_CURRENT row was not replaced: {final_a100}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native CASE_PARTY_CURRENT fixture passed: "
            f"ranges={result.range_count} skipped={result.skipped} "
            f"executed={result.executed} package_rows={len(inserted)}"
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
