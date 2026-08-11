from pathlib import Path

from app.cn.storage_v2_goods_commit import DROP_QUERY_SETTINGS, commit_compaction


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


def test_large_table_drop_override_is_scoped_to_commit_query():
    source = Path("app/cn/storage_v2_goods_commit.py").read_text(encoding="utf-8")
    assert DROP_QUERY_SETTINGS == {"max_table_size_to_drop": 0}
    assert "settings=DROP_QUERY_SETTINGS" in source
    assert "force_drop_table" not in source
    assert "max_table_size_to_drop>" not in source


def test_commit_supports_guarded_pending_drop_resume():
    source = Path("app/cn/storage_v2_goods_commit.py").read_text(encoding="utf-8")
    assert "def _resume_pending_drop" in source
    assert "_validate_post_exchange(client)" in source
    assert "resumed_pending_drop" in source
