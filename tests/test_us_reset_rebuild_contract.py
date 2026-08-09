from pathlib import Path


def test_reset_module_is_us_only_manifest_first_and_confirmation_guarded() -> None:
    wrapper = Path("app/us/reset_rebuild.py").read_text(encoding="utf-8")
    core = Path("app/us/reset_rebuild_core.py").read_text(encoding="utf-8")
    combined = wrapper + "\n" + core

    assert '_core.RESET_VERSION = "US_CLEAN_REBUILD_RESET_V2"' in wrapper
    assert '_core.RESET_CONFIRMATION = "RESET-US-M1.4"' in wrapper
    assert '"us_case_observation_history"' in wrapper
    assert "confirmation != RESET_CONFIRMATION" in core
    assert '"--confirm"' in core
    assert "_write_manifest(raw_root, plan)" in core
    assert "_truncate_us_fact_tables()" in core
    apply_body = core[core.index("def apply_reset(") : core.index("def main()")]
    assert apply_body.index("_write_manifest(raw_root, plan)") < apply_body.index(
        "_truncate_us_fact_tables()"
    )
    assert "TRUNCATE TABLE markorbit_facts.{table} SYNC" in core
    assert "for table in ALL_TABLE_KEYS" in core
    assert "WHERE package_id = %s" in core
    assert "AND jurisdiction = 'US'" in core
    assert "profile = '{}'::jsonb" in core
    assert "status = 'REGISTERED'" in core
    assert "schema_version = %s" in core
    assert "archived_path = NULL" in core
    assert "processed_at = NULL" in core
    assert "error_message = NULL" in core

    forbidden = (
        "DELETE FROM control.source_package",
        "TRUNCATE TABLE markorbit_facts.cn_",
        "DROP TABLE",
        "CASCADE",
        "apply_staging",
        "execute_replay",
    )
    for token in forbidden:
        assert token not in combined


def test_reset_wrapper_requires_worker_off_explicit_apply_and_exact_confirmation() -> None:
    source = Path("scripts/reset-us-clean-rebuild.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert 'foreach ($service in @("postgres", "clickhouse"))' in source
    assert '[switch]$Apply' in source
    assert '$RequiredConfirmation = "RESET-US-M1.4"' in source
    assert "$ConfirmReset -cne $RequiredConfirmation" in source
    assert '$args += @("--apply", "--confirm", $RequiredConfirmation)' in source
    assert "Dry run only" in source
    assert "Pre-reset evidence manifest" in source
    assert "replay-us-deterministic.ps1" in source


def test_reset_plan_requires_safe_preflight_and_staged_sources() -> None:
    wrapper = Path("app/us/reset_rebuild.py").read_text(encoding="utf-8")
    core = Path("app/us/reset_rebuild_core.py").read_text(encoding="utf-8")
    assert "build_preflight" in core
    assert "source_preflight_not_safe" in core
    assert "archive_sources_must_be_staged_before_reset" in core
    assert "registered_us_package_not_in_source_plan" in core
    assert "registry_source_identity_mismatch" in core
    assert "source_descriptor_became_unknown" in core
    assert "manifest_fingerprint" in core
    assert "durable case-observation tables" in wrapper
