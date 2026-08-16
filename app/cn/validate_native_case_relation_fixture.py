from __future__ import annotations

from hashlib import sha256
import os
import uuid
from typing import Any

import clickhouse_connect

from app.cn import case_publish
from app.cn.native_case_relation import NativeCaseRelationCutoverClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
)
from app.db import postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000a145")
SOURCE_RANK = 987_654_323
SOURCE_SHA = "145" + "c" * 61


def _client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "markorbit"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "markorbit"),
        database="markorbit_facts",
    )


class _FailSecondRelationInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._relation_inserts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_case_relation_current" in sql:
            self._relation_inserts += 1
            if self._relation_inserts == 2:
                raise RuntimeError("fixture native case-relation interruption")
        return self._delegate.command(sql, *args, **kwargs)


class _CompatibilityDelegate:
    final_tasks_executed = 0
    final_tasks_skipped = 0

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.final_assertions = 0

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.commands.append(sql)
        raise AssertionError("native case-relation SQL leaked to compatibility delegate")

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
                VALUES (%s, 'CN', 'native-case-relation-fixture.zip',
                        '/fixture/native-case-relation.zip', 0, %s,
                        'MONTHLY_PATCH', 'MONTH', 'native-case-relation', %s,
                        'REGISTERED')
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
        "ALTER TABLE markorbit_facts.cn_case_relation_current "
        f"DELETE WHERE source_package_id = toUUID('{package}')",
        settings={"mutations_sync": 1},
    )


def _stage_fixture_rows(client: Any, package: str) -> None:
    zero = "00000000-0000-0000-0000-000000000000"
    rows = [
        (
            "R100",
            "ROOT100",
            "A",
            1,
            "00000000-0000-0000-0000-000000003101",
            "00000000-0000-0000-0000-000000004101",
            "00000000-0000-0000-0000-000000005101",
            "DIVISION",
            "IR100",
            "relation-a.xml",
            10,
            12,
            "a" * 64,
        ),
        (
            "R200",
            "R200",
            "",
            0,
            "00000000-0000-0000-0000-000000003102",
            "00000000-0000-0000-0000-000000003102",
            zero,
            "DIRECT_CN",
            "",
            "relation-b.xml",
            20,
            20,
            "b" * 64,
        ),
        (
            "R300",
            "ROOT300",
            "B",
            1,
            "00000000-0000-0000-0000-000000003103",
            "00000000-0000-0000-0000-000000004103",
            "00000000-0000-0000-0000-000000005103",
            "DERIVED_CN",
            "IR300",
            "relation-c.xml",
            30,
            31,
            "c" * 64,
        ),
        (
            "R400",
            "ROOT400",
            "C",
            1,
            "00000000-0000-0000-0000-000000003104",
            "00000000-0000-0000-0000-000000004104",
            "00000000-0000-0000-0000-000000005104",
            "DERIVED_CN",
            "IR400",
            "relation-d.xml",
            40,
            42,
            "d" * 64,
        ),
    ]
    values = []
    for (
        application,
        root,
        suffix,
        derived,
        case_id,
        root_case_id,
        relation_id,
        filing_route,
        irn,
        source_file,
        first_line,
        last_line,
        row_hash,
    ) in rows:
        values.append(
            "(" + ", ".join(
                [
                    f"toUUID('{package}')",
                    f"toUUID('{case_id}')",
                    f"toUUID('{root_case_id}')",
                    f"'{application}'",
                    f"'{root}'",
                    f"'{suffix}'",
                    f"'{filing_route}'",
                    "'OTHER'",
                    f"'{irn}'",
                    str(derived),
                    f"toUUID('{relation_id}')",
                    f"'{source_file}'",
                    str(first_line),
                    str(last_line),
                    f"'{row_hash}'",
                    f"'{row_hash}'",
                ]
            ) + ")"
        )
    client.command(
        """
        INSERT INTO markorbit_facts.cn_stage_case_publish
        (
            package_id, case_id, family_root_case_id, application_number,
            case_family_root, suffix_path, filing_route, number_family,
            international_registration_number, is_derived_case, relation_id,
            source_file, source_first_line, source_last_line,
            source_row_hash, record_hash
        ) VALUES
        """
        + ",\n".join(values)
    )


def _case_scope_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_scope_current
        SELECT incoming.case_id
        FROM markorbit_facts.cn_stage_scope_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
    """


def _relation_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_case_relation_current
        SELECT incoming.relation_id
        FROM markorbit_facts.cn_stage_case_publish AS incoming
        WHERE incoming.package_id = toUUID('{package}')
          AND incoming.is_derived_case = 1
    """


def _scope_placeholder(package: str) -> str:
    return f"""
        INSERT INTO markorbit_facts.cn_scope_carve_out_current
        SELECT generateUUIDv4()
        FROM markorbit_facts.cn_stage_scope_publish
        WHERE package_id = toUUID('{package}')
    """


def _relation_hash(root: str, target: str, suffix: str) -> bytes:
    payload = f"{root}|{target}|DERIVED_CASE|{suffix}"
    return sha256(payload.encode("utf-8")).hexdigest().upper().encode("ascii")


def main() -> None:
    client = _client()
    package = str(PACKAGE_ID)
    ensure_publish_subtask_schema()
    case_publish.ensure_case_publish_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup(client, package)
        _stage_fixture_rows(client, package)
        store = PublishSubtaskStore(PACKAGE_ID)

        first_delegate = _CompatibilityDelegate()
        first = NativeCaseRelationCutoverClient(
            first_delegate,
            execution_client=_FailSecondRelationInsert(client),
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=True,
            target_rows=2,
        )
        first.command(_case_scope_placeholder(package))
        try:
            first.command(_relation_placeholder(package))
        except RuntimeError as exc:
            if "fixture native case-relation interruption" not in str(exc):
                raise
        else:
            raise AssertionError("native case-relation interruption did not fire")

        partial = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.cn_case_relation_current FINAL
            WHERE source_package_id = toUUID('{package}')
            """
        ).result_rows[0][0]
        if int(partial) != 1:
            raise AssertionError(f"expected one committed relation before failure, got {partial}")
        if first_delegate.commands:
            raise AssertionError("compatibility delegate received native relation SQL")

        resumed_delegate = _CompatibilityDelegate()
        resumed = NativeCaseRelationCutoverClient(
            resumed_delegate,
            execution_client=client,
            package_uuid=PACKAGE_ID,
            source_rank=SOURCE_RANK,
            subtask_store=store,
            allow_new_cutover=False,
            target_rows=2,
        )
        if not resumed.native_case_relation_enabled:
            raise AssertionError("persisted relation cutover marker was not reused")
        resumed.command(_case_scope_placeholder(package))
        result = resumed.command(_relation_placeholder(package))
        resumed.command(_scope_placeholder(package))
        resumed.assert_final_publish_complete()
        if result.range_count != 2 or result.skipped != 1 or result.executed != 1:
            raise AssertionError(result)
        if resumed_delegate.commands or resumed_delegate.final_assertions != 1:
            raise AssertionError("native relation compatibility boundary was not preserved")

        actual = client.query(
            f"""
            SELECT
                source_application_number,
                target_application_number,
                relation_type,
                derivation_reason,
                filing_route,
                international_registration_number,
                evidence_status,
                source_package_kind,
                source_file,
                source_first_line,
                source_last_line,
                source_row_hash,
                record_hash,
                source_rank
            FROM markorbit_facts.cn_case_relation_current FINAL
            WHERE source_package_id = toUUID('{package}')
            ORDER BY target_application_number
            """
        ).result_rows
        expected = [
            (
                "ROOT100", "R100", "DERIVED_CASE", "UNKNOWN", "DIVISION", "IR100",
                "SUFFIX_AND_ROOT_NUMBER_OBSERVED", "MONTHLY_PATCH", "relation-a.xml",
                10, 12, b"a" * 64, _relation_hash("ROOT100", "R100", "A"), SOURCE_RANK,
            ),
            (
                "ROOT300", "R300", "DERIVED_CASE", "UNKNOWN", "DERIVED_CN", "IR300",
                "SUFFIX_AND_ROOT_NUMBER_OBSERVED", "MONTHLY_PATCH", "relation-c.xml",
                30, 31, b"c" * 64, _relation_hash("ROOT300", "R300", "B"), SOURCE_RANK,
            ),
            (
                "ROOT400", "R400", "DERIVED_CASE", "UNKNOWN", "DERIVED_CN", "IR400",
                "SUFFIX_AND_ROOT_NUMBER_OBSERVED", "MONTHLY_PATCH", "relation-d.xml",
                40, 42, b"d" * 64, _relation_hash("ROOT400", "R400", "C"), SOURCE_RANK,
            ),
        ]
        if actual != expected:
            raise AssertionError(f"unexpected native case-relation rows: {actual}")

        summary = store.assert_complete()
        if summary.get("FAILED", 0) or summary.get("RUNNING", 0):
            raise AssertionError(summary)

        print(
            "native case-relation fixture passed: "
            f"ranges={result.range_count} skipped={result.skipped} "
            f"executed={result.executed} relations={len(actual)}"
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
