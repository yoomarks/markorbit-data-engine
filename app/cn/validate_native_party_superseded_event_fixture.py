from __future__ import annotations

from hashlib import sha256
import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import party_publish
from app.cn.native_party_superseded_event import (
    NativePartySupersededEventCutoverClient,
    party_superseded_event_sql,
)
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.cn.storage_v2_party_history import PartyHistorySuppressionClient
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a158")
SOURCE_RANK = 987_654_336
SOURCE_SHA = "158" + "a" * 61
PREFIX = "PSE158-"
EFFECTIVE_DATE = "2026-02-28"


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
                raise RuntimeError("fixture native party-superseded interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native PARTY_SUPERSEDED_EVENT SQL leaked to compatibility delegate")

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
                VALUES (%s, 'CN', 'native-party-superseded-event-fixture.zip',
                        '/fixture/native-party-superseded-event.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-party-superseded-event',
                        DATE '2026-02-28', %s, 'REGISTERED')
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
        "ALTER TABLE markorbit_facts.cn_observed_event "
        f"DELETE WHERE startsWith(application_number, '{PREFIX}')",
        settings={"mutations_sync": 1},
    )


def _seed_current(client: Any) -> None:
    old_package = "00000000-0000-0000-0000-00000000b158"
    rows = (
        ("A100", "OWNER", "1", SOURCE_RANK - 1, 1, "Drop Owner", "a", "A"),
        ("A200", "OWNER", "2", SOURCE_RANK - 1, 1, "Keep Owner", "b", "B"),
        ("A300", "OWNER", "3", SOURCE_RANK - 1, 1, "Untouched Owner", "c", "C"),
        ("A400", "OWNER", "4", SOURCE_RANK, 1, "Equal Rank Owner", "d", "D"),
        ("A500", "OWNER", "5", SOURCE_RANK - 1, 0, "Already Closed", "e", "E"),
        ("A600", "OWNER", "6", SOURCE_RANK + 1, 1, "Newer Owner", "f", "F"),
    )
    values: list[str] = []
    for index, (suffix, role, key_char, rank, is_current, name, row_char, record_char) in enumerate(
        rows, start=1
    ):
        relation_id = f"00000000-0000-0000-0000-0000000158{index:02d}"
        case_id = f"00000000-0000-0000-0000-0000000258{index:02d}"
        mention_id = f"00000000-0000-0000-0000-0000000358{index:02d}"
        valid_to = "CAST(NULL, 'Nullable(Date32)')" if is_current else "toDate32('2025-12-31')"
        status = "OBSERVED_CURRENT" if is_current else "SUPERSEDED_BY_SOURCE_OBSERVATION"
        values.append(
            f"""
            (
                toUUID('{relation_id}'), toUUID('{case_id}'), '{PREFIX}{suffix}',
                '{role}', '{key_char * 64}', toUUID('{mention_id}'),
                CAST(NULL, 'Nullable(UUID)'), '', '{name}', lowerUTF8('{name}'),
                'Old Address', 'old address', 'CN', 'BJ', 'Beijing', [9], 0.90,
                toDate32('2025-01-01'), {valid_to}, {is_current}, '{status}',
                'CASE_ROLE_REPLACE', 'MONTHLY_PATCH', toDate32('2025-12-31'),
                'old-{suffix}.xml', 1, 2, '{row_char * 64}',
                toUUID('{old_package}'), '{record_char * 64}', {rank}, 0
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
        """ + ",\n".join(values)
    )


def _seed_stage(client: Any, package: str) -> None:
    rows = (
        ("A100", "OWNER", "9", "stage-a100.xml", 100, 101, "q"),
        ("A200", "OWNER", "2", "stage-a200.xml", 200, 201, "r"),
        ("A300", "AGENT", "8", "stage-a300.xml", 300, 301, "s"),
        ("A400", "OWNER", "7", "stage-a400.xml", 400, 401, "t"),
        ("A500", "OWNER", "6", "stage-a500.xml", 500, 501, "u"),
        ("A600", "OWNER", "7", "stage-a600.xml", 600, 601, "v"),
    )
    values: list[str] = []
    for index, (suffix, role, key_char, source_file, first_line, last_line, hash_char) in enumerate(
        rows, start=1
    ):
        relation_id = f"00000000-0000-0000-0000-0000000458{index:02d}"
        case_id = f"00000000-0000-0000-0000-0000000558{index:02d}"
        mention_id = f"00000000-0000-0000-0000-0000000658{index:02d}"
        values.append(
            f"""
            (
                toUUID('{package}'), toUUID('{case_id}'), '{PREFIX}{suffix}',
                '{role}', toUUID('{relation_id}'), '{key_char * 64}',
                toUUID('{mention_id}'), CAST(NULL, 'Nullable(UUID)'), '',
                'Incoming {role}', lowerUTF8('Incoming {role}'), '', '',
                'CN', 'BJ', 'Beijing', [9], 0.95, '{source_file}',
                {first_line}, {last_line}, '{hash_char * 64}', '{hash_char.upper() * 64}'
            )
            """
        )
    client.command(
        """
        INSERT INTO markorbit_facts.cn_stage_party_publish
        (
            package_id, case_id, application_number, role, relation_id,
            relation_key, mention_id, entity_id, agent_code, raw_name,
            normalized_name, raw_address, normalized_address, country_code,
            region_code, city, class_nos, confidence_score, source_file,
            source_first_line, source_last_line, source_row_hash, record_hash
        ) VALUES
        """ + ",\n".join(values)
    )


