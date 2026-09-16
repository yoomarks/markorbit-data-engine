from pathlib import Path
import re


SCRIPT = Path("scripts/audit-us-secondary-family-hot-placement.ps1")
EXPECTED_TABLES = {
    "us_assignment_assignee_history",
    "us_assignment_assignor_history",
    "us_assignment_property_history",
    "us_assignment_record_history",
    "us_ttab_docket_history",
    "us_ttab_party_history",
    "us_ttab_proceeding_history",
    "us_ttab_property_history",
}


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_secondary_family_audit_freezes_exact_expected_tables() -> None:
    text = _text()
    block = text.split("$script:ExpectedTables = @(", 1)[1].split("\n)", 1)[0]
    observed = set(re.findall(r"'((?:us_assignment|us_ttab)_[a-z0-9_]+)'", block))
    assert observed == EXPECTED_TABLES


def test_secondary_family_audit_is_read_only_and_has_no_apply_surface() -> None:
    text = _text().lower()
    assert "[switch]$apply" not in text
    assert "--multiquery" not in text
    for forbidden in (
        "new-vhd",
        "wsl.exe --mount",
        "wsl.exe --shutdown",
        "docker compose up",
        "insert into",
        "create table",
        "alter table",
        "optimize table",
        "move partition",
        "drop table",
    ):
        assert forbidden not in text
    assert "read_only = $true" in text
    assert "mutation_performed = $false" in text


def test_secondary_family_audit_requires_existing_target_runtime() -> None:
    text = _text()
    assert "Get-RunningWslNames" in text
    assert "--list', '--running', '--quiet" in text
    assert "is not already running; refusing to start it" in text
    assert "$script:TargetDistro = 'MarkOrbit-ClickHouse'" in text
    assert "$script:TargetDisk = 'hot_us'" in text
    assert "$script:TargetPolicy = 'hot_us_only'" in text


def test_secondary_family_audit_refuses_application_growth_inference() -> None:
    text = _text()
    assert "future_growth_projection_performed = $false" in text
    assert "future_growth_sufficiency_claimed = $false" in text
    assert "application_amplification_reuse_authorized = $false" in text
    assert "CURRENT_ACCEPTED_SOURCE_BYTES_ONLY_NO_GROWTH_PROJECTION" in text
    assert "0.30" in text
    assert "0.20" in text


def test_secondary_family_audit_has_deterministic_placement_decisions() -> None:
    text = _text()
    assert "US_SECONDARY_FAMILY_PLACEMENT_DESIGN_REQUIRED" in text
    assert "US_SECONDARY_FAMILY_PLACEMENT_REVIEW_REQUIRED" in text
    assert "US_SECONDARY_FAMILY_TARGET_PRESENT" in text
    assert "DESIGN_TARGET_SCHEMA_AND_MIGRATION_PLAN" in text
    assert "REVIEW_TARGET_PLACEMENT_DRIFT" in text
    assert "TARGET_FAMILY_ACCEPTANCE_REVIEW" in text


def test_secondary_family_queries_are_system_metadata_only() -> None:
    text = _text()
    assert "Assert-ReadOnlyMetadataSql" in text
    for system_table in (
        "system.tables",
        "system.parts",
        "system.disks",
        "system.storage_policies",
    ):
        assert system_table in text


def test_secondary_family_audit_exposes_non_runtime_contract_mode() -> None:
    text = _text()
    assert "[switch]$ContractOnly" in text
    assert "if ($ContractOnly)" in text
    assert "US_SECONDARY_FAMILY_HOT_PLACEMENT_CONTRACT_PASS" in text
    contract_block = text.split("if ($ContractOnly)", 1)[1].split("Assert-ExactMain 'entry'", 1)[0]
    assert "Invoke-SourceRows" not in contract_block
    assert "Invoke-TargetRows" not in contract_block
