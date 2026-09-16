from pathlib import Path
import shutil
import subprocess
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply-us-secondary-family-target-schema.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "us-secondary-family-target-schema-apply-executor-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_executor_has_explicit_contract_dryrun_and_apply_surfaces() -> None:
    source = text()
    for marker in (
        "[switch]$DryRun",
        "[switch]$Apply",
        "[switch]$ContractOnly",
        "[string]$AuthorityToken",
        "[string]$ReviewReceiptPath",
        "Choose exactly one of -DryRun or -Apply",
    ):
        assert marker in source


def test_exact_main_is_required_for_runtime_paths() -> None:
    source = text()
    assert "(git branch --show-current).Trim()" in source
    assert "$branch -ne 'main'" in source
    assert "git rev-parse origin/main" in source
    assert "Working tree must be clean" in source

def test_review_plan_is_recomputed_and_bound_exactly() -> None:
    source = text()
    for marker in (
        "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_V1",
        "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_PLAN_V1",
        "Get-CanonicalJson $plan",
        "Review plan SHA failed canonical recomputation",
        "Review main SHA is not the exact execution main",
        "Reviewed schema step count drifted",
    ):
        assert marker in source


def test_authority_token_is_issue_690_and_stale_688_is_rejected() -> None:
    source = text()
    assert 'GO #690 US secondary family target schema apply $PlanSha' in source
    assert "if($token -like 'GO #688*')" in source
    assert "$AuthorityToken -ne $requiredToken" in source
    assert "DryRun must not consume or accept AuthorityToken" in source


def test_all_eight_reviewed_tables_and_order_are_frozen() -> None:
    source = text()
    for table in (
        "us_assignment_record_history", "us_assignment_assignor_history",
        "us_assignment_assignee_history", "us_assignment_property_history",
        "us_ttab_proceeding_history", "us_ttab_party_history",
        "us_ttab_property_history", "us_ttab_docket_history",
    ):
        assert table in source
    assert "Reviewed table order drifted" in source

def test_journal_is_persisted_before_first_create() -> None:
    source = text()
    journal_pos = source.index("Save-Journal $script:ActiveJournal $script:ActiveJournalPath")
    create_pos = source.index("Invoke-TargetCreate ([string]$step.ddl) $table")
    assert journal_pos < create_pos
    for marker in (
        "AUTHORIZED_NOT_STARTED",
        "authority_consumed=$true",
        "authority_token_sha256",
        "ordinary_retry_allowed=$false",
        "auto_drop_allowed=$false",
        "Assert-NoExistingJournal",
    ):
        assert marker in source


def test_create_executes_only_reviewed_fail_closed_ddl() -> None:
    source = text()
    create_fn = source.split("function Invoke-TargetCreate", 1)[1].split("function Invoke-SourceRows", 1)[0]
    assert "^CREATE TABLE markorbit_facts" in create_fn
    assert "IF\\s+NOT\\s+EXISTS" in create_fn
    for forbidden in ("ALTER|DROP|TRUNCATE|INSERT|OPTIMIZE|MOVE",):
        assert forbidden in create_fn
    assert "--query',$Ddl" in create_fn


def test_each_create_verifies_policy_empty_parts_and_source_identity() -> None:
    source = text()
    verify_fn = source.split("function Assert-CreatedTable", 1)[1].split("function Get-JournalPaths", 1)[0]
    assert "storage_policy" in verify_fn
    assert "$script:TargetPolicy" in verify_fn
    assert "active_parts" in verify_fn
    assert "Get-SourceIdentity" in verify_fn
    assert "Authoritative source changed during schema apply" in verify_fn

def test_partial_failure_freezes_state_and_forbids_ordinary_retry_or_auto_drop() -> None:
    source = text()
    for marker in (
        "PARTIAL_FAILURE_FROZEN",
        "TARGET_SCHEMA_PARTIAL_STATE_REMEDIATION_REVIEW",
        "ordinary_retry_allowed=$false",
        "auto_drop_allowed=$false",
        "HALT_AND_FREEZE_PARTIAL_EMPTY_SCHEMA_STATE",
    ):
        assert marker in source
    assert "DROP TABLE" not in source
    assert "TRUNCATE TABLE" not in source


def test_success_requires_all_eight_empty_hot_us_only_tables() -> None:
    source = text()
    for marker in (
        "Final target schema count mismatch",
        "Final target storage policy mismatch",
        "Final target schemas are not empty",
        "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_SUCCESS",
        "TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT",
        "created_table_count=8",
    ):
        assert marker in source


def test_source_is_read_only_and_capacity_is_current_footprint_only() -> None:
    source = text()
    source_fn = source.split("function Invoke-SourceRows", 1)[1].split("function Get-SourceIdentity", 1)[0]
    assert "clickhouse-client" in source_fn
    assert "Assert-ReadOnlySelect" in source_fn
    assert "recommended_30pct_floor" in source
    assert "SourceIdentity.bytes" in source

def test_contract_only_runs_under_windows_powershell_51() -> None:
    if shutil.which("powershell.exe") is None:
        pytest.skip("Windows PowerShell 5.1 is validated by the dedicated windows-latest job")
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT),
        "-ExpectedMainSha", "7c9c006f64ac1ff20e6e1a784f6320fdb5c3523f",
        "-ContractOnly",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_EXECUTOR_CONTRACT_PASS" in output
    assert "schema_apply_executed=False" in output
    assert "GO #690 US secondary family target schema apply" in output


def test_workflow_runs_ps51_and_python_contract_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "windows-latest", "powershell.exe -NoProfile", "-ContractOnly",
        "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_EXECUTOR_CONTRACT_PASS",
        "pytest", "test_us_secondary_family_target_schema_apply_executor_contract.py",
        "concurrency:",
    ):
        assert marker in workflow
