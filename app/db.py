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
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_http_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )
