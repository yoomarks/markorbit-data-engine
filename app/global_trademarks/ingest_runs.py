from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.db import postgres_conn
from app.global_trademarks.ingest_schema import ensure_seed_ingest_schema


_RUN_NAMESPACE = uuid.UUID("ba14ce5e-8f8f-43fb-978f-af445f7d7d4a")


@dataclass(frozen=True, slots=True)
class IngestRunState:
    run_id: uuid.UUID
    checkpoint: int
    rows_committed: int
    status: str

    @property
    def complete(self) -> bool:
        return self.status == "COMPLETE"


def ingest_run_id(*, source_object_id: uuid.UUID, pipeline_id: str) -> uuid.UUID:
    return uuid.uuid5(_RUN_NAMESPACE, f"{source_object_id}\0{pipeline_id}")


def begin_or_resume_ingest_run(
    *,
    source_object_id: uuid.UUID,
    jurisdiction: str,
    pipeline_id: str,
    metadata: dict[str, Any] | None = None,
) -> IngestRunState:
    ensure_seed_ingest_schema()
    run_id = ingest_run_id(source_object_id=source_object_id, pipeline_id=pipeline_id)
    payload = json.dumps(metadata or {}, ensure_ascii=False)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO acquisition.global_trademark_ingest_run (
                    run_id, source_object_id, jurisdiction, pipeline_id, metadata
                ) VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (source_object_id, pipeline_id) DO UPDATE
                SET updated_at = now(),
                    status = CASE
                        WHEN acquisition.global_trademark_ingest_run.status = 'COMPLETE'
                        THEN 'COMPLETE'
                        ELSE 'RUNNING'
                    END,
                    error_text = NULL,
                    metadata = acquisition.global_trademark_ingest_run.metadata || EXCLUDED.metadata
                RETURNING run_id, checkpoint, rows_committed, status
                """,
                (run_id, source_object_id, jurisdiction, pipeline_id, payload),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("failed to create or resume trademark ingest run")
    return IngestRunState(
        run_id=row["run_id"],
        checkpoint=int(row["checkpoint"]),
        rows_committed=int(row["rows_committed"]),
        status=row["status"],
    )


def checkpoint_ingest_run(
    cur,
    *,
    run_id: uuid.UUID,
    checkpoint: int,
    rows_committed: int,
) -> None:
    cur.execute(
        """
        UPDATE acquisition.global_trademark_ingest_run
        SET checkpoint = %s,
            rows_committed = %s,
            status = 'RUNNING',
            updated_at = now(),
            error_text = NULL
        WHERE run_id = %s
        """,
        (checkpoint, rows_committed, run_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"missing trademark ingest run: {run_id}")


def complete_ingest_run(
    cur,
    *,
    run_id: uuid.UUID,
    checkpoint: int,
    rows_committed: int,
) -> None:
    cur.execute(
        """
        UPDATE acquisition.global_trademark_ingest_run
        SET checkpoint = %s,
            rows_committed = %s,
            status = 'COMPLETE',
            updated_at = now(),
            completed_at = now(),
            error_text = NULL
        WHERE run_id = %s
        """,
        (checkpoint, rows_committed, run_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"missing trademark ingest run: {run_id}")


def fail_ingest_run(*, run_id: uuid.UUID, error_text: str) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE acquisition.global_trademark_ingest_run
                SET status = 'FAILED',
                    updated_at = now(),
                    error_text = %s
                WHERE run_id = %s
                """,
                (error_text[:4000], run_id),
            )
        conn.commit()
