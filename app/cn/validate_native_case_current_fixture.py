from __future__ import annotations

import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import case_publish
from app.cn.native_case_current import NativeCaseCurrentCutoverClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a148")
SOURCE_RANK = 987_654_326
SOURCE_SHA = "148" + "f" * 61
PREFIX = "CC148-"


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondCaseInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_case_current" in sql:
            self._inserts += 1
            if self._inserts == 2:
                raise RuntimeError("fixture native case-current interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native CASE_CURRENT SQL leaked to compatibility delegate")

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
                VALUES (%s, 'CN', 'native-case-current-fixture.zip',
                        '/fixture/native-case-current.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-case-current',
                        DATE '2026-01-31', %s, 'REGISTERED')
                """,
                (str(PACKAGE_ID), SOURCE_SHA, SOURCE_RANK),
            )
        conn.commit()


def _cleanup(client: Any, package: str) -> None:
    client.command(
        "ALTER TABLE markorbit_facts.cn_stage_case_publish "
        f"DELETE WHERE package_id = toUUID('{package}')",
        settings={"mutations_sync": 1},
    )
    client.command(
        "ALTER TABLE markorbit_facts.cn_case_current "
        f"DELETE WHERE startsWith(application_number, '{PREFIX}')",
        settings={"mutations_sync": 1},
    )


def _seed_current(client: Any) -> None:
    old_package = "00000000-0000-0000-0000-00000000b148"
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_case_current
        (
            case_id, application_number, case_family_root, suffix_path,
            filing_route, number_family, mark_name_raw, mark_name_norm,
            classes, source_package_kind, source_effective_date, source_file,
            source_first_line, source_last_line, source_row_hash,
            last_source_package_id, record_hash, source_rank, is_deleted
        ) VALUES
        (
            toUUID('00000000-0000-0000-0000-000000001481'),
            '{PREFIX}A100', '{PREFIX}A100', '', 'DIRECT_CN', 'OTHER',
            'Old A100', 'old a100', [9], 'MONTHLY_PATCH', toDate32('2025-12-31'),
            'old-a100.xml', 1, 1, '{'1' * 64}', toUUID('{old_package}'),
            '{'a' * 64}', {SOURCE_RANK - 1}, 0
        ),
        (
            toUUID('00000000-0000-0000-0000-000000001482'),
            '{PREFIX}A200', '{PREFIX}A200', '', 'DIRECT_CN', 'OTHER',
            'Old A200', 'old a200', [12], 'MONTHLY_PATCH', toDate32('2026-01-31'),
            'old-a200.xml', 2, 2, '{'2' * 64}', toUUID('{old_package}'),
            '{'b' * 64}', {SOURCE_RANK}, 0
        ),
        (
            toUUID('00000000-0000-0000-0000-000000001483'),
            '{PREFIX}A300', '{PREFIX}A300', '', 'DIRECT_CN', 'OTHER',
            'Newer A300', 'newer a300', [25], 'MONTHLY_PATCH', toDate32('2026-02-28'),
            'old-a300.xml', 3, 3, '{'3' * 64}', toUUID('{old_package}'),
            '{'c' * 64}', {SOURCE_RANK + 1}, 0
        )
        """
    )


