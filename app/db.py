from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

import clickhouse_connect
import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


POSTGRES_SESSION_OPTIONS = (
    "-c lock_timeout=15s "
    "-c idle_in_transaction_session_timeout=60s"
)
_POSTGRES_LOCK_TIMEOUT_OVERRIDE: ContextVar[str | None] = ContextVar(
    "markorbit_postgres_lock_timeout_override",
    default=None,
)
_CLICKHOUSE_EXECUTION_OVERRIDES: ContextVar[dict[str, int | str]] = ContextVar(
    "markorbit_clickhouse_execution_overrides",
    default={},
)


def _postgres_session_options() -> str:
    lock_timeout = _POSTGRES_LOCK_TIMEOUT_OVERRIDE.get()
    if lock_timeout is None:
        return POSTGRES_SESSION_OPTIONS
    return (
        f"-c lock_timeout={lock_timeout} "
        "-c idle_in_transaction_session_timeout=60s"
    )


@contextmanager
def postgres_conn():
    settings = get_settings()
    with psycopg.connect(
        settings.postgres_dsn,
        row_factory=dict_row,
        options=_postgres_session_options(),
    ) as conn:
        yield conn


@contextmanager
def postgres_execution_settings(*, lock_timeout: str = "") -> Iterator[None]:
    """Apply transaction-wait policy to Postgres connections in this execution context.

    Normal API/contact work keeps the 15-second lock timeout. Long-running CN
    package ingestion can opt into PostgreSQL's ``lock_timeout=0`` so a concurrent
    Entity Hub writer is queued instead of invalidating an otherwise deterministic
    package replay. PostgreSQL deadlock detection remains active independently.
    """
    if not lock_timeout:
        yield
        return
    if any(character.isspace() for character in lock_timeout):
        raise ValueError("lock_timeout must be a single PostgreSQL duration token")

    token = _POSTGRES_LOCK_TIMEOUT_OVERRIDE.set(lock_timeout)
    try:
        yield
    finally:
        _POSTGRES_LOCK_TIMEOUT_OVERRIDE.reset(token)


@contextmanager
def clickhouse_execution_settings(
    *,
    join_algorithm: str = "",
    grace_hash_join_initial_buckets: int = 0,
    send_receive_timeout: int = 0,
) -> Iterator[None]:
    """Apply resource-only ClickHouse settings to the current execution context.

    This lets CN ingestion use its proven disk-spilling JOIN profile regardless
    of whether it was started by PowerShell, Admin, API retry, or the persistent
    worker, without leaking that profile into US domains or unrelated requests.
    """
    overrides = dict(_CLICKHOUSE_EXECUTION_OVERRIDES.get())
    if join_algorithm:
        overrides["join_algorithm"] = join_algorithm
    if grace_hash_join_initial_buckets > 0:
        overrides["grace_hash_join_initial_buckets"] = int(
            grace_hash_join_initial_buckets
        )
    if send_receive_timeout > 0:
        overrides["send_receive_timeout"] = int(send_receive_timeout)
    token = _CLICKHOUSE_EXECUTION_OVERRIDES.set(overrides)
    try:
        yield
    finally:
        _CLICKHOUSE_EXECUTION_OVERRIDES.reset(token)


def clickhouse_client():
    settings = get_settings()
    query_settings = {
        # These are execution-resource controls only. They do not change
        # query semantics, identity, lifecycle, FINAL behavior, or lineage.
        "max_threads": settings.clickhouse_max_threads,
        "max_bytes_before_external_group_by": (
            settings.clickhouse_external_group_by_bytes
        ),
        "max_bytes_before_external_sort": settings.clickhouse_external_sort_bytes,
    }

    overrides = _CLICKHOUSE_EXECUTION_OVERRIDES.get()
    join_algorithm = str(
        overrides.get("join_algorithm") or settings.clickhouse_join_algorithm
    )
    grace_buckets = int(
        overrides.get("grace_hash_join_initial_buckets")
        or settings.clickhouse_grace_hash_join_initial_buckets
    )
    send_receive_timeout = int(
        overrides.get("send_receive_timeout")
        or settings.clickhouse_send_receive_timeout
    )

    # ClickHouse 24.8 does not support grace_hash for every JOIN
    # strictness/storage combination used by all Data Engine domains. Keep the
    # normal client on ClickHouse's default algorithm and opt in only through a
    # scoped execution profile (CN ingestion) or an explicit environment value.
    if join_algorithm:
        query_settings["join_algorithm"] = join_algorithm
        if join_algorithm == "grace_hash":
            query_settings["grace_hash_join_initial_buckets"] = grace_buckets

    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_http_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
        send_receive_timeout=send_receive_timeout,
        settings=query_settings,
    )
