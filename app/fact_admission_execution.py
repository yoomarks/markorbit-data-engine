from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Iterator

from app.db import postgres_conn


class ExecutionAlreadyRunning(RuntimeError):
    pass


def _advisory_lock_key(scope: str) -> int:
    cleaned = scope.strip()
    if not cleaned:
        raise ValueError("execution lock scope is required")
    raw = int.from_bytes(hashlib.sha256(cleaned.encode("utf-8")).digest()[:8], "big")
    return raw - (1 << 64) if raw >= (1 << 63) else raw


@contextmanager
def fact_admission_execution_lock(scope: str) -> Iterator[None]:
    """Serialize one fact-admission scope with a session-scoped PostgreSQL lock."""
    lock_key = _advisory_lock_key(scope)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (lock_key,))
            row = cur.fetchone()
            if not row or not bool(row["locked"]):
                raise ExecutionAlreadyRunning(
                    f"fact admission is already running for scope: {scope}"
                )
        try:
            yield
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s) AS unlocked", (lock_key,))
            conn.commit()