def _seed_stage(client: Any, package: str) -> None:
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_stage_case_publish
        (
            package_id, case_id, family_root_case_id, application_number,
            case_family_root, suffix_path, filing_route, number_family,
            international_registration_number, is_derived_case, relation_id,
            mark_name_raw, mark_type_raw, mark_form_raw, agent_code, classes,
            data_quality_flags, source_file, source_first_line, source_last_line,
            source_row_hash, record_hash
        ) VALUES
        (
            toUUID('{package}'), toUUID('00000000-0000-0000-0000-000000002481'),
            toUUID('00000000-0000-0000-0000-000000002481'), '{PREFIX}A100',
            '{PREFIX}A100', '', 'DIRECT_CN', 'OTHER', '', 0,
            toUUID('00000000-0000-0000-0000-000000000000'),
            'New A100', 'WORD', 'STANDARD', 'AG100', [9], ['FIXTURE'],
            'new-a100.xml', 10, 12, '{'d' * 64}', '{'A' * 64}'
        ),
        (
            toUUID('{package}'), toUUID('00000000-0000-0000-0000-000000002482'),
            toUUID('00000000-0000-0000-0000-000000002482'), '{PREFIX}A200',
            '{PREFIX}A200', '', 'DIRECT_CN', 'OTHER', '', 0,
            toUUID('00000000-0000-0000-0000-000000000000'),
            'New A200', 'WORD', 'STANDARD', 'AG200', [12], ['FIXTURE'],
            'new-a200.xml', 20, 21, '{'e' * 64}', '{'B' * 64}'
        ),
        (
            toUUID('{package}'), toUUID('00000000-0000-0000-0000-000000002483'),
            toUUID('00000000-0000-0000-0000-000000002483'), '{PREFIX}A300',
            '{PREFIX}A300', '', 'DIRECT_CN', 'OTHER', '', 0,
            toUUID('00000000-0000-0000-0000-000000000000'),
            'Incoming A300', 'WORD', 'STANDARD', 'AG300', [25], ['FIXTURE'],
            'new-a300.xml', 30, 31, '{'f' * 64}', '{'C' * 64}'
        )
        """
    )


def _placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_current
        SELECT incoming.case_id
        FROM markorbit_facts.cn_stage_case_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    case_publish.ensure_case_publish_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _seed_current(client)
        _seed_stage(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first = NativeCaseCurrentCutoverClient(
            first_delegate,
            execution_client=_FailSecondCaseInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        try:
            first.command(_placeholder(package))
        except RuntimeError as exc:
            if "fixture native case-current interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native CASE_CURRENT interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_current
            WHERE last_source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed CASE_CURRENT row before failure, got {partial}")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeCaseCurrentCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed.native_case_current_enabled:
            raise AssertionError("persisted CASE_CURRENT marker was not reused")
        result = resumed.command(_placeholder(package))
        resumed.assert_final_publish_complete()
        if result.range_count != 3 or result.skipped != 1 or result.executed != 2:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("CASE_CURRENT compatibility boundary was not preserved")

        inserted = client.query(
            f"""
            SELECT application_number, mark_name_raw, mark_name_norm, classes,
                   source_package_kind, toString(source_effective_date), source_file,
                   source_first_line, source_last_line, source_row_hash,
                   record_hash, source_rank
            FROM markorbit_facts.cn_case_current
            WHERE last_source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        expected = [
            (
                f"{PREFIX}A100", "New A100", "new a100", [9], "MONTHLY_PATCH",
                "2026-01-31", "new-a100.xml", 10, 12, b"d" * 64,
                b"A" * 64, SOURCE_RANK,
            ),
            (
                f"{PREFIX}A200", "New A200", "new a200", [12], "MONTHLY_PATCH",
                "2026-01-31", "new-a200.xml", 20, 21, b"e" * 64,
                b"B" * 64, SOURCE_RANK,
            ),
        ]
        if inserted != expected:
            raise AssertionError(f"unexpected native CASE_CURRENT package rows: {inserted}")

        blocked = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_current
            WHERE last_source_package_id = toUUID('{package}')
              AND application_number = '{PREFIX}A300'
            """
        ).result_rows[0][0]
        if int(blocked) != 0:
            raise AssertionError("newer CASE_CURRENT row was incorrectly overwritten")

        final_a100 = client.query(
            f"""
            SELECT mark_name_raw, source_rank
            FROM markorbit_facts.cn_case_current FINAL
            WHERE application_number = '{PREFIX}A100'
            """
        ).result_rows
        if final_a100 != [("New A100", SOURCE_RANK)]:
            raise AssertionError(f"older CASE_CURRENT row was not replaced: {final_a100}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native CASE_CURRENT fixture passed: "
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
