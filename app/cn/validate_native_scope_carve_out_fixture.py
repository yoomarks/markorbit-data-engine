from __future__ import annotations

from hashlib import sha256
import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import goods_lifecycle
from app.cn.native_scope_carve_out import NativeScopeCarveOutCutoverClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a146")
SOURCE_RANK = 987_654_324
SOURCE_SHA = "146" + "d" * 61


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondScopeInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._scope_inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_scope_carve_out_current" in sql:
            self._scope_inserts += 1
            if self._scope_inserts == 2:
                raise RuntimeError("fixture native scope-carve-out interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native scope-carve-out SQL leaked to compatibility delegate")

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
                    source_rank, status
                )
                VALUES (%s, 'CN', 'native-scope-carve-out-fixture.zip',
                        '/fixture/native-scope-carve-out.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-scope-carve-out', %s,
                        'REGISTERED')
                """,
                (str(PACKAGE_ID), SOURCE_SHA, SOURCE_RANK),
            )
        conn.commit()


def _cleanup(client: Any, package: str) -> None:
    mutations = (
        "ALTER TABLE markorbit_facts.cn_scope_carve_out_current "
        f"DELETE WHERE source_package_id = toUUID('{package}')",
        "ALTER TABLE markorbit_facts.cn_case_relation_current "
        f"DELETE WHERE source_package_id = toUUID('{package}')",
        "ALTER TABLE markorbit_facts.cn_case_scope_current "
        f"DELETE WHERE last_source_package_id = toUUID('{package}')",
        "ALTER TABLE markorbit_facts.cn_stage_scope_publish "
        f"DELETE WHERE package_id = toUUID('{package}')",
    )
    for sql in mutations:
        client.command(sql, settings={"mutations_sync": 1})


def _seed_relations(client: Any, package: str) -> None:
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_case_relation_current
        (
            relation_id, source_case_id, target_case_id,
            source_application_number, target_application_number,
            relation_type, derivation_reason, filing_route,
            international_registration_number, confidence_score, evidence_status,
            source_package_id, source_package_kind, source_file,
            source_first_line, source_last_line, source_row_hash,
            record_hash, source_rank, is_deleted
        ) VALUES
        (
            toUUID('00000000-0000-0000-0000-000000006101'),
            toUUID('00000000-0000-0000-0000-000000007101'),
            toUUID('00000000-0000-0000-0000-000000008101'),
            'ROOT100', 'R100', 'DERIVED_CASE', 'UNKNOWN', 'DIVISION', 'IR100',
            0.95, 'SUFFIX_AND_ROOT_NUMBER_OBSERVED', toUUID('{package}'),
            'MONTHLY_PATCH', 'relation-r100.xml', 1, 2, '{'1' * 64}',
            '{'2' * 64}', {SOURCE_RANK}, 0
        ),
        (
            toUUID('00000000-0000-0000-0000-000000006300'),
            toUUID('00000000-0000-0000-0000-000000007300'),
            toUUID('00000000-0000-0000-0000-000000008300'),
            'ROOT300', 'R300', 'DERIVED_CASE', 'UNKNOWN', 'DERIVED_CN', 'IR300',
            0.95, 'SUFFIX_AND_ROOT_NUMBER_OBSERVED', toUUID('{package}'),
            'MONTHLY_PATCH', 'relation-r300.xml', 3, 4, '{'3' * 64}',
            '{'4' * 64}', {SOURCE_RANK}, 0
        )
        """
    )


def _seed_source_scope(client: Any, package: str) -> None:
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        (
            case_id, application_number, class_no, scope_hash,
            source_package_kind, source_file, source_first_line, source_last_line,
            source_row_hash, last_source_package_id, source_rank, is_deleted
        ) VALUES
        (
            toUUID('00000000-0000-0000-0000-000000009101'),
            'ROOT100', 9, '{'s' * 64}', 'MONTHLY_PATCH', 'root-scope.xml',
            5, 6, '{'5' * 64}', toUUID('{package}'), {SOURCE_RANK - 1}, 0
        )
        """
    )


def _seed_target_scope_stage(client: Any, package: str) -> None:
    client.command(
        f"""
        INSERT INTO markorbit_facts.cn_stage_scope_publish
        (
            package_id, case_id, application_number, class_no,
            scope_hash, effective_scope_hash, source_file,
            source_first_line, source_last_line, source_row_hash
        ) VALUES
        (
            toUUID('{package}'),
            toUUID('00000000-0000-0000-0000-00000000a101'),
            'R100', 9, '{'t' * 64}', 'effective-r100',
            'target-r100.xml', 10, 12, '{'a' * 64}'
        ),
        (
            toUUID('{package}'),
            toUUID('00000000-0000-0000-0000-00000000a300'),
            'R300', 25, '{'u' * 64}', 'effective-r300',
            'target-r300.xml', 30, 31, '{'c' * 64}'
        )
        """
    )


def _case_scope_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        SELECT incoming.case_id
        FROM markorbit_facts.cn_stage_scope_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def _scope_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_scope_carve_out_current
        SELECT generateUUIDv4()
        FROM markorbit_facts.cn_stage_scope_publish
        WHERE package_id = toUUID('{package}')
    """


