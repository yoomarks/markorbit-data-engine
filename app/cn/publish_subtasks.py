from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
import uuid

from app.db import postgres_conn


CHECKPOINT_VERSION = "CN_FINAL_PUBLISH_V1"
PUBLISH_STAGE_TABLES = (
    "cn_stage_case_publish",
    "cn_stage_party_publish",
    "cn_stage_scope_publish",
)


@dataclass(frozen=True)
class PublishCheckpoint:
    package_id: str
    checkpoint_version: str
    stage_counts: dict[str, int]


class PublishSubtaskStore:
    """Durable progress ledger for bounded legacy snapshot persistence.

    ClickHouse publish-stage tables are the temporary data files. This Postgres
    ledger records which application-range command has committed successfully.
    A worker/container restart therefore resumes at the first unfinished range.
    """

    def __init__(self, package_uuid: uuid.UUID | str) -> None:
        self.package_id = str(package_uuid)

    @staticmethod
    def task_key(
        *,
        sql_hash: str,
        stage_table: str,
        lower: str | None,
        upper: str | None,
    ) -> str:
        payload = "|".join(
            (
                CHECKPOINT_VERSION,
                sql_hash,
                stage_table,
                lower or "-inf",
                upper or "+inf",
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def is_success(self, task_key: str, sql_hash: str) -> bool:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, sql_hash
                    FROM control.cn_publish_subtask
                    WHERE package_id = %s
                      AND checkpoint_version = %s
                      AND task_key = %s
                    """,
                    (self.package_id, CHECKPOINT_VERSION, task_key),
                )
                row = cur.fetchone()
        return bool(
            row
            and str(row["status"]) == "SUCCESS"
            and str(row["sql_hash"]) == sql_hash
        )

    def mark_running(
        self,
        *,
        task_key: str,
        task_group: str,
        task_index: int,
        task_total: int,
        stage_table: str,
        lower: str | None,
        upper: str | None,
        sql_hash: str,
    ) -> None:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO control.cn_publish_subtask
                    (
                        package_id, checkpoint_version, task_key, task_group,
                        task_index, task_total, stage_table, range_lower, range_upper,
                        sql_hash, status, attempts, started_at, completed_at,
                        last_error, updated_at
                    )
                    VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     'RUNNING', 1, now(), NULL, '', now())
                    ON CONFLICT (package_id, checkpoint_version, task_key)
                    DO UPDATE SET
                        task_group = EXCLUDED.task_group,
                        task_index = EXCLUDED.task_index,
                        task_total = EXCLUDED.task_total,
                        stage_table = EXCLUDED.stage_table,
                        range_lower = EXCLUDED.range_lower,
                        range_upper = EXCLUDED.range_upper,
                        sql_hash = EXCLUDED.sql_hash,
                        status = 'RUNNING',
                        attempts = control.cn_publish_subtask.attempts + 1,
                        started_at = now(),
                        completed_at = NULL,
                        last_error = '',
                        updated_at = now()
                    """,
                    (
                        self.package_id,
                        CHECKPOINT_VERSION,
                        task_key,
                        task_group,
                        int(task_index),
                        int(task_total),
                        stage_table,
                        lower,
                        upper,
                        sql_hash,
                    ),
                )

    def mark_success(self, task_key: str) -> None:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE control.cn_publish_subtask
                    SET status = 'SUCCESS',
                        completed_at = now(),
                        last_error = '',
                        updated_at = now()
                    WHERE package_id = %s
                      AND checkpoint_version = %s
                      AND task_key = %s
                    """,
                    (self.package_id, CHECKPOINT_VERSION, task_key),
                )

    def mark_failed(self, task_key: str, error: str) -> None:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE control.cn_publish_subtask
                    SET status = 'FAILED',
                        completed_at = NULL,
                        last_error = %s,
                        updated_at = now()
                    WHERE package_id = %s
                      AND checkpoint_version = %s
                      AND task_key = %s
                    """,
                    (
                        str(error)[-8000:],
                        self.package_id,
                        CHECKPOINT_VERSION,
                        task_key,
                    ),
                )

    def summary(self) -> dict[str, int]:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, count(*) AS row_count
                    FROM control.cn_publish_subtask
                    WHERE package_id = %s AND checkpoint_version = %s
                    GROUP BY status
                    """,
                    (self.package_id, CHECKPOINT_VERSION),
                )
                rows = cur.fetchall()
        result = {"SUCCESS": 0, "RUNNING": 0, "FAILED": 0}
        for row in rows:
            result[str(row["status"])] = int(row["row_count"] or 0)
        return result

    def assert_complete(self) -> dict[str, int]:
        summary = self.summary()
        if summary.get("RUNNING", 0) or summary.get("FAILED", 0):
            raise RuntimeError(f"CN final publish subtask ledger incomplete: {summary}")
        return summary


def ensure_publish_subtask_schema() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS control.cn_publish_checkpoint
                (
                    package_id UUID PRIMARY KEY
                        REFERENCES control.source_package(package_id) ON DELETE CASCADE,
                    checkpoint_version TEXT NOT NULL,
                    stage_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS control.cn_publish_subtask
                (
                    package_id UUID NOT NULL
                        REFERENCES control.source_package(package_id) ON DELETE CASCADE,
                    checkpoint_version TEXT NOT NULL,
                    task_key CHAR(64) NOT NULL,
                    task_group TEXT NOT NULL,
                    task_index INTEGER NOT NULL,
                    task_total INTEGER NOT NULL,
                    stage_table TEXT NOT NULL,
                    range_lower TEXT,
                    range_upper TEXT,
                    sql_hash CHAR(64) NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (package_id, checkpoint_version, task_key)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cn_publish_subtask_status
                ON control.cn_publish_subtask(package_id, checkpoint_version, status)
                """
            )


