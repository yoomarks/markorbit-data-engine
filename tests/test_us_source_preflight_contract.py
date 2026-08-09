from pathlib import Path


def test_us_source_preflight_module_is_database_and_mutation_free() -> None:
    source = Path("app/us/source_preflight.py").read_text(encoding="utf-8")
    assert 'PREFLIGHT_VERSION = "US_SOURCE_PREFLIGHT_V1"' in source
    assert "historical_part_completeness" in source
    assert "DAILY_PACKAGE_NOT_AFTER_HISTORICAL_BASELINE" in source
    assert "SEMANTIC_PARTITION_SHA_CONFLICT" in source
    assert "needs_staging_from_archive" in source
    assert "--deep-source-test" in source
    assert "--expected-history-parts" in source

    forbidden = (
        "postgres_conn",
        "clickhouse_client",
        "register_us_package",
        "ingest_us_package",
        "update_package_status",
        "ensure_us_m1_schema",
        "shutil.move",
        "shutil.copy",
    )
    for token in forbidden:
        assert token not in source


def test_us_source_preflight_script_is_read_only_and_worker_guarded() -> None:
    source = Path("scripts/preflight-us-source-replay.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert '"python", "-m", "app.us.source_preflight"' in source
    assert "--expected-history-parts" in source
    assert "--deep-source-test" in source
    assert "safe_to_replay" in source
    assert "reports" in source

    forbidden = (
        "run-us.ps1",
        "retry-us.ps1",
        "apply-us-m1-schema.ps1",
        "scan_us_incoming",
        "register_us_package",
        "ingest_us_package",
    )
    for token in forbidden:
        assert token not in source
