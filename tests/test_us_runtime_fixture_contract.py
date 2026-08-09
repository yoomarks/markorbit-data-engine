from pathlib import Path


def test_us_runtime_fixture_writes_checks_and_cleans_all_tables() -> None:
    source = Path("app/us/validate_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_M1.1_RUNTIME_FIXTURE"' in source
    assert "DIRECT_SERIAL" in source
    assert "MADRID_SERIAL" in source
    assert "partial_first_use_date" in source
    assert "event_description" in source
    assert "use_1a_filed" in source
    assert "madrid_66a_current" in source
    assert "_cleanup_package_outputs(package_id)" in source
    assert "residual_rows" in source


def test_us_m12_snapshot_fixture_checks_current_replacement_and_event_history() -> None:
    source = Path("app/us/validate_snapshot_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_M1.2_CHILD_SNAPSHOT_FIXTURE"' in source
    assert "SnapshotAwareUSBatchPublisher" in source
    assert '"owner_current": 0' in source
    assert '"classification_current": 0' in source
    assert '"statement_current": 0' in source
    assert '"event_history": 2' in source
    assert "tombstone_counts" in source
    assert "_cleanup_package_outputs(new_package_id)" in source
    assert "_cleanup_package_outputs(old_package_id)" in source


def test_us_m13_official_fact_fixture_checks_all_new_fact_families() -> None:
    source = Path("app/us/validate_official_fact_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_M1.3_OFFICIAL_FACT_FAMILIES_FIXTURE"' in source
    for table in (
        "us_correspondent_current",
        "us_design_search_current",
        "us_prior_registration_current",
        "us_foreign_application_current",
        "us_madrid_filing_current",
        "us_madrid_event_history",
    ):
        assert table in source
    assert "madrid_filing_request" in source
    assert "madrid_event_history" in source
    assert "_cleanup_package_outputs(package_id)" in source


def test_us_m13_acceptance_fixture_exercises_real_audit_queries_and_cleanup() -> None:
    source = Path("app/us/validate_acceptance_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_M1.3_REAL_DATA_ACCEPTANCE_FIXTURE"' in source
    assert "build_audit(verify_source_files=False)" in source
    assert 'report["status"] != "PASS_WITH_WARNINGS"' in source
    assert "history_success_count" in source
    assert "daily_success_count" in source
    assert "rank_boundary_ok" in source
    assert "source_lineage_rank_mismatches" in source
    assert "_cleanup_package_outputs(DAILY_PACKAGE_ID)" in source
    assert "_cleanup_package_outputs(HISTORY_PACKAGE_ID)" in source
    assert "_delete_packages()" in source


def test_us_deterministic_replay_fixture_runs_history_then_daily_and_cleans_up() -> None:
    source = Path("app/us/validate_replay_executor_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_DETERMINISTIC_REPLAY_EXECUTOR_FIXTURE"' in source
    assert "execute_replay" in source
    assert 'max_packages=1' in source
    assert 'max_packages=None' in source
    assert 'first["status"] != "PAUSED"' in source
    assert 'second["status"] != "COMPLETE"' in source
    assert "daily_current_case" in source
    assert "source_rank_order" in source
    assert "_cleanup_fixture_state(raw_root)" in source


def test_us_clean_rebuild_fixture_resets_and_replays_again() -> None:
    source = Path("app/us/validate_reset_rebuild_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_CLEAN_REBUILD_RESET_FIXTURE"' in source
    assert "apply_staging" in source
    assert "build_reset_plan" in source
    assert "apply_reset" in source
    assert "RESET_CONFIRMATION" in source
    assert '"all_11_tables_zero_after_reset": "PASS"' in source
    assert '"package_identity_preserved": "PASS"' in source
    assert '"post_reset_replay": "PASS"' in source
    assert "manifest_sha256" in source
    assert "_cleanup_fixture_state(raw_root)" in source


def test_us_pipeline_readiness_fixture_routes_to_terminal_accepted_state() -> None:
    source = Path("app/us/validate_pipeline_readiness_fixture.py").read_text(encoding="utf-8")
    assert '"contract": "US_PIPELINE_READINESS_FIXTURE"' in source
    assert "build_readiness" in source
    assert 'initial["state"] != "REPLAY_READY"' in source
    assert 'database_only["state"] != "SOURCE_VERIFICATION_REQUIRED"' in source
    assert 'accepted["state"] != "ACCEPTED"' in source
    assert 'accepted["ready"] is not True' in source
    assert 'accepted["next_action"]["code"] != "NONE"' in source
    assert "_cleanup_package_outputs" in source


def test_us_runtime_fixture_script_applies_schema_and_runs_all_fixtures() -> None:
    source = Path("scripts/validate-us-m1-fixture.ps1").read_text(encoding="utf-8")
    assert "apply-us-m1-schema.ps1" in source
    assert "python -m app.us.validate_fixture" in source
    assert "python -m app.us.validate_snapshot_fixture" in source
    assert "python -m app.us.validate_official_fact_fixture" in source


def test_ci_runs_us_fixtures_against_live_postgres_and_clickhouse() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "us-runtime-fixture:" in workflow
    assert "docker compose up -d --wait postgres clickhouse" in workflow
    assert "python -m app.us.validate_fixture" in workflow
    assert "python -m app.us.validate_snapshot_fixture" in workflow
    assert "python -m app.us.validate_official_fact_fixture" in workflow
    assert "python -m app.us.validate_acceptance_fixture" in workflow
    assert "python -m app.us.validate_replay_executor_fixture" in workflow
    assert "python -m app.us.validate_reset_rebuild_fixture" in workflow
    assert "python -m app.us.validate_pipeline_readiness_fixture" in workflow
    assert "docker compose down -v --remove-orphans" in workflow
