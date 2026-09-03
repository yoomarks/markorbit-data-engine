from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight-production-cn-warm-migration-equivalence.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-cn-warm-migration-equivalence-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_gate_has_no_apply_surface_and_keeps_all_mutation_authority_false() -> None:
    source = text()
    assert "[switch]$Apply" not in source
    assert "apply_surface_present=False" in source
    for marker in (
        "clickhouse_mutation_authorized=False",
        "cn_warm_move_authorized=False",
        "vhdx_mutation_authorized=False",
        "wsl_mutation_authorized=False",
        "docker_restart_authorized=False",
        "docker_prune_authorized=False",
        "accepted_volume_mutation_authorized=False",
        "raw_delete_authorized=False",
        "cn_replay_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in source


def test_issue_481_operator_architecture_supersedes_old_small_warm_model() -> None:
    source = text()
    assert "USER_ARCHITECTURE_SCENARIO_V1_ISSUE_481" in source
    assert "ISSUE_481_USER_ARCHITECTURE_SCENARIO" in source
    assert "TableName -eq 'cn_observed_event'" in source
    assert "TableName.EndsWith('_event'" in source
    assert "TableName.StartsWith('cn_goods_'" in source
    assert "WARM_EVENT_HISTORY" in source
    assert "WARM_GOODS_CATEGORY" in source
    assert "HOT_CURRENT_SERVING_CONSERVATIVE" in source


def test_legacy_contracts_are_recorded_not_used_as_silent_veto() -> None:
    source = text()
    for marker in (
        "cn_goods_item_current",
        "HOT_REQUIRED",
        "cn_goods_item_observation",
        "WARM_CANDIDATE_REQUIRES_SUMMARY_REPLACEMENT",
        "cn_observed_event",
        "HOT_WITH_COMPACTABLE_BASELINE",
        "legacy_placement_contract",
        "operator_override_basis",
    ):
        assert marker in source


def test_gate_is_bound_to_accepted_e_reclaim_go_receipt() -> None:
    source = text()
    for marker in (
        "9231b52d5e5bc14f455df353758e87829c7398ce",
        "PRODUCTION_E_BACKUP_GUARDED_RECLAIM_APPLY_V1",
        "PRODUCTION_E_BACKUP_GUARDED_RECLAIM_GO",
        "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT",
        "853980217998",
        "618860039242",
        "e_backup_root_removed",
        "recommended_30_percent_admission",
        "production_invariant_preserved",
        "env_unchanged",
    ):
        assert marker in source


def test_only_clickhouse_system_metadata_is_queried() -> None:
    source = text()
    assert "FROM system.tables" in source
    assert "FROM system.parts" in source
    assert "system metadata only" in source
    assert "SELECT\n    name AS table" in source
    assert "SELECT\n    table," in source
    for forbidden in (
        "[switch]$Apply",
        "OPTIMIZE TABLE",
        "MOVE PARTITION",
        "MOVE PART '",
        "DROP TABLE",
        "TRUNCATE TABLE",
        "docker system prune",
        "docker volume rm",
        "wsl --shutdown",
        "wsl --unmount",
        "wsl --unregister",
        "Mount-VHD",
        "Resize-VHD",
        "Optimize-VHD",
    ):
        assert forbidden not in source


def test_each_active_candidate_requires_mergetree_and_single_source_disk() -> None:
    source = text()
    assert "NON_MERGETREE:" in source
    assert "AMBIGUOUS_SOURCE_DISK:" in source
    assert "@($candidate.disk_names).Count -ne 1" in source
    assert "source_disk=$sourceDisk" in source
    assert "rollback_target_source_disk=$sourceDisk" in source
    assert "warm_candidate_manifest_sha256" in source
    assert "schema_fingerprint_sha256" in source


def test_direct_serving_reads_are_explicit_performance_acceptance_not_silenced() -> None:
    source = text()
    assert "direct_serving_read" in source
    assert "direct_serving_read_count" in source
    assert "performance_sensitive_post_move_acceptance_required" in source
    assert "direct_logical_reads_are_not_blockers_by_themselves=$true" in source
    assert "post_move_query_and_latency_acceptance_required" in source
    assert "post_move_summary_and_case_api_acceptance_required=$true" in source
    assert "post_move_latency_acceptance_required=$true" in source


def test_runtime_physical_placement_coupling_is_fail_closed() -> None:
    source = text()
    for marker in (
        "runtime_physical_placement_blocker",
        "/var/lib/clickhouse",
        "disk_name",
        "storage_policy",
        "system\\.disks",
        "system\\.storage_policies",
        "$runtimePhysicalBlockers.Count -eq 0",
    ):
        assert marker in source


def test_capacity_uses_fresh_e_space_30_percent_reserve_and_copy_safety() -> None:
    source = text()
    assert "[double]$CopySafetyMarginPercent = 10" in source
    assert "0.30" in source
    assert "New-Object System.IO.DriveInfo('E')" in source
    assert "Get-RequiredCapacityBytes $warmBytes $CopySafetyMarginPercent" in source
    assert "recommended_30_percent_admission" in source
    assert "planning_to_fresh_required_delta_bytes" in source
    assert "$recommendedMargin -ge 0" in source


def test_production_boundary_freezes_main_consumers_mount_env_and_recovery() -> None:
    source = text()
    for marker in (
        "Assert-ExactMain 'entry'",
        "Assert-ExactMain 'metadata_before'",
        "Assert-ExactMain 'final'",
        "Assert-ExactMain 'exit'",
        "Assert-RawConsumersStopped",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        ".env changed during CN Warm equivalence preflight",
        "E:\\DockerDataBackup\\DockerDesktopWSL_20260901_before_recovery",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "961542094848",
    ):
        assert marker in source


def test_ready_state_is_strategy_only_and_advances_only_to_ext4_preflight() -> None:
    source = text()
    assert "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_READY" in source
    assert "PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT" in source
    assert "migration_equivalence_strategy_ready=$strategyReady" in source
    assert "migration_completed=$false" in source
    assert "migration_completed=False" in source
    assert "source_policy_cleanup_authorized=$false" in source
    assert "post_move_metadata_equivalence_required=$true" in source
    assert "post_move_row_count_equivalence_required=$true" in source
    assert "post_move_target_disk_residency_required=$true" in source
    assert "post_move_writer_placement_acceptance_required=$true" in source


def test_contract_fixture_proves_logical_read_allowed_and_physical_coupling_blocked() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split(
        "function Assert-Administrator", 1
    )[0]
    assert "cn_goods_item_current" in fixture
    assert "Direct serving read fixture was not detected" in fixture
    assert "Logical SQL read was incorrectly treated as physical coupling" in fixture
    assert "disk_name = 'default'" in fixture
    assert "Runtime physical placement blocker fixture was not detected" in fixture
    assert "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_CONTRACT_DIRECT_INVOCATION_OK" in fixture


def test_tooling_provenance_is_exact_three_file_boundary() -> None:
    source = text()
    assert "AllowedToolingFiles" in source
    assert "preflight-production-cn-warm-migration-equivalence.ps1" in source
    assert "test_production_cn_warm_migration_equivalence_contract.py" in source
    assert "production-cn-warm-migration-equivalence-runtime.yml" in source
    assert "$changed.Count -ne 3" in source
    assert "changed outside the exact 3-file boundary" in source


def test_workflow_is_windows_ps51_contract_gate_without_apply() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in source
    assert "powershell.exe -NoProfile" in source
    assert "-ContractOnly" in source
    assert "AcceptedEReclaimReceiptPath" in source
    assert "Apply" in source  # AST gate explicitly rejects an Apply parameter.
    assert "must not expose Apply" in source
    assert "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_CONTRACT_DIRECT_INVOCATION_OK" in source
    assert "concurrency:" in source
