from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.db import postgres_conn


CN_INGESTION_LOCK_NAME = "markorbit:cn:package-ingestion"
INTERRUPTED_MESSAGE = (
    "Recovered automatically: the previous CN ingestion process ended before "
    "the package reached SUCCESS or FAILED. Partial stage/published rows will be "
    "cleaned before the package is replayed from the authoritative ZIP."
)


@contextmanager
def cn_ingestion_guard() -> Iterator[bool]:
    """Hold one PostgreSQL session advisory lock for a whole CN ingest cycle.

    The lock is session-scoped, so PostgreSQL releases it automatically if the
    API container, Python process, Docker Desktop, or host machine stops. That
    makes a leftover PROCESSING package distinguishable from a live ingestion
    without relying on a time-based stale threshold (real packages may take
    hours to finish).
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)::bigint) AS acquired",
                (CN_INGESTION_LOCK_NAME,),
            )
            acquired = bool(cur.fetchone()["acquired"])

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
                        (CN_INGESTION_LOCK_NAME,),
                    )
            except Exception:
                # Closing the PostgreSQL session also releases a session advisory
                # lock, so recovery remains safe even if explicit unlock fails.
                pass


def recover_interrupted_cn_ingestions() -> list[dict[str, object]]:
    """Convert orphaned PROCESSING packages into retryable INTERRUPTED work.

    Call only while cn_ingestion_guard is held. With the exclusive advisory lock
    acquired, no guard-aware CN ingestion is alive; therefore any remaining
    PROCESSING package was abandoned by a terminated process and can be safely
    retried. Source-rank ordering later guarantees an older interrupted base
    partition is replayed before newer REGISTERED packages.
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.source_package
                SET status = 'INTERRUPTED',
                    error_message = %s,
                    last_seen_at = now()
                WHERE jurisdiction = 'CN'
                  AND status = 'PROCESSING'
                RETURNING package_id, file_name, source_rank
                """,
                (INTERRUPTED_MESSAGE,),
            )
            recovered = [dict(row) for row in cur.fetchall()]

            if recovered:
                cur.execute(
                    """
                    UPDATE control.job_run
                    SET status = 'INTERRUPTED',
                        finished_at = COALESCE(finished_at, now()),
                        error_message = COALESCE(error_message, %s)
                    WHERE job_type = 'CN_PACKAGE_INGESTION'
                      AND status = 'RUNNING'
                    """,
                    (INTERRUPTED_MESSAGE,),
                )
        conn.commit()
    return recovered
