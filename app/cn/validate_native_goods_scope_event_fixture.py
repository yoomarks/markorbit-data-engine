from __future__ import annotations

from hashlib import sha256
import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import goods_lifecycle
from app.cn.native_goods_scope_event import (
    NativeGoodsScopeEventCutoverClient,
    goods_scope_event_sql,
)
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.cn.storage_v2_events import EventBaselineDeltaClient
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a157")
SOURCE_RANK = 987_654_335
SOURCE_SHA = "157" + "a" * 61
PREFIX = "GSE157-"


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondEventInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_observed_event" in sql:
            self._inserts += 1
            if self._inserts == 2:
                raise RuntimeError("fixture native goods-scope-event interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native GOODS_SCOPE_EVENT SQL leaked to compatibility delegate")

    def assert_final_publish_complete(self) -> dict[str, int]:
        self.final_assertions += 1
        return {"SUCCESS": 1, "RUNNING": 0, "FAILED": 0}


def _ensure_source_package() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM control.source_package WHERE package_id = %s", (str(PACKAGE_ID),))
            cur.execute(
                """
                INSERT INTO control.source_package
                (
                    package_id, jurisdiction, file_name, file_path, file_size,
                    sha256, package_kind, partition_dimension, partition_value,
                    source_period_end, source_rank, status
                )
                VALUES (%s, 'CN', 'native-goods-scope-event-fixture.zip',
                        '/fixture/native-goods-scope-event.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-goods-scope-event',
                        DATE '2026-07-31', %s, 'REGISTERED')
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
    client.command(
        "ALTER TABLE markorbit_facts.cn_observed_event "
        f"DELETE WHERE startsWith(application_number, '{PREFIX}')",
        settings={"mutations_sync": 1},
    )


def _seed_current(client: Any) -> None:
    old_package = "00000000-0000-0000-0000-00000000b157"
    rows = (
        ("A100", 9, SOURCE_RANK - 1, 3, 2, 1, 0, "o"),
        ("A200", 12, SOURCE_RANK - 1, 9, 9, 0, 0, "p"),
        ("A300", 25, SOURCE_RANK, 2, 2, 0, 0, "q"),
        ("A500", 35, SOURCE_RANK + 1, 2, 1, 1, 0, "r"),
    )
    values: list[str] = []
    for index, (suffix, class_no, rank, total, active, inactive, unknown, hash_char) in enumerate(
        rows, start=1
    ):
        values.append(
            f"""
            (
                toUUID('00000000-0000-0000-0000-0000000157{index:02d}'),
                '{PREFIX}{suffix}', {class_no}, {total}, {active}, {inactive}, {unknown},
                '{hash_char * 64}', 'MONTHLY_PATCH', 'old-{suffix}.xml', 1, 2,
                '{index}' || repeat('0', 63), toUUID('{old_package}'), {rank}, 0
            )
            """
        )
    client.command(
        """
        INSERT INTO markorbit_facts.cn_case_scope_current
        (
            case_id, application_number, class_no, source_item_count,
            interpreted_active_item_count, interpreted_inactive_item_count,
            unmapped_status_item_count, scope_hash, source_package_kind,
            source_file, source_first_line, source_last_line, source_row_hash,
            last_source_package_id, source_rank, is_deleted
        ) VALUES
        """ + ",\n".join(values)
    )


def _seed_stage(client: Any, package: str) -> None:
    rows = (
        ("A100", 9, 4, 3, 1, 0, "a", "V2", "b"),
        ("A200", 12, 10, 8, 2, 0, "p", "V2", "c"),
        ("A300", 25, 3, 2, 1, 0, "c", "V2", "d"),
        ("A400", 30, 1, 1, 0, 0, "d", "V2", "e"),
        ("A500", 35, 4, 4, 0, 0, "e", "V2", "f"),
    )
    values: list[str] = []
    for index, (suffix, class_no, total, active, inactive, unknown, scope_char, mapping, row_char) in enumerate(
        rows, start=1
    ):
        values.append(
            f"""
            (
                toUUID('{package}'),
                toUUID('00000000-0000-0000-0000-0000000257{index:02d}'),
                '{PREFIX}{suffix}', {class_no}, {total}, {active}, {inactive}, {unknown},
                {total}, 1, 'COMPLETE', '{mapping}', ['1'],
                'goods-{suffix}', 'goods {suffix}', ['{class_no:02d}01'], ['{class_no:02d}01'],
                '{scope_char * 64}', 'effective-{suffix}', 'stage-{suffix}.xml',
                {index * 10}, {index * 10 + 1}, '{row_char * 64}'
            )
            """
        )
    client.command(
        """
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
        """ + ",\n".join(values)
    )


def _legacy_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT
            if(cur.application_number = '', 'GOODS_SCOPE_OBSERVED',
               'GOODS_SCOPE_CHANGED_OBSERVED')
        FROM markorbit_facts.cn_stage_scope_publish AS incoming
        LEFT JOIN markorbit_facts.cn_case_scope_current AS cur FINAL
          ON cur.application_number = incoming.application_number
         AND cur.class_no = incoming.class_no
        WHERE (cur.application_number = '' OR cur.source_rank < {SOURCE_RANK})
          AND (cur.application_number = '' OR cur.scope_hash != incoming.scope_hash)
          AND incoming.package_id = toUUID('{package}')
    """


def _derived_placeholder() -> str:
    return (
        "INSERT INTO markorbit_facts.cn_observed_event "
        "SELECT 'DERIVED_CASE_OBSERVED' FROM markorbit_facts.cn_stage_case_publish"
    )


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
        first_native = NativeGoodsScopeEventCutoverClient(
            first_delegate,
            execution_client=_FailSecondEventInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        first = EventBaselineDeltaClient(first_native)
        try:
            first.command(_legacy_placeholder(package))
        except RuntimeError as exc:
            if "fixture native goods-scope-event interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native GOODS_SCOPE_EVENT interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed goods-scope event before failure, got {partial}")

        resumed_delegate = _CompatibilityDelegate()
        resumed_native = NativeGoodsScopeEventCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed_native.native_goods_scope_event_enabled:
            raise AssertionError("persisted GOODS_SCOPE_EVENT marker was not reused")
        resumed = EventBaselineDeltaClient(resumed_native)
        result = resumed.command(_legacy_placeholder(package))
        resumed.command(_derived_placeholder())
        resumed.assert_rewrite_counts()
        resumed_native.assert_final_publish_complete()
        if result.range_count != 5 or result.skipped != 1 or result.executed != 4:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("GOODS_SCOPE_EVENT compatibility boundary was not preserved")

        events = client.query(
            f"""
            SELECT application_number, event_type, ifNull(toString(event_date), ''),
                   affected_scope, class_no, field_name, old_value_compact,
                   new_value_compact, evidence_level, legal_effect, confidence_score,
                   source_package_kind, source_file, source_first_line,
                   source_last_line, source_row_hash, source_rank, event_hash
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE source_package_id = toUUID('{package}')
            ORDER BY application_number, class_no
            """
        ).result_rows
        old_hash = "o" * 64
        new_hash = "a" * 64
        event_hash = sha256(
            f"{PREFIX}A100|GOODS|9|{old_hash}|{new_hash}|{SOURCE_RANK}".encode("utf-8")
        ).hexdigest().upper().encode("ascii")
        old_value = (
            '{"source_item_count":"3","active":"2","inactive":"1",'
            '"unknown":"0","scope_hash":"' + old_hash + '"}'
        )
        new_value = (
            '{"source_item_count":"4","active":"3","inactive":"1",'
            '"unknown":"0","scope_hash":"' + new_hash + '","mapping_version":"V2"}'
        )
        expected = [(
            f"{PREFIX}A100", "GOODS_SCOPE_CHANGED_OBSERVED", "", "GOODS", 9,
            "goods_scope", old_value, new_value, "OFFICIAL_FACT_OBSERVATION",
            "NOT_DETERMINED", 1.0, "MONTHLY_PATCH", "stage-A100.xml", 10, 11,
            b"b" * 64, SOURCE_RANK, event_hash,
        )]
        if events != expected:
            raise AssertionError(f"unexpected native GOODS_SCOPE_EVENT rows: {events}")

        client.command(
            goods_scope_event_sql(
                PACKAGE_ID,
                package_kind="MONTHLY_PATCH",
                source_rank=SOURCE_RANK,
                lower=None,
                upper=f"{PREFIX}A200",
            )
        )
        converged = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE source_package_id = toUUID('{package}')
              AND application_number = '{PREFIX}A100'
              AND class_no = 9
            """
        ).result_rows[0][0]
        if int(converged) != 1:
            raise AssertionError(f"goods-scope event_hash replay did not converge: {converged}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native GOODS_SCOPE_EVENT fixture passed: "
            f"ranges={result.range_count} skipped={result.skipped} "
            f"executed={result.executed} semantic_events={len(events)}"
        )
    finally:
        try:
            clear_publish_checkpoint(PACKAGE_ID)
        finally:
            _cleanup(client, package)
            with postgres_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM control.source_package WHERE package_id = %s", (str(PACKAGE_ID),))
                conn.commit()


if __name__ == "__main__":
    main()
