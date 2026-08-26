from pathlib import Path


def test_final_checkpoint_uses_spill_capable_clickhouse_profile():
    source = Path("app/cn/final_checkpoint.py").read_text(encoding="utf-8")
    assert "clickhouse_execution_settings" in source
    assert 'join_algorithm="grace_hash"' in source
    assert "grace_hash_join_initial_buckets=32" in source
    assert "send_receive_timeout=3600" in source
