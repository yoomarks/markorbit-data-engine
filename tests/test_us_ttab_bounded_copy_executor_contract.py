from pathlib import Path

SCRIPT = Path("scripts/apply-us-ttab-bounded-copy.ps1")
WORKFLOW = Path(".github/workflows/us-ttab-bounded-copy-executor-runtime.yml")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_executor_modes_and_issue_710_authority_contract() -> None:
    source = text()
    for marker in ("[switch]$DryRun", "[switch]$Apply", "[switch]$ContractOnly"):
        assert marker in source
    assert 'GO #710 US TTAB bounded table copy $PlanSha' in source
    assert "$script:Issue=710" in source
    assert "Assert-ExactMain 'entry'" in source


def test_historical_review_plan_is_explicitly_superseded() -> None:
    source = text()
    old = "95a07ea0d67f280974f6e4cceabbacd423695069083a60de5cd382cd92db2ef6"
    assert old in source
    assert "Historical pre-executor TTAB copy plan is superseded" in source

def test_ttab_scope_order_and_assignment_exclusion() -> None:
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
    pipe = source.split("function Get-NativePipeScript", 1)[1].split("function Invoke-TtabNativePipe", 1)[0]
    assert "us_assignment_" not in pipe
    assert "Assert-SafeTtabTable" in pipe


def test_native_pipe_uses_fd_config_and_runtime_env_without_password_argv() -> None:
    source = text()
    pipe = source.split("function Get-NativePipeScript", 1)[1].split("function Invoke-TtabNativePipe", 1)[0]
    assert "/dev/fd/3" in pipe
    assert "MO_SRC_CH_USER" in pipe and "MO_SRC_CH_PASSWORD" in pipe
    assert "FORMAT Native" in pipe
    assert "--password" not in pipe
    assert "SOURCE_CLICKHOUSE_CLIENT_STDOUT_TO_TARGET_CLICKHOUSE_CLIENT_STDIN" not in pipe

def test_journal_is_durable_before_first_native_pipe() -> None:
    source = text()
    main = source.split("$script:ActiveJournal=New-CopyJournal", 1)[1]
    assert main.index("Save-Journal $script:ActiveJournal $script:ActiveJournalPath") < main.index("Invoke-TtabNativePipe")
    loop = main.split("foreach($tablePlan in $tables)", 1)[1]
    assert loop.index("state='COPY_INTENT'") < loop.index("Invoke-TtabNativePipe")
    assert loop.index("Save-Journal $script:ActiveJournal $script:ActiveJournalPath") < loop.index("Invoke-TtabNativePipe")


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
    assert "US_TTAB_PARTIAL_COPY_REMEDIATION_REVIEW" in source

def test_dryrun_never_consumes_authority_or_copies() -> None:
    source = text()
    dry = source.split("if($DryRun){", 1)[1].split("$script:ActiveJournal=New-CopyJournal", 1)[0]
    assert "authority_consumed=$false" in dry
    assert "copy_executed=$false" in dry
    assert "mutation_performed=$false" in dry
    assert "Invoke-TtabNativePipe" not in dry
    assert "EXACT_OPERATOR_GO_710_REQUIRED" in dry


def test_success_stops_at_secondary_family_acceptance_cutover_review() -> None:
    source = text()
    assert "US_TTAB_BOUNDED_COPY_SUCCESS" in source
    assert "SECONDARY_FAMILY_TARGET_ACCEPTANCE_AND_SERVING_CUTOVER_REVIEW" in source
    assert "copied_table_count=4" in source
    assert "assignment_target_accepted=True" in source


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
    assert "GO #710 US TTAB bounded table copy" not in workflow
    assert "AuthorityToken" not in workflow


def test_exact_main_refreshes_origin_at_each_boundary() -> None:
    source = text()
    block = source.split("function Assert-ExactMain", 1)[1].split("function Get-StringSha256", 1)[0]
    assert "git fetch origin main" in block
    assert "Unable to fetch origin/main during" in block
    assert "git status --porcelain" in block


def test_review_and_plan_bind_accepted_assignment_receipt_and_source_identity() -> None:
    source = text()
    accepted = "01e8e777696ce79aa3db71eac4837f6e6c2114265f9c2662c998688ec62a38b8"
    assert accepted in source
    assert "review.accepted_assignment_success_receipt_sha256" in source
    assert "p.accepted_assignment.receipt_sha256" in source
    assert "resolved.plan.accepted_assignment.source_identity_sha256" in source
    assert "Accepted Assignment success receipt SHA drifted." in source


def test_fresh_connectivity_requires_target_source_version_match() -> None:
    source = text()
    block = source.split("function Assert-FreshConnectivity", 1)[1].split("function Get-NativePipeScript", 1)[0]
    assert "targetVersion[0].version -ne $remoteVersion" in block
    assert "Target/source ClickHouse version drifted." in block


def test_wsl_bash_stdin_is_normalized_to_lf_before_execution() -> None:
    source = text()
    assert "function Convert-ToWslLfText" in source
    assert '.Replace("`r`n","`n").Replace("`r","`n")' in source
    transport = source.split("function Invoke-WslScriptWithSourceCredentials", 1)[1].split("function Resolve-TargetGateway", 1)[0]
    assert "$normalizedScript=Convert-ToWslLfText $ScriptText" in transport
    assert "$process.StandardInput.Write($normalizedScript)" in transport
    assert "$process.StandardInput.Write($ScriptText)" not in transport


def test_assignment_target_is_revalidated_at_all_copy_boundaries() -> None:
    source = text()
    assert "function Assert-AssignmentTargetAccepted" in source
    assert "Assignment target V2 checksum drifted" in source
    assert source.count("Assert-AssignmentTargetAccepted $resolved.plan") >= 2
    pre = source.split("function Assert-PreCopyTable", 1)[1].split("function Assert-PostCopyTable", 1)[0]
    post = source.split("function Assert-PostCopyTable", 1)[1].split("function Get-JournalPaths", 1)[0]
    assert "Assert-AssignmentTargetAccepted $FullPlan" in pre
    assert "Assert-AssignmentTargetAccepted $FullPlan" in post


def test_review_plan_remains_non_authorizing_until_exact_go() -> None:
    source = text()
    assert "p.constraints.ttab_copy_authorized" in source
    assert "p.constraints.future_ttab_copy_authorized" in source
    assert "p.constraints.assignment_mutation_authorized" in source
    assert "p.constraints.review_only" in source
    assert "p.constraints.copy_primitive_execution_performed" in source


def test_native_mutation_surface_is_ttab_only() -> None:
    source = text()
    pipe = source.split("function Get-NativePipeScript", 1)[1].split("function Invoke-TtabNativePipe", 1)[0]
    assert "INSERT INTO markorbit_facts.__TABLE__ FORMAT Native" in pipe
    assert "us_assignment_" not in pipe
    invoke = source.split("function Invoke-TtabNativePipe", 1)[1].split("function Get-TargetSchemaFingerprint", 1)[0]
    assert "us_assignment_" in invoke  # explicit reject guard
    assert "forbidden credential/Assignment argv text" in invoke


def test_each_table_acceptance_persists_assignment_acceptance_evidence() -> None:
    source = text()
    assert "assignment_target_accepted=$true" in source
    post = source.split("function Assert-PostCopyTable", 1)[1].split("function Get-JournalPaths", 1)[0]
    assert "assignment_target_accepted=$true" in post