def capture_publish_stage_counts(
    package_uuid: uuid.UUID | str,
    *,
    client: Any,
) -> dict[str, int]:
    package = str(package_uuid)
    counts: dict[str, int] = {}
    for stage_table in PUBLISH_STAGE_TABLES:
        rows = client.query(
            f"""
            SELECT count()
            FROM markorbit_facts.{stage_table}
            WHERE package_id = toUUID('{package}')
            """
        ).result_rows
        counts[stage_table] = int(rows[0][0] or 0) if rows else 0
    return counts


def save_publish_checkpoint(
    package_uuid: uuid.UUID | str,
    *,
    stage_counts: dict[str, int],
) -> None:
    ensure_publish_subtask_schema()
    package = str(package_uuid)
    normalized = {
        table: int(stage_counts.get(table, 0)) for table in PUBLISH_STAGE_TABLES
    }
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO control.cn_publish_checkpoint
                    (package_id, checkpoint_version, stage_counts, created_at, updated_at)
                VALUES (%s, %s, %s::jsonb, now(), now())
                ON CONFLICT (package_id)
                DO UPDATE SET
                    checkpoint_version = EXCLUDED.checkpoint_version,
                    stage_counts = EXCLUDED.stage_counts,
                    updated_at = now()
                """,
                (package, CHECKPOINT_VERSION, payload),
            )


def load_publish_checkpoint(
    package_uuid: uuid.UUID | str,
) -> PublishCheckpoint | None:
    ensure_publish_subtask_schema()
    package = str(package_uuid)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id::text, checkpoint_version, stage_counts
                FROM control.cn_publish_checkpoint
                WHERE package_id = %s
                """,
                (package,),
            )
            row = cur.fetchone()
    if not row:
        return None
    raw_counts = dict(row["stage_counts"] or {})
    return PublishCheckpoint(
        package_id=str(row["package_id"]),
        checkpoint_version=str(row["checkpoint_version"]),
        stage_counts={key: int(value or 0) for key, value in raw_counts.items()},
    )


def publish_checkpoint_is_usable(
    package_uuid: uuid.UUID | str,
    checkpoint: PublishCheckpoint,
    *,
    client: Any,
) -> bool:
    if checkpoint.checkpoint_version != CHECKPOINT_VERSION:
        return False
    expected = {
        table: int(checkpoint.stage_counts.get(table, 0))
        for table in PUBLISH_STAGE_TABLES
    }
    actual = capture_publish_stage_counts(package_uuid, client=client)
    return actual == expected


def has_publish_checkpoint(package_uuid: uuid.UUID | str) -> bool:
    return load_publish_checkpoint(package_uuid) is not None


def clear_publish_checkpoint(package_uuid: uuid.UUID | str) -> None:
    ensure_publish_subtask_schema()
    package = str(package_uuid)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM control.cn_publish_subtask WHERE package_id = %s",
                (package,),
            )
            cur.execute(
                "DELETE FROM control.cn_publish_checkpoint WHERE package_id = %s",
                (package,),
            )
