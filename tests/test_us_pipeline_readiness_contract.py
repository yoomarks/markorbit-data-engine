from pathlib import Path


def test_pipeline_readiness_is_read_only_and_never_executes_mutations() -> None:
    source = Path("app/us/pipeline_readiness.py").read_text(encoding="utf-8")
    assert 'READINESS_VERSION = "US_PIPELINE_READINESS_V1"' in source
    assert "build_preflight" in source
    assert "schema_state" in source
    assert "build_replay_plan" in source
    assert "build_reset_plan" in source
    assert "build_acceptance_audit" in source
    assert "RESET_RECOVERABLE_REPLAY_BLOCKERS" in source
    assert '"CLEAN_REBUILD_REQUIRED"' in source
    assert '"ACCEPTANCE_FAILED"' in source
    assert '"ACCEPTED"' in source

    forbidden = (
        "ensure_us_m1_schema",
        "execute_replay",
        "apply_staging",
        "apply_reset",
        "register_us_package",
        "ingest_us_package",
        "TRUNCATE",
        "UPDATE control.source_package",
        "DELETE FROM",
    )
    for token in forbidden:
        assert token not in source


def test_readiness_reset_route_is_dry_run_only() -> None:
    source = Path("app/us/pipeline_readiness.py").read_text(encoding="utf-8")
    reset_section = source[
        source.index('"CLEAN_REBUILD_REQUIRED"') : source.index(
            '"PIPELINE_BLOCKED"', source.index('"CLEAN_REBUILD_REQUIRED"')
        )
    ]
    assert "reset-us-clean-rebuild.ps1" in reset_section
    assert "-Apply" not in reset_section
    assert "RESET-US-M1.3" not in reset_section


def test_readiness_acceptance_failure_never_auto_routes_to_reset() -> None:
    source = Path("app/us/pipeline_readiness.py").read_text(encoding="utf-8")
    failure_section = source[source.index('"ACCEPTANCE_FAILED"') :]
    assert "INVESTIGATE_ACCEPTANCE_FAILURE" in failure_section
    assert "audit-us-real-data.ps1" in failure_section
    assert "reset-us-clean-rebuild.ps1" not in failure_section


def test_pipeline_status_wrapper_is_worker_guarded_and_read_only() -> None:
    source = Path("scripts/status-us-pipeline.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "deterministic US pipeline snapshot" in source
    assert 'foreach ($service in @("postgres", "clickhouse"))' in source
    assert '"python", "-m", "app.us.pipeline_readiness"' in source
    assert "--deep-source-test" in source
    assert "--verify-source-files" in source
    assert "next_action.command" in source
    assert "reports" in source

    forbidden = (
        "-Apply",
        "RESET-US-M1.3",
        "run-us.ps1",
        "retry-us.ps1",
        "replay-us-deterministic.ps1 -Apply",
        "reset-us-clean-rebuild.ps1 -Apply",
    )
    for token in forbidden:
        assert token not in source
