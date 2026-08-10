from contextlib import contextmanager

import clickhouse_connect
import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


POSTGRES_SESSION_OPTIONS = (
    "-c lock_timeout=15s "
    "-c idle_in_transaction_session_timeout=60s"
)


@contextmanager
def postgres_conn():
    settings = get_settings()
    with psycopg.connect(
        settings.postgres_dsn,
        row_factory=dict_row,
        options=POSTGRES_SESSION_OPTIONS,
    ) as conn:
        yield conn


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
    # ClickHouse 24.8 does not support grace_hash for every JOIN
    # strictness/storage combination used by all Data Engine domains. Keep the
    # normal client on ClickHouse's default algorithm and opt in only for the
    # CN full-corpus replay worker through its container environment.
    if settings.clickhouse_join_algorithm:
        query_settings["join_algorithm"] = settings.clickhouse_join_algorithm
        if settings.clickhouse_join_algorithm == "grace_hash":
            query_settings["grace_hash_join_initial_buckets"] = (
                settings.clickhouse_grace_hash_join_initial_buckets
            )

    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_http_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
        settings=query_settings,
    )
