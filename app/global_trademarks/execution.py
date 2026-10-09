from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.fact_admission_execution import (
    ExecutionAlreadyRunning as ExecutionAlreadyRunning,
    fact_admission_execution_lock,
)
from app.global_trademarks.migrations import assert_global_trademark_schema


@contextmanager
def global_trademark_execution_lock(scope: str) -> Iterator[None]:
    """Hold a session-scoped PostgreSQL advisory lock for one ingestion scope.

    The lock connection stays open while loaders use their own transactional
    connections. This is intentionally small and local: it prevents accidental
    duplicate execution on the current single-host deployment without introducing
    a distributed lease/heartbeat system prematurely.
    """
    assert_global_trademark_schema()
    with fact_admission_execution_lock(scope):
        yield
