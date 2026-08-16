from __future__ import annotations

import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn.legacy_snapshot_persist import plan_agent_code_batches
from app.cn.native_agent_snapshot import NativeAgentCutoverClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a144")
SOURCE_RANK = 987_654_322
SOURCE_SHA = "144" + "b" * 61


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondAgentInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._agent_inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_agent_current" in sql:
            self._agent_inserts += 1
            if self._agent_inserts == 2:
                raise RuntimeError("fixture native Agent interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    """Fixture guard: native Agent placeholder must never reach this delegate."""

    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.aux_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native Agent placeholder leaked into compatibility delegate")

    def assert_aux_persist_complete(self) -> None:
        self.aux_assertions += 1

    def assert_agent_persist_complete(self) -> None:
        raise AssertionError("native Agent fixture unexpectedly used legacy Agent assertion")


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
                VALUES (%s, 'CN', 'native-agent-fixture.zip', '/fixture/native-agent.zip', 0,
                        %s, 'MONTHLY_PATCH', 'MONTH', 'native-agent', %s, 'REGISTERED')
                """,
                (str(PACKAGE_ID), SOURCE_SHA, SOURCE_RANK),
            )
        conn.commit()


def _cleanup(client: Any, package: str) -> None:
    for table, column in (
        ("cn_stage_basic", "package_id"),
        ("cn_stage_agent", "package_id"),
        ("cn_agent_current", "last_source_package_id"),
    ):
        client.command(
            f"ALTER TABLE markorbit_facts.{table} "
            f"DELETE WHERE {column} = toUUID('{package}')",
            settings={"mutations_sync": 1},
        )


def _stage_fixture_rows(client: Any, package: str) -> None:
    zero = "00000000-0000-0000-0000-000000000000"
    mention_1 = "00000000-0000-0000-0000-000000001001"
    mention_2 = "00000000-0000-0000-0000-000000001002"
    mention_3 = "00000000-0000-0000-0000-000000001003"
    entity_1 = "00000000-0000-0000-0000-000000002001"
    entity_2 = "00000000-0000-0000-0000-000000002002"
    entity_3 = "00000000-0000-0000-0000-000000002003"
    hash_a = "a" * 64
    hash_b = "b" * 64
    hash_c = "c" * 64
    hash_d = "d" * 64

    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_stage_basic
        (
            package_id, case_id, family_root_case_id, application_number,
            agent_code, agent_mention_id, agent_entity_id, class_no,
            source_file, source_start_line, source_end_line, row_hash
        ) VALUES
        (toUUID('{package}'), toUUID('{zero}'), toUUID('{zero}'), 'APP-A1',
         'A001', toUUID('{mention_1}'), toUUID('{entity_1}'), 1,
         'basic-a1.xml', 10, 10, '{hash_a}'),
        (toUUID('{package}'), toUUID('{zero}'), toUUID('{zero}'), 'APP-A2',
         'A001', toUUID('{mention_1}'), toUUID('{entity_1}'), 2,
         'basic-a2.xml', 20, 20, '{hash_b}'),
        (toUUID('{package}'), toUUID('{zero}'), toUUID('{zero}'), 'APP-B1',
         'A002', toUUID('{mention_2}'), toUUID('{entity_2}'), 3,
         'basic-b1.xml', 30, 30, '{hash_c}'),
        (toUUID('{package}'), toUUID('{zero}'), toUUID('{zero}'), 'APP-C1',
         'A003', toUUID('{mention_3}'), toUUID('{entity_3}'), 4,
         'basic-c1.xml', 40, 40, '{hash_d}')
        """
    )
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_stage_agent
        (
            package_id, relation_id, mention_id, entity_id, agent_code,
            agent_name, agent_name_norm, source_file,
            source_start_line, source_end_line, row_hash
        ) VALUES
        (toUUID('{package}'), toUUID('{zero}'), toUUID('{mention_1}'), toUUID('{entity_1}'),
         'A001', 'Agent One Early', 'agent one early', 'agent-a1.xml', 100, 100, '{hash_a}'),
        (toUUID('{package}'), toUUID('{zero}'), toUUID('{mention_1}'), toUUID('{entity_1}'),
         'A001', 'Agent One Latest', 'agent one latest', 'agent-a2.xml', 200, 200, '{hash_b}'),
        (toUUID('{package}'), toUUID('{zero}'), toUUID('{mention_3}'), toUUID('{entity_3}'),
         'A003', 'Agent Three', 'agent three', 'agent-c1.xml', 300, 300, '{hash_d}')
        """
    )


def _agent_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_agent_current
        SELECT b.agent_code
        FROM markorbit_facts.cn_stage_basic AS b
        LEFT JOIN markorbit_facts.cn_stage_agent AS a
          ON a.package_id = b.package_id AND a.agent_code = b.agent_code
        WHERE b.package_id = toUUID('{package}')
    """


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _stage_fixture_rows(client, package)

        batches = plan_agent_code_batches(
            PACKAGE_ID,
            client=client,
            target_rows=3,
            max_codes=2,
        )
        if batches != [("A001", "A002"), ("A003",)]:
            raise AssertionError(f"unexpected Agent batch plan: {batches}")

        store = PublishSubtaskStore(PACKAGE_ID)
        first_delegate = _CompatibilityDelegate()
        first = NativeAgentCutoverClient(
            first_delegate,
            execution_client=_FailSecondAgentInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            agent_batches=batches,
            subtask_store=store,
            allow_new_cutover=True,
        )
        try:
            first.command(_agent_placeholder(package))
        except RuntimeError as exc:
            if "fixture native Agent interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native Agent interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_agent_current FINAL
            WHERE last_source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 2:
            raise AssertionError(f"expected first Agent batch only, got {partial} rows")
        if first_delegate.commands:
            raise AssertionError("legacy compatibility delegate received native Agent SQL")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeAgentCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            agent_batches=batches,
            subtask_store=store,
            allow_new_cutover=False,
        )
        if not resumed.native_agent_enabled:
            raise AssertionError("persisted Agent cutover marker was not reused on resume")
        result = resumed.command(_agent_placeholder(package))
        resumed.assert_agent_persist_complete()
        if result.batch_count != 2 or result.skipped != 1 or result.executed != 1:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.aux_assertions != 1:
            raise AssertionError("native Agent compatibility boundary was not preserved")

        rows = client.query(
            f"""
            SELECT agent_code, agent_name, agent_name_norm, source_file,
                   source_first_line, source_last_line, source_rank
            FROM markorbit_facts.cn_agent_current FINAL
            WHERE last_source_package_id = toUUID('{package}')
            ORDER BY agent_code
            """
        ).result_rows
        expected = [
            ("A001", "Agent One Latest", "agent one latest", "basic-a1.xml", 10, 20, SOURCE_RANK),
            ("A002", "A002", "a002", "basic-b1.xml", 30, 30, SOURCE_RANK),
            ("A003", "Agent Three", "agent three", "basic-c1.xml", 40, 40, SOURCE_RANK),
        ]
        if rows != expected:
            raise AssertionError(f"unexpected native Agent Current rows: {rows}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native Agent snapshot fixture passed: "
            f"batches={result.batch_count} skipped={result.skipped} "
            f"executed={result.executed} rows={len(rows)}"
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
