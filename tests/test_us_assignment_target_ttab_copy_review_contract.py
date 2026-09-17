from pathlib import Path

SCRIPT = Path("scripts/review-us-assignment-target-ttab-copy.ps1")
WORKFLOW = Path(".github/workflows/us-assignment-target-ttab-copy-review-runtime.yml")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_review_is_strictly_non_mutating() -> None:
    source = text()
    for marker in (
        "review_only=True",
        "assignment_target_acceptance_only=True",
        "ttab_copy_authorized=False",
        "mutation_performed=False",
        "copy_primitive_execution_performed=False",
        "serving_cutover_authorized=False",
        "source_cleanup_authorized=False",
        "credential_values_persisted=False",
    ):
        assert marker in source
    assert "BOUNDED_TTAB_TABLE_COPY_EXECUTOR_IMPLEMENTATION" in source
    assert "-Apply" not in source


def test_accepted_assignment_success_is_exactly_bound() -> None:
    source = text()
    assert "01e8e777696ce79aa3db71eac4837f6e6c2114265f9c2662c998688ec62a38b8" in source
    assert "a81d0dd1af056aded650a411fa2eca5c3cc01fa8" in source
    assert "22dc4e4a5234ed0a57b82ce1e7929693a6b5dc26341f1b8b1ead5492dd60f209" in source
    assert "US_ASSIGNMENT_BOUNDED_COPY_SUCCESS" in source
    assert "@($journal.accepted_tables).Count -ne 4" in source


def test_checksum_contract_is_null_safe_v2_for_secondary_tables() -> None:
    source = text()
    assert "NULL_SAFE_JSON_TUPLE_CITYHASH64_V2" in source
    assert "cityHash64(toJSONString(tuple(*)))" in source
    checksum = source.split("function Get-LogicalChecksum", 1)[1].split("function Get-TargetLogicalChecksum", 1)[0]
    assert "^us_(assignment|ttab)_" in checksum
    assert "cityHash64(tuple(*))" not in checksum

def test_ttab_order_and_empty_target_are_frozen() -> None:
    source = text()
    expected = [
        "us_ttab_proceeding_history",
        "us_ttab_party_history",
        "us_ttab_property_history",
        "us_ttab_docket_history",
    ]
    block = source.split("$script:TtabTables=@(", 1)[1].split(")", 1)[0]
    positions = [block.index(name) for name in expected]
    assert positions == sorted(positions)
    target = source.split("function Get-TargetState", 1)[1].split("function Get-AssignmentAcceptance", 1)[0]
    assert "Target TTAB tables are not empty." in target
    assert "storage_policy -ne $script:TargetPolicy" in target


def test_assignment_acceptance_rechecks_logical_parity_and_hot_us() -> None:
    source = text()
    block = source.split("function Get-AssignmentAcceptance", 1)[1].split("function Get-TtabTablePlan", 1)[0]
    assert "Get-LogicalChecksum $table" in block
    assert "Get-TargetLogicalChecksum $table" in block
    assert "checksum_sum" in block and "checksum_xor" in block
    assert "Target Assignment disk residency drifted" in block
    assert "$script:TargetDisk" in block


def test_live_connectivity_uses_runtime_only_credentials() -> None:
    source = text()
    block = source.split("function Invoke-TargetWslSourceSql", 1)[1].split("function Assert-TargetServerCanReachSource", 1)[0]
    assert "WSLENV" in block
    assert "MO_SRC_CH_USER" in block and "MO_SRC_CH_PASSWORD" in block
    assert "$process.StandardInput.Write($Sql)" in block
    assert "credential_values_persisted=$false" in source
    assert "credential_values_in_plan=$false" in source
    assert "write-host $password" not in source.lower()

def test_plan_freezes_capacity_failure_and_future_primitive_without_executing_it() -> None:
    source = text()
    assert "TARGET_WSL_DUAL_CLICKHOUSE_CLIENT_NATIVE_PIPE_V1" in source
    assert "future_growth_projection_performed=$false" in source
    assert "future_growth_sufficiency_claimed=$false" in source
    assert "future_ttab_copy_authorized=$false" in source
    assert "auto_truncate_allowed=$false" in source
    assert "auto_drop_allowed=$false" in source
    assert "ordinary_retry_allowed=$false" in source
    assert "partial_target_state_must_be_preserved=$true" in source
    assert "Invoke-TtabCopy" not in source


def test_exact_main_is_refetched_and_review_ends_before_authority() -> None:
    source = text()
    exact = source.split("function Assert-ExactMain", 1)[1].split("function Convert-JsonLines", 1)[0]
    assert "git fetch origin main" in exact
    assert "git status --porcelain" in exact
    assert "future_authority_token_format" in source
    assert "US_ASSIGNMENT_TARGET_TTAB_COPY_REVIEW_READY" in source
    assert "BOUNDED_TTAB_TABLE_COPY_EXECUTOR_IMPLEMENTATION" in source


def test_scoped_workflow_runs_ps51_contract_only_and_python_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-powershell51:" in workflow
    assert "python-contract:" in workflow
    assert "review-us-assignment-target-ttab-copy.ps1" in workflow
    assert "test_us_assignment_target_ttab_copy_review_contract.py" in workflow
    assert "-ContractOnly" in workflow
    assert "-Apply" not in workflow
    assert "AuthorityToken" not in workflow
