from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review-us-secondary-family-target-schema-apply.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "us-secondary-family-target-schema-apply-review-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_review_has_no_apply_surface_and_never_consumes_authority() -> None:
    source = text()
    assert "[switch]$Apply" not in source
    assert "[switch]$Resume" not in source
    for marker in (
        "read_only=$true",
        "mutation_performed=$false",
        "schema_apply_executed=$false",
        "authority_consumed=$false",
        "apply_authorized=$false",
        "authority_consumed=False",
        "schema_apply_executed=False",
        "apply_authorized=False",
    ):
        assert marker in source

def test_review_is_bound_to_exact_accepted_design_receipt() -> None:
    source = text()
    for marker in (
        "23fbe1fcf79acaabfb19b0a73e60302cbb3680de924cb7848297ab7b8e57bd50",
        "6f9eac978395520d23de5cfbbdcfee86b0bd5aba",
        "US_SECONDARY_FAMILY_MIGRATION_DESIGN_V1",
        "US_SECONDARY_FAMILY_MIGRATION_DESIGN_READY",
        "TARGET_SCHEMA_APPLY_REVIEW",
        "Accepted design receipt SHA mismatch",
    ):
        assert marker in source


def test_fresh_design_is_recomputed_on_current_exact_main() -> None:
    source = text()
    for marker in (
        "design-us-secondary-family-hot-migration.ps1",
        "Resolve-FreshDesignReceipt",
        "-ExpectedMainSha",
        "$ExpectedMainSha",
        "Fresh design main SHA drifted.",
        "target_tables_currently_absent",
        "recommended_30pct_current_footprint_fits",
    ):
        assert marker in source

def test_plan_freezes_exact_ddl_identity_and_order() -> None:
    source = text()
    for marker in (
        "Get-DesignTableIdentity",
        "Accepted/fresh target DDL identity drifted.",
        "target_create_table_query_sha256",
        "Target DDL SHA drifted:",
        "Sort-Object { [int]$_.migration_order }",
        "schema_apply_steps",
        "storage_policy = 'hot_us_only'",
        "Target DDL must fail closed on collision",
    ):
        assert marker in source


def test_plan_preflight_and_per_create_verification_are_frozen() -> None:
    source = text()
    for marker in (
        "all_eight_target_tables_absent",
        "hot_us_only_policy_identity_unchanged",
        "recommended_30pct_current_footprint_reserve_fits",
        "source_remains_authoritative_and_retained",
        "created_table_exact_schema_and_ddl_identity",
        "created_table_storage_policy_hot_us_only",
        "created_table_active_parts_zero",
        "no_copy_or_serving_action_started",
    ):
        assert marker in source

def test_partial_apply_failure_is_fail_closed_without_auto_drop() -> None:
    source = text()
    for marker in (
        "HALT_AND_FREEZE_PARTIAL_EMPTY_SCHEMA_STATE",
        "auto_drop_allowed=$false",
        "silent_continue_allowed=$false",
        "ordinary_retry_allowed=$false",
        "remediation_review_required=$true",
        "created_empty_tables_must_be_reported=$true",
        "rollback_drop_allowed=$false",
    ):
        assert marker in source


def test_canonical_plan_hash_and_future_authority_token_are_deterministic() -> None:
    source = text()
    for marker in (
        "Get-CanonicalJson",
        "Get-StringSha256 $planCanonical",
        "GO #688 US secondary family target schema apply $PlanSha",
        "TARGET_SCHEMA_APPLY_AUTHORITY_REQUIRED",
        "required_authority_token",
        "authority_consumed=$false",
    ):
        assert marker in source

def test_review_never_executes_schema_or_data_mutation() -> None:
    source = text()
    forbidden = (
        "[switch]$Apply",
        "clickhouse-client --query 'CREATE",
        'clickhouse-client --query "CREATE',
        " INSERT INTO ",
        " ALTER TABLE ",
        " DROP TABLE ",
        " TRUNCATE TABLE ",
        " OPTIMIZE TABLE ",
        " MOVE PART ",
        "New-VHD ",
        "Resize-VHD ",
        "Mount-VHD ",
        "Dismount-VHD ",
        "--shutdown",
        "--unregister",
        "docker system prune",
        "docker volume rm",
    )
    for marker in forbidden:
        assert marker not in source


def test_contract_fixture_is_runtime_independent_and_checks_token_format() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split("if ($ContractOnly)", 1)[0]
    assert "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_CONTRACT_PASS" in fixture
    assert "Canonical plan hash is not deterministic." in fixture
    assert "Authority token format drifted." in fixture
    assert "Invoke-NativeText 'docker'" not in fixture
    assert "wsl.exe" not in fixture.lower()

def test_workflow_runs_windows_ps51_and_python_contract_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in workflow
    assert "powershell.exe -NoProfile" in workflow
    assert "-ContractOnly" in workflow
    assert "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_CONTRACT_PASS" in workflow
    assert "ubuntu-latest" in workflow
    assert "test_us_secondary_family_target_schema_apply_review_contract.py" in workflow
    assert "concurrency:" in workflow
    assert "paths:" in workflow
