from pathlib import Path


def test_us_source_staging_is_file_only_and_explicit_apply() -> None:
    source = Path("app/us/stage_sources.py").read_text(encoding="utf-8")
    assert 'STAGING_VERSION = "US_SOURCE_STAGING_V1"' in source
    assert '"--apply"' in source
    assert 'destination.open("xb")' in source
    assert "os.fsync" in source
    assert "Archive source changed after preflight" in source
    assert "Refusing to overwrite existing staging destination" in source
    assert "postflight" in source
    assert "archive_staging_required_count" in source

    forbidden = (
        "postgres_conn",
        "clickhouse_client",
        "register_us_package",
        "ingest_us_package",
        "update_package_status",
        "ensure_us_m1_schema",
        "shutil.move",
        "os.replace",
    )
    for token in forbidden:
        assert token not in source


def test_us_source_staging_script_is_dry_run_by_default_and_worker_guarded() -> None:
    source = Path("scripts/stage-us-replay-sources.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert '[switch]$Apply' in source
    assert 'if ($Apply)' in source
    assert '$args += "--apply"' in source
    assert "Dry run only" in source
    assert "ExpectedHistoryParts" in source
    assert "reports" in source

    forbidden = (
        "run-us.ps1",
        "retry-us.ps1",
        "apply-us-m1-schema.ps1",
        "register_us_package",
        "ingest_us_package",
    )
    for token in forbidden:
        assert token not in source
