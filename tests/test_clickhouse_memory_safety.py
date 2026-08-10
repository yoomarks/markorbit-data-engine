from pathlib import Path


def test_clickhouse_client_has_large_package_spill_controls():
    config = Path("app/config.py").read_text(encoding="utf-8")
    db = Path("app/db.py").read_text(encoding="utf-8")
    replay = Path("scripts/replay-cn-full.ps1").read_text(encoding="utf-8")

    assert "clickhouse_max_threads: int = 4" in config
    assert "clickhouse_external_group_by_bytes: int = 536_870_912" in config
    assert "clickhouse_external_sort_bytes: int = 536_870_912" in config
    assert 'clickhouse_join_algorithm: str = ""' in config
    assert "clickhouse_grace_hash_join_initial_buckets: int = 32" in config

    assert '"max_threads": settings.clickhouse_max_threads' in db
    assert '"max_bytes_before_external_group_by"' in db
    assert "settings.clickhouse_external_group_by_bytes" in db
    assert '"max_bytes_before_external_sort"' in db
    assert "settings.clickhouse_external_sort_bytes" in db
    assert "if settings.clickhouse_join_algorithm:" in db
    assert 'query_settings["join_algorithm"] = settings.clickhouse_join_algorithm' in db
    assert 'if settings.clickhouse_join_algorithm == "grace_hash":' in db
    assert 'query_settings["grace_hash_join_initial_buckets"]' in db

    assert '"CLICKHOUSE_JOIN_ALGORITHM=grace_hash"' in replay
    assert '"CLICKHOUSE_GRACE_HASH_JOIN_INITIAL_BUCKETS=32"' in replay
