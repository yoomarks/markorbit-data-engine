from __future__ import annotations

import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import goods_lifecycle
from app.cn.native_case_scope_current import NativeCaseScopeCutoverClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a147")
SOURCE_RANK = 987_654_325
SOURCE_SHA = "147" + "e" * 61
PREFIX = "CS147-"


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondCaseScopeInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_case_scope_current" in sql:
            self._inserts += 1
            if self._inserts == 2:
                raise RuntimeError("fixture native case-scope interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native case-scope SQL leaked to compatibility delegate")

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
                VALUES (%s, 'CN', 'native-case-scope-fixture.zip',
                        '/fixture/native-case-scope.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-case-scope',
                        DATE '2026-01-31', %s, 'REGISTERED')
                """,
                (str(PACKAGE_ID), SOURCE_SHA, SOURCE_RANK),
            )
        conn.commit()


def _cleanup(client: Any, package: str) -> None:
    client.command(
        "ALTER TABLE markorbit_facts.cn_stage_scope_publish "
        f"DELETE WHERE package_id = toUUID('{package}')",
        settings={"mutations_sync": 1},
    )
    client.command(
        "ALTER TABLE markorbit_facts.cn_case_scope_current "
        f"DELETE WHERE startsWith(application_number, '{PREFIX}')",
        settings={"mutations_sync": 1},
    )


def _seed_current(client: Any) -> None:
    old_package = "00000000-0000-0000-0000-00000000b147"
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        (
            case_id, application_number, class_no, scope_hash,
            source_package_kind, source_effective_date, source_file,
            source_first_line, source_last_line, source_row_hash,
            last_source_package_id, source_rank, is_deleted
        ) VALUES
        (
            toUUID('00000000-0000-0000-0000-000000001471'),
            '{PREFIX}A100', 9, '{'o' * 64}', 'MONTHLY_PATCH', toDate32('2025-12-31'),
            'old-a100.xml', 1, 1, '{'1' * 64}', toUUID('{old_package}'),
            {SOURCE_RANK - 1}, 0
        ),
        (
            toUUID('00000000-0000-0000-0000-000000001472'),
            '{PREFIX}A200', 12, '{'p' * 64}', 'MONTHLY_PATCH', toDate32('2026-01-31'),
            'old-a200.xml', 2, 2, '{'2' * 64}', toUUID('{old_package}'),
            {SOURCE_RANK}, 0
        ),
        (
            toUUID('00000000-0000-0000-0000-000000001473'),
            '{PREFIX}A300', 25, '{'q' * 64}', 'MONTHLY_PATCH', toDate32('2026-02-28'),
            'old-a300.xml', 3, 3, '{'3' * 64}', toUUID('{old_package}'),
            {SOURCE_RANK + 1}, 0
        )
        """
    )


def _seed_stage(client: Any, package: str) -> None:
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_stage_scope_publish
        (
            package_id, case_id, application_number, class_no,
            source_item_count, interpreted_active_item_count,
            interpreted_inactive_item_count, unmapped_status_item_count,
            effective_item_count, interpretation_complete,
            scope_interpretation_status, goods_status_mapping_version,
            observed_status_codes, goods_items_compact, goods_text_search,
            similar_groups, active_similar_groups, scope_hash, effective_scope_hash,
            source_file, source_first_line, source_last_line, source_row_hash
        ) VALUES
        (
            toUUID('{package}'), toUUID('00000000-0000-0000-0000-000000002471'),
            '{PREFIX}A100', 9, 2, 2, 0, 0, 2, 1, 'COMPLETE', 'V1', ['1'],
            'goods-a100', 'goods a100', ['0901'], ['0901'], '{'a' * 64}',
            'effective-a100', 'new-a100.xml', 10, 12, '{'a' * 64}'
        ),
        (
            toUUID('{package}'), toUUID('00000000-0000-0000-0000-000000002472'),
            '{PREFIX}A200', 12, 1, 1, 0, 0, 1, 1, 'COMPLETE', 'V1', ['1'],
            'goods-a200', 'goods a200', ['1201'], ['1201'], '{'b' * 64}',
            'effective-a200', 'new-a200.xml', 20, 21, '{'b' * 64}'
        ),
        (
            toUUID('{package}'), toUUID('00000000-0000-0000-0000-000000002473'),
            '{PREFIX}A300', 25, 1, 1, 0, 0, 1, 1, 'COMPLETE', 'V1', ['1'],
            'goods-a300', 'goods a300', ['2501'], ['2501'], '{'c' * 64}',
            'effective-a300', 'new-a300.xml', 30, 31, '{'c' * 64}'
        )
        """
    )


def _placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        SELECT incoming.case_id
        FROM markorbit_facts.cn_stage_scope_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    goods_lifecycle.ensure_m16_goods_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _seed_current(client)
        _seed_stage(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first = NativeCaseScopeCutoverClient(
            first_delegate,
            execution_client=_FailSecondCaseScopeInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        try:
            first.command(_placeholder(package))
        except RuntimeError as exc:
            if "fixture native case-scope interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native case-scope interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_scope_current
            WHERE last_source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed case-scope row before failure, got {partial}")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeCaseScopeCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed.native_case_scope_enabled:
            raise AssertionError("persisted case-scope marker was not reused")
        result = resumed.command(_placeholder(package))
        resumed.assert_final_publish_complete()
        if result.range_count != 3 or result.skipped != 1 or result.executed != 2:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("case-scope compatibility boundary was not preserved")

        inserted = client.query(
            f"""
            SELECT application_number, class_no, scope_hash, source_package_kind,
                   toString(source_effective_date), source_file,
                   source_first_line, source_last_line, source_row_hash, source_rank
            FROM markorbit_facts.cn_case_scope_current
            WHERE last_source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        expected = [
            (
                f"{PREFIX}A100", 9, b"a" * 64, "MONTHLY_PATCH", "2026-01-31",
                "new-a100.xml", 10, 12, b"a" * 64, SOURCE_RANK,
            ),
            (
                f"{PREFIX}A200", 12, b"b" * 64, "MONTHLY_PATCH", "2026-01-31",
                "new-a200.xml", 20, 21, b"b" * 64, SOURCE_RANK,
            ),
        ]
        if inserted != expected:
            raise AssertionError(f"unexpected native case-scope package rows: {inserted}")

        newer_blocked = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_scope_current
            WHERE last_source_package_id = toUUID('{package}')
              AND application_number = '{PREFIX}A300'
            """
        ).result_rows[0][0]
        if int(newer_blocked) != 0:
            raise AssertionError("newer Current row was incorrectly overwritten")

        final_a100 = client.query(
            f"""
            SELECT scope_hash, source_rank
            FROM markorbit_facts.cn_case_scope_current FINAL
            WHERE application_number = '{PREFIX}A100' AND class_no = 9
            """
        ).result_rows
        if final_a100 != [(b"a" * 64, SOURCE_RANK)]:
            raise AssertionError(f"older Current row was not replaced: {final_a100}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native case-scope fixture passed: "
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
