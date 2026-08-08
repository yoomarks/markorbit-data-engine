from pathlib import Path


def test_member_checkpoint_is_durable_and_stage_validated():
    source = Path("app/cn/checkpoint.py").read_text(encoding="utf-8")
    assert 'CHECKPOINT_VERSION = "CN_PACKAGE_MEMBER_CHECKPOINT_V1"' in source
    assert "FROM control.source_package_file" in source
    assert "validated_completed_member_names" in source
    assert "source_file NOT IN" in source
    assert "DELETE FROM control.source_package_file" in source
    assert "ROLE_STAGE_TABLE" in source


def test_m16_runtime_skips_only_completed_members_and_preserves_partial_recovery():
    source = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    assert "validated_completed_member_names(package_id)" in source
    assert "if member.internal_name in completed" in source
    assert "legacy.iter_package_members = iter_members_with_resume" in source
    assert "legacy.upsert_package_file = checkpoint_upsert" in source
    assert "legacy._cleanup_stage = checkpoint_cleanup_stage" in source
    assert "cleanup_uncheckpointed_stage" in source
    assert '"INTERRUPTED"' in source


def test_resumed_metrics_are_rebuilt_before_full_stage_cleanup():
    source = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    finalize_at = source.index("finalize_checkpoint_metrics(")
    full_cleanup_at = source.index("_LEGACY_CLEANUP_STAGE(package_uuid)", finalize_at)
    assert finalize_at < full_cleanup_at

    checkpoint = Path("app/cn/checkpoint.py").read_text(encoding="utf-8")
    assert 'corrected["role_counts"]' in checkpoint
    assert 'corrected["stage_counts"]' in checkpoint
    assert 'corrected["files"]' in checkpoint
    assert 'corrected["resume_checkpoint_members_reused"]' in checkpoint