def _event_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_observed_event
        SELECT concat(cur.role, '_RELATION_SUPERSEDED_OBSERVED')
        FROM markorbit_facts.cn_stage_party_publish AS incoming
        JOIN markorbit_facts.cn_case_party_current AS cur FINAL
          ON cur.application_number = incoming.application_number
        WHERE incoming.package_id = toUUID('{package}')
    """


def _history_placeholder(action: str, package: str) -> str:
    marker = "'SUPERSEDED'" if action == "SUPERSEDED" else "'OBSERVED_CURRENT'"
    return f"""
        INSERT INTO markorbit_facts.cn_case_party_relation_history
        SELECT {marker}
        FROM markorbit_facts.cn_stage_party_publish
        WHERE package_id = toUUID('{package}')
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
        _seed_current(client)
        _seed_stage(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first_native = NativePartySupersededEventCutoverClient(
            first_delegate,
            execution_client=_FailSecondEventInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        first = PartyHistorySuppressionClient(first_native)
        try:
            first.command(_event_placeholder(package))
        except RuntimeError as exc:
            if "fixture native party-superseded interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native PARTY_SUPERSEDED_EVENT interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed superseded event before failure, got {partial}")

        resumed_delegate = _CompatibilityDelegate()
        resumed_native = NativePartySupersededEventCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed_native.native_party_superseded_event_enabled:
            raise AssertionError("persisted PARTY_SUPERSEDED_EVENT marker was not reused")
        resumed = PartyHistorySuppressionClient(resumed_native)
        result = resumed.command(_event_placeholder(package))
        resumed.command(_history_placeholder("SUPERSEDED", package))
        resumed.command(_history_placeholder("OBSERVED", package))
        resumed.assert_suppression_complete()
        resumed_native.assert_final_publish_complete()
        if result.range_count != 6 or result.skipped != 1 or result.executed != 5:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("PARTY_SUPERSEDED_EVENT compatibility boundary was not preserved")

        events = client.query(
            f"""
            SELECT application_number, event_type, toString(event_date), affected_scope,
                   class_no, field_name, old_value_compact, new_value_compact,
                   evidence_level, legal_effect, toString(confidence_score),
                   source_package_kind, source_file, source_first_line,
                   source_last_line, source_row_hash, source_rank, event_hash
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        relation_key = "1" * 64
        event_hash = sha256(
            f"{PREFIX}A100|OWNER|SUPERSEDED|{relation_key}|{SOURCE_RANK}".encode("utf-8")
        ).hexdigest().upper().encode("ascii")
        old_value = (
            '{"name":"Drop Owner","address":"Old Address","relation_key":"'
            + relation_key
            + '"}'
        )
        expected = [(
            f"{PREFIX}A100", "OWNER_RELATION_SUPERSEDED_OBSERVED", EFFECTIVE_DATE,
            "PARTY", None, "owner", old_value, "",
            "OFFICIAL_DATA_RELATION_REPLACEMENT", "NOT_DETERMINED", "0.95",
            "MONTHLY_PATCH", "stage-a100.xml", 100, 101,
            b"q" * 64, SOURCE_RANK, event_hash,
        )]
        if events != expected:
            raise AssertionError(f"unexpected native PARTY_SUPERSEDED_EVENT rows: {events}")

        client.command(
            party_superseded_event_sql(
                PACKAGE_ID,
                package_kind="MONTHLY_PATCH",
                source_effective_date=EFFECTIVE_DATE,
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
            raise AssertionError(f"party superseded event_hash replay did not converge: {converged}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native PARTY_SUPERSEDED_EVENT fixture passed: "
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