def _record_hash(
    source_app: str,
    target_app: str,
    class_no: int,
    source_hash: str,
    target_hash: str,
) -> bytes:
    # Legacy ClickHouse LEFT JOIN semantics expose a missing FixedString(64)
    # source hash as 64 NUL bytes inside concat(), although insertion into the
    # destination String column displays as an empty string. Preserve that exact
    # hash behavior rather than normalizing the semantic input in the fixture.
    hash_source = source_hash if source_hash else "\x00" * 64
    payload = f"{source_app}|{target_app}|{class_no}|{hash_source}|{target_hash}"
    return sha256(payload.encode("utf-8")).hexdigest().upper().encode("ascii")


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    goods_lifecycle.ensure_m16_goods_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _seed_relations(client, package)
        _seed_source_scope(client, package)
        _seed_target_scope_stage(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first = NativeScopeCarveOutCutoverClient(
            first_delegate,
            execution_client=_FailSecondScopeInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=1,
        )
        first.command(_case_scope_placeholder(package))
        try:
            first.command(_scope_placeholder(package))
        except RuntimeError as exc:
            if "fixture native scope-carve-out interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native scope-carve-out interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_scope_carve_out_current FINAL
            WHERE source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed carve-out before failure, got {partial}")
        if first_delegate.commands:
            raise AssertionError("compatibility delegate received native carve-out SQL")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeScopeCarveOutCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=1,
        )
        if not resumed.native_scope_carve_out_enabled:
            raise AssertionError("persisted scope-carve-out cutover marker was not reused")
        resumed.command(_case_scope_placeholder(package))
        result = resumed.command(_scope_placeholder(package))
        resumed.assert_final_publish_complete()
        if result.range_count != 2 or result.skipped != 1 or result.executed != 1:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("native scope-carve-out compatibility boundary was not preserved")

        actual = client.query(
            f"""
            SELECT
                toString(relation_id), source_application_number,
                target_application_number, class_no, carve_out_type,
                source_scope_hash, target_scope_hash, evidence_status,
                round(toFloat64(confidence_score), 2), toString(source_package_id),
                source_file, source_first_line, source_last_line,
                source_row_hash, record_hash, source_rank
            FROM markorbit_facts.cn_scope_carve_out_current FINAL
            WHERE source_package_id = toUUID('{package}')
            ORDER BY target_application_number
            """
        ).result_rows
        expected = [
            (
                "00000000-0000-0000-0000-000000006101", "ROOT100", "R100", 9,
                "UNKNOWN", "s" * 64, "t" * 64, "ROOT_AND_TARGET_SCOPE_OBSERVED",
                0.75, package, "target-r100.xml", 10, 12, b"a" * 64,
                _record_hash("ROOT100", "R100", 9, "s" * 64, "t" * 64), SOURCE_RANK,
            ),
            (
                "00000000-0000-0000-0000-000000006300", "ROOT300", "R300", 25,
                "UNKNOWN", "", "u" * 64, "TARGET_SCOPE_ONLY", 0.55, package,
                "target-r300.xml", 30, 31, b"c" * 64,
                _record_hash("ROOT300", "R300", 25, "", "u" * 64), SOURCE_RANK,
            ),
        ]
        if actual != expected:
            raise AssertionError(f"unexpected native scope-carve-out rows: {actual}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native scope-carve-out fixture passed: "
            f"ranges={result.range_count} skipped={result.skipped} "
            f"executed={result.executed} carve_outs={len(actual)}"
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
