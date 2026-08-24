from __future__ import annotations

from app.cn import ingest as legacy
from app.cn.resource_client import cn_resource_client
from app.cn.validate_fixture import main as fixture_main
from app.db import clickhouse_execution_settings
from app.jobs import (
    CN_CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
    CN_GRACE_HASH_JOIN_INITIAL_BUCKETS,
    CN_JOIN_ALGORITHM,
)


def main() -> None:
    """Run the non-empty CN fixture under the production M1.6 resource profile.

    The legacy fixture deliberately exercises the full publish SQL against the live
    accumulated current corpus. As that corpus grows, even a tiny synthetic package
    can require a large JOIN right side. Keep the fixture semantically identical while
    applying the same per-query envelope and disk-spilling JOIN profile used by real
    CN package ingestion.
    """
    original_client = legacy.clickhouse_client
    legacy.clickhouse_client = lambda: cn_resource_client(original_client)
    try:
        with clickhouse_execution_settings(
            join_algorithm=CN_JOIN_ALGORITHM,
            grace_hash_join_initial_buckets=CN_GRACE_HASH_JOIN_INITIAL_BUCKETS,
            send_receive_timeout=CN_CLICKHOUSE_SEND_RECEIVE_TIMEOUT,
        ):
            fixture_main()
    finally:
        legacy.clickhouse_client = original_client


if __name__ == "__main__":
    main()
