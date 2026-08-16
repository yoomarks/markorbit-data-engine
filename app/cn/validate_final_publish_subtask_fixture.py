from __future__ import annotations

from typing import Any
import uuid

from app.cn import case_publish as case
from app.cn import goods_lifecycle as goods
from app.cn import party_publish as party
from app.cn.final_publish import ResumableFinalPublishClient
from app.cn.publish_subtasks import (
    PublishSubtaskStore,
    capture_publish_stage_counts,
    clear_publish_checkpoint,
    ensure_publish_subtask_schema,
    load_publish_checkpoint,
    publish_checkpoint_is_usable,
    save_publish_checkpoint,
)
from app.db import clickhouse_client, postgres_conn


PACKAGE_ID = uuid.UUID("00000000-0000-0000-0000-00000000f137")
SOURCE_RANK = 13_700_001
STAGE_ROWS = 50_010
SOURCE_SHA = "137" + "f" * 61


class _FailSecondInsert:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._insert_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def query(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        return self._delegate.query(sql, *args, **kwargs)

    def command(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        if "INSERT INTO markorbit_facts.cn_case_current" in sql:
            self._insert_count += 1
            if self._insert_count == 2:
                raise RuntimeError("fixture interruption after first committed range")
        return self._delegate.command(sql, *args, **kwargs)


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
                VALUES (%s, 'CN', 'fixture.zip', '/fixture/fixture.zip', 0,
                        %s, 'MONTHLY_PATCH', 'MONTH', 'fixture', %s, 'REGISTERED')
                """,
                (str(PACKAGE_ID), SOURCE_SHA, SOURCE_RANK),
            )


def _cleanup_clickhouse() -> None:
    client = clickhouse_client()
    package = str(PACKAGE_ID)
    predicates = (
        ("cn_stage_case_publish", f"package_id = toUUID('{package}')"),
        ("cn_stage_party_publish", f"package_id = toUUID('{package}')"),
        ("cn_stage_scope_publish", f"package_id = toUUID('{package}')"),
        ("cn_case_current", f"last_source_package_id = toUUID('{package}')"),
    )
    for table, predicate in predicates:
        client.command(
            f"ALTER TABLE markorbit_facts.{table} DELETE WHERE {predicate}",
            settings={"mutations_sync": 1},
        )


def _stage_case_rows() -> None:
    package = str(PACKAGE_ID)
    clickhouse_client().command(
        f"""
        INSERT INTO markorbit_facts.cn_stage_case_publish
            (package_id, application_number, source_row_hash)
        SELECT
            toUUID('{package}'),
            concat('F', leftPad(toString(number), 8, '0')),
            repeat('a', 64)
        FROM numbers({STAGE_ROWS})
        """
    )


def _case_current_insert() -> str:
    package = str(PACKAGE_ID)
    return f"""
        INSERT INTO markorbit_facts.cn_case_current
            (application_number, last_source_package_id, source_rank, ingested_at, is_deleted)
        SELECT
            incoming.application_number,
            toUUID('{package}'),
            {SOURCE_RANK},
            now64(3),
            toUInt8(0)
        FROM
        (
            SELECT application_number
            FROM markorbit_facts.cn_stage_case_publish
            WHERE package_id = toUUID('{package}')
        ) AS incoming
        LEFT JOIN markorbit_facts.cn_case_current AS cur FINAL
          ON cur.application_number = incoming.application_number
        WHERE cur.application_number = '' OR cur.source_rank <= {SOURCE_RANK}
    """


def _current_count() -> int:
    package = str(PACKAGE_ID)
    rows = clickhouse_client().query(
        f"""
        SELECT count()
        FROM markorbit_facts.cn_case_current FINAL
        WHERE last_source_package_id = toUUID('{package}')
          AND is_deleted = 0
        """
    ).result_rows
    return int(rows[0][0] or 0) if rows else 0


def main() -> None:
    ensure_publish_subtask_schema()
    case.ensure_case_publish_schema()
    party.ensure_party_publish_schema()
    goods.ensure_m16_goods_schema()
    _ensure_source_package()

    try:
        clear_publish_checkpoint(PACKAGE_ID)
        _cleanup_clickhouse()
        _stage_case_rows()

        base_client = clickhouse_client()
        stage_counts = capture_publish_stage_counts(PACKAGE_ID, client=base_client)
        if stage_counts["cn_stage_case_publish"] != STAGE_ROWS:
            raise AssertionError(stage_counts)
        save_publish_checkpoint(PACKAGE_ID, stage_counts=stage_counts)
        checkpoint = load_publish_checkpoint(PACKAGE_ID)
        if checkpoint is None:
            raise AssertionError("publish checkpoint was not persisted")
        if not publish_checkpoint_is_usable(
            PACKAGE_ID,
            checkpoint,
            client=base_client,
        ):
            raise AssertionError("fresh publish checkpoint was not usable")

        store = PublishSubtaskStore(PACKAGE_ID)
        interrupted = ResumableFinalPublishClient(
            _FailSecondInsert(base_client),
            package_uuid=PACKAGE_ID,
            agent_batches=[],
            subtask_store=store,
        )
        try:
            interrupted.command(_case_current_insert())
        except RuntimeError as exc:
            if "fixture interruption after first committed range" not in str(exc):
                raise
        else:
            raise AssertionError("fixture interruption did not fire")

        if interrupted.final_tasks_executed != 1:
            raise AssertionError(
                f"expected one committed range, got {interrupted.final_tasks_executed}"
            )
        partial_count = _current_count()
        if not 0 < partial_count < STAGE_ROWS:
            raise AssertionError(
                f"expected a partial durable current snapshot, got {partial_count}"
            )

        resumed = ResumableFinalPublishClient(
            clickhouse_client(),
            package_uuid=PACKAGE_ID,
            agent_batches=[],
            subtask_store=store,
        )
        resumed.command(_case_current_insert())
        if resumed.final_tasks_skipped != 1:
            raise AssertionError(
                f"expected one skipped successful task, got {resumed.final_tasks_skipped}"
            )
        if resumed.final_tasks_executed < 1:
            raise AssertionError("resume did not execute unfinished tasks")

        final_count = _current_count()
        if final_count != STAGE_ROWS:
            raise AssertionError(
                f"expected {STAGE_ROWS} final current rows, got {final_count}"
            )
        summary = store.assert_complete()
        if summary["FAILED"] or summary["RUNNING"]:
            raise AssertionError(summary)

        audit = resumed.audit_current_coverage(source_rank=SOURCE_RANK)
        if any(audit.values()):
            raise AssertionError(audit)

        print(
            "resumable final publish fixture passed: "
            f"partial={partial_count} final={final_count} "
            f"skipped={resumed.final_tasks_skipped} executed={resumed.final_tasks_executed}"
        )
    finally:
        try:
            clear_publish_checkpoint(PACKAGE_ID)
        finally:
            _cleanup_clickhouse()
            with postgres_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM control.source_package WHERE package_id = %s",
                        (str(PACKAGE_ID),),
                    )


if __name__ == "__main__":
    main()
