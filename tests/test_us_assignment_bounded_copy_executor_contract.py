from pathlib import Path

SCRIPT = Path("scripts/apply-us-assignment-bounded-copy.ps1")
WORKFLOW = Path(".github/workflows/us-assignment-bounded-copy-executor-runtime.yml")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_executor_modes_and_issue_704_authority_contract() -> None:
    source = text()
    for marker in ("[switch]$DryRun", "[switch]$Apply", "[switch]$ContractOnly"):
        assert marker in source
    assert 'GO #704 US Assignment bounded table copy $PlanSha' in source
    assert "$script:Issue=704" in source
    assert "Assert-ExactMain 'entry'" in source


def test_historical_review_plan_is_explicitly_superseded() -> None:
    source = text()
    old = "3b1005403e34ce3796ea006b0a99033668ce42ce7463e6b25335f0b617360273"
    assert old in source
    assert "Historical pre-executor Assignment copy plan is superseded" in source

def test_assignment_scope_order_and_ttab_exclusion() -> None:
    source = text()
    expected = [
        "us_assignment_record_history",
        "us_assignment_assignor_history",
        "us_assignment_assignee_history",
        "us_assignment_property_history",
    ]
    positions = [source.index(name) for name in expected]
    assert positions == sorted(positions)
    pipe = source.split("function Get-NativePipeScript", 1)[1].split("function Invoke-AssignmentNativePipe", 1)[0]
    assert "us_ttab_" not in pipe
    assert "Assert-SafeAssignmentTable" in pipe


def test_native_pipe_uses_fd_config_and_runtime_env_without_password_argv() -> None:
    source = text()
    pipe = source.split("function Get-NativePipeScript", 1)[1].split("function Invoke-AssignmentNativePipe", 1)[0]
    assert "/dev/fd/3" in pipe
    assert "MO_SRC_CH_USER" in pipe and "MO_SRC_CH_PASSWORD" in pipe
    assert "FORMAT Native" in pipe
    assert "--password" not in pipe
    assert "SOURCE_CLICKHOUSE_CLIENT_STDOUT_TO_TARGET_CLICKHOUSE_CLIENT_STDIN" not in pipe

def test_journal_is_durable_before_first_native_pipe() -> None:
    source = text()
    main = source.split("$script:ActiveJournal=New-CopyJournal", 1)[1]
    assert main.index("Save-Journal $script:ActiveJournal $script:ActiveJournalPath") < main.index("Invoke-AssignmentNativePipe")
    loop = main.split("foreach($tablePlan in $tables)", 1)[1]
    assert loop.index("state='COPY_INTENT'") < loop.index("Invoke-AssignmentNativePipe")
    assert loop.index("Save-Journal $script:ActiveJournal $script:ActiveJournalPath") < loop.index("Invoke-AssignmentNativePipe")


def test_v2_post_copy_acceptance_and_hot_us_residency_are_required() -> None:
    source = text()
    assert "NULL_SAFE_JSON_TUPLE_CITYHASH64_V2" in source
    assert "cityHash64(toJSONString(tuple(*)))" in source
    assert "Get-TargetLogicalChecksum" in source
    assert "Target V2 checksum mismatch after copy" in source
    assert "Assert-TargetPartsOnHotUs" in source
    assert "Source changed during copy" in source
    assert "Write-TableAcceptance" in source


def test_partial_failure_freezes_and_forbids_cleanup_retry() -> None:
    source = text()
    assert "PARTIAL_FAILURE_FROZEN" in source
    assert "ordinary_retry_allowed=$false" in source
    assert "auto_truncate_allowed=$false" in source
    assert "auto_drop_allowed=$false" in source
    assert "US_ASSIGNMENT_PARTIAL_COPY_REMEDIATION_REVIEW" in source

def test_dryrun_never_consumes_authority_or_copies() -> None:
    source = text()
    dry = source.split("if($DryRun){", 1)[1].split("$script:ActiveJournal=New-CopyJournal", 1)[0]
    assert "authority_consumed=$false" in dry
    assert "copy_executed=$false" in dry
    assert "mutation_performed=$false" in dry
    assert "Invoke-AssignmentNativePipe" not in dry
    assert "EXACT_OPERATOR_GO_704_REQUIRED" in dry


def test_success_stops_at_assignment_acceptance_ttab_review_gate() -> None:
    source = text()
    assert "US_ASSIGNMENT_BOUNDED_COPY_SUCCESS" in source
    assert "ASSIGNMENT_TARGET_ACCEPTANCE_AND_TTAB_REVIEW" in source
    assert "copied_table_count=4" in source
    assert "ttab_untouched=True" in source


def test_secret_values_are_not_written_to_journal_or_receipts() -> None:
    source = text()
    journal = source.split("function New-CopyJournal", 1)[1].split("function Write-TableAcceptance", 1)[0]
    assert "Password" not in journal and "MO_SRC_CH_PASSWORD" not in journal
    assert "authority_token_sha256" in journal
    assert "credential_values_persisted=$false" in source
    assert "Redact-Secrets" in source


def test_workflow_never_invokes_apply_or_embeds_production_go() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "-ContractOnly" in workflow
    assert " -Apply" not in workflow
    assert "GO #704 US Assignment bounded table copy" not in workflow


def test_exact_main_refreshes_origin_at_each_boundary() -> None:
    source = text()
    block = source.split("function Assert-ExactMain", 1)[1].split("function Get-StringSha256", 1)[0]
    assert "git fetch origin main" in block
    assert "Unable to fetch origin/main during" in block
    assert "git status --porcelain" in block


def test_review_and_plan_bind_accepted_connectivity_receipt() -> None:
    source = text()
    accepted = "9f8ee8a5bf0980503a1ebd3ab24d0665f9d828aa866e2a8504ef4584a987da8a"
    assert accepted in source
    assert "review.accepted_connectivity_receipt_sha256" in source
    assert "p.accepted_connectivity.receipt_sha256" in source
    assert "Accepted connectivity receipt SHA drifted." in source
    assert "Plan accepted connectivity receipt drifted." in source


def test_fresh_connectivity_requires_target_source_version_match() -> None:
    source = text()
    block = source.split("function Assert-FreshConnectivity", 1)[1].split("function Get-NativePipeScript", 1)[0]
    assert "targetVersion[0].version -ne $remoteVersion" in block
    assert "Target/source ClickHouse version drifted." in block
