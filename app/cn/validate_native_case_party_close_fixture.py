from __future__ import annotations

from hashlib import sha256
import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import party_publish
from app.cn.native_case_party_close import NativeCasePartyCloseCutoverClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a150")
SOURCE_RANK = 987_654_328
SOURCE_SHA = "150" + "a" * 61
PREFIX = "CPC150-"
EFFECTIVE_DATE = "2026-02-28"


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondCloseInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_case_party_current" in sql:
            self._inserts += 1
            if self._inserts == 2:
                raise RuntimeError("fixture native case-party-close interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native CASE_PARTY_CURRENT_CLOSE SQL leaked to compatibility delegate")

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
                VALUES (%s, 'CN', 'native-case-party-close-fixture.zip',
                        '/fixture/native-case-party-close.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-case-party-close',
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


def _seed_current(client: Any) -> None:
    old_package = "00000000-0000-0000-0000-00000000b150"
    rows = (
        ("A100", "OWNER", "1", SOURCE_RANK - 1, 1, "Drop Owner", "a", "A"),
        ("A200", "OWNER", "2", SOURCE_RANK - 1, 1, "Keep Owner", "b", "B"),
        ("A300", "OWNER", "3", SOURCE_RANK - 1, 1, "Untouched Owner", "c", "C"),
        ("A400", "OWNER", "4", SOURCE_RANK, 1, "Equal Rank Owner", "d", "D"),
        ("A500", "OWNER", "5", SOURCE_RANK - 1, 0, "Already Closed", "e", "E"),
    )
    values: list[str] = []
    for index, (suffix, role, key_char, rank, is_current, name, row_char, record_char) in enumerate(rows, start=1):
        relation_id = f"00000000-0000-0000-0000-0000000150{index:02d}"
        case_id = f"00000000-0000-0000-0000-0000000250{index:02d}"
        mention_id = f"00000000-0000-0000-0000-0000000350{index:02d}"
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
    )
    values: list[str] = []
    for index, (suffix, role, key_char, source_file, first_line, last_line, hash_char) in enumerate(rows, start=1):
        relation_id = f"00000000-0000-0000-0000-0000000450{index:02d}"
        case_id = f"00000000-0000-0000-0000-0000000550{index:02d}"
        mention_id = f"00000000-0000-0000-0000-0000000650{index:02d}"
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


def _placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_party_current
        SELECT incoming.relation_id, 'SUPERSEDED_BY_SOURCE_OBSERVATION'
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
        _seed_current(client)
        _seed_stage(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first = NativeCasePartyCloseCutoverClient(
            first_delegate,
            execution_client=_FailSecondCloseInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        try:
            first.command(_placeholder(package))
        except RuntimeError as exc:
            if "fixture native case-party-close interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native CASE_PARTY_CURRENT_CLOSE interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_party_current
            WHERE last_source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed close row before failure, got {partial}")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeCasePartyCloseCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed.native_case_party_close_enabled:
            raise AssertionError("persisted CASE_PARTY_CURRENT_CLOSE marker was not reused")
        result = resumed.command(_placeholder(package))
        resumed.assert_final_publish_complete()
        if result.range_count != 5 or result.skipped != 1 or result.executed != 4:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("CASE_PARTY_CURRENT_CLOSE compatibility boundary was not preserved")

        closed = client.query(
            f"""
            SELECT application_number, role, relation_key, raw_name,
                   toString(valid_from), toString(valid_to), is_current,
                   relation_status, replacement_mode, source_package_kind,
                   toString(source_effective_date), source_file,
                   source_first_line, source_last_line, source_row_hash,
                   record_hash, source_rank
            FROM markorbit_facts.cn_case_party_current
            WHERE last_source_package_id = toUUID('{package}')
            ORDER BY application_number
            """
        ).result_rows
        expected_hash = sha256(
            ("A" * 64 + f"|SUPERSEDED|{SOURCE_RANK}").encode("utf-8")
        ).hexdigest().upper().encode("ascii")
        expected = [(
            f"{PREFIX}A100", "OWNER", b"1" * 64, "Drop Owner",
            "2025-01-01", EFFECTIVE_DATE, 0,
            "SUPERSEDED_BY_SOURCE_OBSERVATION", "CASE_ROLE_REPLACE",
            "MONTHLY_PATCH", EFFECTIVE_DATE, "stage-a100.xml",
            100, 101, b"a" * 64, expected_hash, SOURCE_RANK,
        )]
        if closed != expected:
            raise AssertionError(f"unexpected native CASE_PARTY_CURRENT_CLOSE rows: {closed}")

        untouched = client.query(
            f"""
            SELECT application_number, role, is_current, source_rank
            FROM markorbit_facts.cn_case_party_current FINAL
            WHERE startsWith(application_number, '{PREFIX}')
              AND application_number != '{PREFIX}A100'
            ORDER BY application_number, role
            """
        ).result_rows
        expected_untouched = [
            (f"{PREFIX}A200", "OWNER", 1, SOURCE_RANK - 1),
            (f"{PREFIX}A300", "OWNER", 1, SOURCE_RANK - 1),
            (f"{PREFIX}A400", "OWNER", 1, SOURCE_RANK),
            (f"{PREFIX}A500", "OWNER", 0, SOURCE_RANK - 1),
        ]
        if untouched != expected_untouched:
            raise AssertionError(f"unrelated/retained relations changed: {untouched}")

        final_a100 = client.query(
            f"""
            SELECT is_current, relation_status, toString(valid_to), source_rank
            FROM markorbit_facts.cn_case_party_current FINAL
            WHERE application_number = '{PREFIX}A100'
              AND role = 'OWNER' AND relation_key = '{'1' * 64}'
            """
        ).result_rows
        if final_a100 != [(0, "SUPERSEDED_BY_SOURCE_OBSERVATION", EFFECTIVE_DATE, SOURCE_RANK)]:
            raise AssertionError(f"omitted relation was not closed: {final_a100}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native CASE_PARTY_CURRENT_CLOSE fixture passed: "
            f"ranges={result.range_count} skipped={result.skipped} "
            f"executed={result.executed} closed_rows={len(closed)}"
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
