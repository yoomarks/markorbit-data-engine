from pathlib import Path


def test_reset_module_is_us_only_manifest_first_and_confirmation_guarded() -> None:
    source = Path("app/us/reset_rebuild.py").read_text(encoding="utf-8")
    assert 'RESET_VERSION = "US_CLEAN_REBUILD_RESET_V1"' in source
    assert 'RESET_CONFIRMATION = "RESET-US-M1.3"' in source
    assert "confirmation != RESET_CONFIRMATION" in source
    assert '"--confirm"' in source
    assert "_write_manifest(raw_root, plan)" in source
    assert "_truncate_us_fact_tables()" in source
    apply_body = source[source.index("def apply_reset(") : source.index("def main()")]
    assert apply_body.index("_write_manifest(raw_root, plan)") < apply_body.index(
        "_truncate_us_fact_tables()"
    )
    assert "TRUNCATE TABLE markorbit_facts.{table} SYNC" in source
    assert "for table in ALL_TABLE_KEYS" in source
    assert "WHERE package_id = %s" in source
    assert "AND jurisdiction = 'US'" in source
    assert "profile = '{}'::jsonb" in source
    assert "status = 'REGISTERED'" in source
    assert "schema_version = %s" in source
    assert "archived_path = NULL" in source
    assert "processed_at = NULL" in source
    assert "error_message = NULL" in source

    forbidden = (
        "DELETE FROM control.source_package",
        "TRUNCATE TABLE markorbit_facts.cn_",
        "DROP TABLE",
        "CASCADE",
        "apply_staging",
        "execute_replay",
    )
    for token in forbidden:
        assert token not in source


def test_reset_wrapper_requires_worker_off_explicit_apply_and_exact_confirmation() -> None:
    source = Path("scripts/reset-us-clean-rebuild.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running -q worker" in source
    assert "Persistent worker is running" in source
    assert 'foreach ($service in @("postgres", "clickhouse"))' in source
    assert '[switch]$Apply' in source
    assert '$RequiredConfirmation = "RESET-US-M1.3"' in source
    assert "$ConfirmReset -cne $RequiredConfirmation" in source
    assert '$args += @("--apply", "--confirm", $RequiredConfirmation)' in source
    assert "Dry run only" in source
    assert "Pre-reset evidence manifest" in source
    assert "replay-us-deterministic.ps1" in source


def test_reset_plan_requires_safe_preflight_and_staged_sources() -> None:
    source = Path("app/us/reset_rebuild.py").read_text(encoding="utf-8")
    assert "build_preflight" in source
    assert "source_preflight_not_safe" in source
    assert "archive_sources_must_be_staged_before_reset" in source
    assert "registered_us_package_not_in_source_plan" in source
    assert "registry_source_identity_mismatch" in source
    assert "source_descriptor_became_unknown" in source
    assert "manifest_fingerprint" in source
