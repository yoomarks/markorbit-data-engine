from pathlib import Path


def test_replay_executor_is_plan_ordered_stop_on_failure_and_explicit_apply() -> None:
    source = Path("app/us/replay_executor.py").read_text(encoding="utf-8")
    assert 'REPLAY_EXECUTOR_VERSION = "US_DETERMINISTIC_REPLAY_V1"' in source
    assert "build_preflight" in source
    assert "out_of_order_success_package" in source
    assert "successful_package_requires_m13_replay" in source
    assert "pending_source_requires_archive_staging" in source
    assert "registered_us_package_not_in_source_plan" in source
    assert "registered_source_rank_order_violation" in source
    assert "recover_interrupted_us_ingestions" in source
    assert "us_ingestion_guard" in source
    assert "register_us_package" in source
    assert "ingest_us_package" in source
    assert '"--apply"' in source
    assert '"--all"' in source
    assert '"--max-packages"' in source
    assert '"acceptance_required_after_complete": True' in source


def test_replay_executor_does_not_auto_stage_or_reset_sources_or_database() -> None:
    source = Path("app/us/replay_executor.py").read_text(encoding="utf-8")
    forbidden = (
        "apply_staging",
        "stage_sources",
        "shutil.copy",
        "shutil.move",
        "DROP TABLE",
        "TRUNCATE",
        "reset-",
    )
    for token in forbidden:
        assert token not in source


def test_replay_wrapper_is_dry_run_by_default_worker_guarded_and_acceptance_aware() -> None:
    source = Path("scripts/replay-us-deterministic.ps1").read_text(encoding="utf-8")
    summary_source = Path("app/us/replay_summary.py").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert 'foreach ($service in @("postgres", "clickhouse"))' in source
    assert '[switch]$Apply' in source
    assert '[switch]$All' in source
    assert 'assert-domain-apply-gate.ps1' in source
    assert '-TargetDomain "US_APPLICATION"' in source
    assert "--max-packages" in source
    assert "Dry run only" in source
    assert "audit-us-real-data.ps1" in source
    assert "app.us.replay_summary" in source
    assert 'SUMMARY_VERSION = "US_REPLAY_SUMMARY_V1"' in summary_source
    assert 'summary["apply_one_package_ok"]' in summary_source


def test_us_repository_exposes_replay_registry_without_changing_registration_contract() -> None:
    source = Path("app/us/repository.py").read_text(encoding="utf-8")
    assert "def list_us_replay_registry" in source
    assert "profile, schema_version, archived_path" in source
    assert "WHERE jurisdiction = 'US'" in source
    assert "ON CONFLICT (sha256)" in source
    assert "US_SCHEMA_VERSION" in source
