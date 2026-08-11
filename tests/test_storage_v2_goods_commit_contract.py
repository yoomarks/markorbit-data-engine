from pathlib import Path

from app.cn.storage_v2_goods_commit import commit_compaction


def test_single_process_commit_entrypoint_is_importable():
    assert callable(commit_compaction)


def test_commit_wrapper_never_starts_persistent_worker():
    source = Path("scripts/commit-cn-goods-history.ps1").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "docker compose run --rm --no-deps" in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose up -d worker" not in lowered
    assert "docker compose stop worker" in lowered
    assert "$pythonargs" in lowered
    assert "app.cn.storage_v2_goods_commit" in source


def test_commit_path_keeps_old_wide_table_until_post_exchange_validation():
    source = Path("app/cn/storage_v2_goods_commit.py").read_text(encoding="utf-8")
    exchange = source.index("EXCHANGE TABLES")
    validation = source.index("validation_error")
    drop = source.index("DROP TABLE")
    assert exchange < validation < drop
