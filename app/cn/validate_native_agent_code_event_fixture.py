from __future__ import annotations

from hashlib import sha256
import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import case_publish
from app.cn.native_agent_code_event import (
    NativeAgentCodeEventCutoverClient,
    agent_code_event_sql,
)
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a156")
SOURCE_RANK = 987_654_334
SOURCE_SHA = "156" + "a" * 61
PREFIX = "ACE156-"


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
                raise RuntimeError("fixture native agent-code-event interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native AGENT_CODE_EVENT SQL leaked to compatibility delegate")

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
                VALUES (%s, 'CN', 'native-agent-code-event-fixture.zip',
                        '/fixture/native-agent-code-event.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-agent-code-event',
                        DATE '2026-07-31', %s, 'REGISTERED')
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
    client.command(
        "ALTER TABLE markorbit_facts.cn_observed_event "
        f"DELETE WHERE startsWith(application_number, '{PREFIX}')",
        settings={"mutations_sync": 1},
    )


def _seed_current(client: Any) -> None:
    old_package = "00000000-0000-0000-0000-00000000b156"
    rows = (
        ("A100", SOURCE_RANK - 1, "AGENT-OLD-A", "A"),
        ("A200", SOURCE_RANK - 1, "AGENT-SAME-B", "C"),
        ("A300", SOURCE_RANK, "AGENT-OLD-C", "D"),
        ("A500", SOURCE_RANK + 1, "AGENT-OLD-E", "G"),
    )
    values: list[str] = []
    for index, (suffix, rank, agent_code, hash_char) in enumerate(rows, start=1):
        values.append(
            f"""
            (
                toUUID('00000000-0000-0000-0000-0000000156{index:02d}'),
                '{PREFIX}{suffix}', '{PREFIX}{suffix}', '', 'DIRECT_CN', 'OTHER',
                'Mark {suffix}', '{agent_code}', toDate32('2025-01-{index:02d}'),
                'MONTHLY_PATCH', 'old-{suffix}.xml', 1, 2, '{hash_char.lower() * 64}',
                toUUID('{old_package}'), '{hash_char * 64}', {rank}, 0
            )
            """
        )
    client.command(
        """
        INSERT INTO markorbit_facts.cn_case_current
        (
            case_id, application_number, case_family_root, suffix_path,
            filing_route, number_family, mark_name_raw, agent_code, filing_date,
            source_package_kind, source_file, source_first_line, source_last_line,
            source_row_hash, last_source_package_id, record_hash, source_rank, is_deleted
        ) VALUES
        """ + ",\n".join(values)
    )


def _seed_stage(client: Any, package: str) -> None:
    rows = (
        ("A100", "AGENT-NEW-A", "B"),
        ("A200", "AGENT-SAME-B", "C"),
        ("A300", "AGENT-NEW-C", "E"),
        ("A400", "AGENT-NEW-D", "F"),
        ("A500", "AGENT-NEW-E", "H"),
    )
    values: list[str] = []
    for index, (suffix, agent_code, hash_char) in enumerate(rows, start=1):
        values.append(
            f"""
            (
                toUUID('{package}'),
                toUUID('00000000-0000-0000-0000-0000000256{index:02d}'),
                toUUID('00000000-0000-0000-0000-0000000356{index:02d}'),
                '{PREFIX}{suffix}', '{PREFIX}{suffix}', '', 'DIRECT_CN', 'OTHER',
                0, toUUID('00000000-0000-0000-0000-0000000456{index:02d}'),
                'Mark {suffix}', '{agent_code}', toDate32('2026-07-{index:02d}'), [9],
                'stage-{suffix}.xml', {index * 10}, {index * 10 + 1},
                '{hash_char.lower() * 64}', '{hash_char * 64}'
            )
            """
        )
    client.command(
        """
        INSERT INTO markorbit_facts.cn_stage_case_publish
        (
            package_id, case_id, family_root_case_id, application_number,
            case_family_root, suffix_path, filing_route, number_family,
            is_derived_case, relation_id, mark_name_raw, agent_code, filing_date, classes,
            source_file, source_first_line, source_last_line,
            source_row_hash, record_hash
        ) VALUES
        """ + ",\n".join(values)
    )


def _placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT 'AGENT_CODE_CHANGED_OBSERVED'
        FROM markorbit_facts.cn_stage_case_publish
        WHERE package_id = toUUID('{package}')
    """


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    case_publish.ensure_case_publish_schema(client=client)
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _seed_current(client)
        _seed_stage(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first = NativeAgentCodeEventCutoverClient(
            first_delegate,
            execution_client=_FailSecondEventInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        try:
            first.command(_placeholder(package))
        except RuntimeError as exc:
            if "fixture native agent-code-event interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native AGENT_CODE_EVENT interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed agent-code event before failure, got {partial}")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeAgentCodeEventCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed.native_agent_code_event_enabled:
            raise AssertionError("persisted AGENT_CODE_EVENT marker was not reused")
        result = resumed.command(_placeholder(package))
        resumed.assert_final_publish_complete()
        if result.range_count != 5 or result.skipped != 1 or result.executed != 4:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("AGENT_CODE_EVENT compatibility boundary was not preserved")

        events = client.query(
            f"""
            SELECT application_number, event_type, ifNull(toString(event_date), ''), affected_scope,
                   field_name, old_value_compact, new_value_compact,
                   evidence_level, legal_effect, confidence_score,
                   source_package_kind, source_file, source_first_line,
                   source_last_line, source_row_hash, source_rank, event_hash
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        event_hash = sha256(
            (
                f"{PREFIX}A100|AGENT_CODE_CHANGED_OBSERVED|agent_code|"
                f"AGENT-OLD-A|AGENT-NEW-A|{SOURCE_RANK}"
            ).encode("utf-8")
        ).hexdigest().upper().encode("ascii")
        expected = [(
            f"{PREFIX}A100", "AGENT_CODE_CHANGED_OBSERVED", "",
            "PARTY", "agent_code", "AGENT-OLD-A", "AGENT-NEW-A",
            "OFFICIAL_FACT_OBSERVATION", "NOT_DETERMINED", 1.0,
            "MONTHLY_PATCH", "stage-A100.xml", 10, 11,
            b"b" * 64, SOURCE_RANK, event_hash,
        )]
        if events != expected:
            raise AssertionError(f"unexpected native AGENT_CODE_EVENT rows: {events}")

        client.command(
            agent_code_event_sql(
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
            """
        ).result_rows[0][0]
        if int(converged) != 1:
            raise AssertionError(f"agent-code event_hash replay did not converge: {converged}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native AGENT_CODE_EVENT fixture passed: "
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
