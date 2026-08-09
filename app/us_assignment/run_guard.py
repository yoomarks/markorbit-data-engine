from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.db import postgres_conn
from app.us_assignment import ASSIGNMENT_JURISDICTION


ASSIGNMENT_LOCK_NAME = "markorbit:us:assignment-ingestion"
INTERRUPTED_MESSAGE = (
    "Recovered automatically: the previous US assignment ingestion ended before "
    "SUCCESS/FAILED. Package-scoped assignment observations will be removed and replayed."
)


@contextmanager
def assignment_ingestion_guard() -> Iterator[bool]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)::bigint) AS acquired",
                (ASSIGNMENT_LOCK_NAME,),
            )
            acquired = bool(cur.fetchone()["acquired"])
        conn.commit()
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s)::bigint)",
                        (ASSIGNMENT_LOCK_NAME,),
                    )
                conn.commit()
            except Exception:
                pass


def recover_interrupted_assignment_ingestions() -> list[dict[str, object]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.source_package
                SET status = 'INTERRUPTED', error_message = %s, last_seen_at = now()
                WHERE jurisdiction = %s AND status = 'PROCESSING'
                RETURNING package_id, file_name, source_rank
                """,
                (INTERRUPTED_MESSAGE, ASSIGNMENT_JURISDICTION),
            )
            recovered = [dict(row) for row in cur.fetchall()]
            if recovered:
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET status = 'INTERRUPTED', finished_at = COALESCE(finished_at, now()),
                        error_message = COALESCE(error_message, %s)
                    WHERE job_type = 'US_ASSIGNMENT_PACKAGE_INGESTION' AND status = 'RUNNING'
                    """,
                    (INTERRUPTED_MESSAGE,),
                )
        conn.commit()
    return recovered
