from pathlib import Path


def test_clickhouse_client_has_large_package_spill_controls():
    config = Path("app/config.py").read_text(encoding="utf-8")
    db = Path("app/db.py").read_text(encoding="utf-8")
    jobs = Path("app/jobs.py").read_text(encoding="utf-8")
    replay = Path("scripts/replay-cn-full.ps1").read_text(encoding="utf-8")
    cn_resource = Path("app/cn/resource_client.py").read_text(encoding="utf-8")
    m16 = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")

    assert "clickhouse_max_threads: int = 4" in config
    assert "clickhouse_external_group_by_bytes: int = 536_870_912" in config
    assert "clickhouse_external_sort_bytes: int = 536_870_912" in config
    assert 'clickhouse_join_algorithm: str = ""' in config
    assert "clickhouse_grace_hash_join_initial_buckets: int = 32" in config
    assert "clickhouse_send_receive_timeout: int = 300" in config

    assert '"max_threads": settings.clickhouse_max_threads' in db
    assert '"max_bytes_before_external_group_by"' in db
    assert "settings.clickhouse_external_group_by_bytes" in db
    assert '"max_bytes_before_external_sort"' in db
    assert "settings.clickhouse_external_sort_bytes" in db
    assert "clickhouse_execution_settings(" in db
    assert 'overrides.get("join_algorithm") or settings.clickhouse_join_algorithm' in db
    assert 'query_settings["join_algorithm"] = join_algorithm' in db
    assert 'if join_algorithm == "grace_hash":' in db
    assert 'query_settings["grace_hash_join_initial_buckets"]' in db
    assert "send_receive_timeout=send_receive_timeout" in db

    # The full replay wrapper keeps its explicit environment profile, while the
    # core CN ingestion path now applies the same profile for Admin/API/worker use.
    assert '"CLICKHOUSE_JOIN_ALGORITHM=grace_hash"' in replay
    assert '"CLICKHOUSE_GRACE_HASH_JOIN_INITIAL_BUCKETS=32"' in replay
    assert '"CLICKHOUSE_SEND_RECEIVE_TIMEOUT=3600"' in replay
    assert 'CN_JOIN_ALGORITHM = "grace_hash"' in jobs
    assert "CN_GRACE_HASH_JOIN_INITIAL_BUCKETS = 32" in jobs
    assert "CN_CLICKHOUSE_SEND_RECEIVE_TIMEOUT = 3600" in jobs
    assert "with clickhouse_execution_settings(" in jobs

    # CN aggregation has a hard per-query envelope below the container's observed
    # ~14 GiB ceiling, spills earlier, and uses one processing lane.
    assert "CN_MAX_THREADS = 1" in cn_resource
    assert "CN_MAX_MEMORY_USAGE = 8_589_934_592" in cn_resource
    assert "CN_EXTERNAL_GROUP_BY_BYTES = 67_108_864" in cn_resource
    assert "CN_EXTERNAL_SORT_BYTES = 67_108_864" in cn_resource
    assert '"max_memory_usage": CN_MAX_MEMORY_USAGE' in cn_resource
    assert '"max_bytes_before_external_group_by": CN_EXTERNAL_GROUP_BY_BYTES' in cn_resource
    assert '"max_bytes_before_external_sort": CN_EXTERNAL_SORT_BYTES' in cn_resource
    assert "cn_resource_client" in m16
    assert "legacy.clickhouse_client = lambda: cn_resource_client(" in m16
    assert "goods.clickhouse_client = lambda: cn_resource_client(" in m16
    assert "party.clickhouse_client = lambda: cn_resource_client(" in m16

    # GOODS uses the tighter budget proven necessary by the 8 GiB aggregation
    # failure; CASE/PARTY retain the existing 100k whole-application budget.
    assert "CN_GOODS_CHUNK_ROWS = 10_000" in m16
    assert "CN_CASE_CHUNK_ROWS = 100_000" in m16
    assert "CN_PARTY_CHUNK_ROWS = 100_000" in m16
    assert '"GOODS_LIFECYCLE"' in m16
    assert '"CASE_MATERIALIZE"' in m16
    assert '"PARTY_MATERIALIZE"' in m16
    assert '"LEGACY_SNAPSHOT_PERSIST"' in m16
