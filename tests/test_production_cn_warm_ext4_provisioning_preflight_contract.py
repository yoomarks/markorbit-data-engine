from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight-production-cn-warm-ext4-provisioning.ps1"
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "production-cn-warm-ext4-provisioning-preflight-runtime.yml"
)


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_gate_is_strictly_read_only_without_apply_or_resume_surface() -> None:
    source = text()
    assert "[switch]$Apply" not in source
    assert "[switch]$Resume" not in source
    assert "read_only=True" in source
    assert "mutation_performed=False" in source
    assert "apply_surface_present=False" in source
    assert "resume_surface_present=False" in source
    for marker in (
        "vhdx_create_authorized=False",
        "vhdx_resize_authorized=False",
        "vhdx_mount_authorized=False",
        "vhdx_detach_authorized=False",
        "vhdx_compact_authorized=False",
        "vhdx_move_authorized=False",
        "vhdx_delete_authorized=False",
        "wsl_mutation_authorized=False",
        "clickhouse_mutation_authorized=False",
        "cn_warm_move_authorized=False",
        "docker_restart_authorized=False",
        "docker_prune_authorized=False",
        "accepted_volume_mutation_authorized=False",
        "raw_delete_authorized=False",
        "cn_replay_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in source


def test_dangerous_vhdx_and_wsl_mutation_commands_are_absent() -> None:
    source = text().lower()
    for forbidden in (
        "new-vhd",
        "resize-vhd",
        "mount-vhd",
        "dismount-vhd",
        "optimize-vhd",
        "wsl.exe' @('--unmount'",
        "wsl --unmount",
        "wsl.exe' @('--shutdown'",
        "wsl --shutdown",
        "wsl.exe' @('--unregister'",
        "wsl --unregister",
        "docker system prune",
        "docker volume rm",
        "move partition",
        "optimize table",
        "alter table",
    ):
        assert forbidden not in source


def test_gate_is_bound_to_accepted_equivalence_receipt_and_manifest() -> None:
    source = text()
    for marker in (
        "96befe0ae4824dfe2f0ffed48d0b12cc0c508e0f",
        "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_V1",
        "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_READY",
        "PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT",
        "5519154978",
        "716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231",
        "2430570761",
        "562600035674",
        "618860039242",
        "Get-CandidateManifestHash",
        "failed canonical SHA recomputation",
        "rows do not re-sum",
        "bytes do not re-sum",
    ):
        assert marker in source


def test_quota_model_includes_copy_safety_filesystem_overhead_and_future_headroom() -> None:
    source = text()
    for marker in (
        "CopySafetyMarginPercent = [double]10",
        "FilesystemRuntimeOverheadPercent = [double]8",
        "MinimumFilesystemRuntimeOverheadBytes = [int64](64GB)",
        "FutureExpansionHeadroomPercent = [double]25",
        "filesystem_runtime_overhead_bytes",
        "future_expansion_headroom_bytes",
        "proposed_vhdx_max_bytes",
        "proposed_ext4_quota_bytes",
        "Round-UpToGiB",
    ):
        assert marker in source
    assert "proposedMax -le [int64]$accepted.recomputed_physical_bytes" in source
    assert "PROPOSED_WARM_VHDX_MAX_HAS_NO_OVERHEAD_OR_HEADROOM" in source


def test_fresh_e_capacity_preserves_30_percent_reserve_against_max_size() -> None:
    source = text()
    assert "New-Object System.IO.DriveInfo('E')" in source
    assert "0.30" in source
    assert "Get-RecommendedBudget $eTotal $eFree" in source
    assert "recommended_margin_after_proposed_vhdx_max_bytes" in source
    assert "recommended_30_percent_admission" in source
    assert "PROPOSED_WARM_VHDX_MAX_EXCEEDS_30_PERCENT_ADMISSION_BUDGET" in source


def test_unique_production_path_is_derived_and_not_operator_overridable() -> None:
    source = text()
    assert "E:\\MarkOrbitData\\production\\clickhouse\\warm_cn.vhdx" in source
    assert "markorbit_prod_warm_cn" in source
    param_block = source.split(")\n\nSet-StrictMode", 1)[0]
    assert "ProposedWarmVhdxPath" not in param_block
    assert "ProposedWarmMountName" not in param_block
    assert "PROPOSED_WARM_VHDX_PATH_ALREADY_EXISTS" in source
    assert "PROPOSED_WARM_VHDX_COLLIDES_WITH_PROTECTED_VHDX" in source
    assert "PROPOSED_WARM_VHDX_COLLIDES_WITH_EXISTING_E_VHDX" in source
    assert "PROPOSED_WARM_VHDX_PARENT_REPARSE_POINT" in source


def test_known_spike_tooling_runtime_and_recovery_vhdx_are_protected() -> None:
    source = text()
    for marker in (
        "D:\\MarkOrbitData\\spike\\hot_cn_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_us_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_global_spike.vhdx",
        "E:\\MarkOrbitData\\spike\\warm_spike.vhdx",
        "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse-Spike\\ext4.vhdx",
        "E:\\MarkOrbitData\\wsl-tooling\\Ubuntu-24.04\\ext4.vhdx",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "D:\\DockerData\\DockerDesktopWSL",
    ):
        assert marker in source


def test_wsl_distro_root_mount_and_disk_image_inventory_are_read_only() -> None:
    source = text()
    for marker in (
        "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss",
        "Get-WslDistros",
        "Test-WslFindmntReady",
        "Get-WslMountInventory",
        "findmnt",
        "Get-NoFollowVhdxInventory",
        "ReparsePoint",
        "Get-DiskImageSnapshot",
        "Get-DiskImage",
        "disk_image_states",
    ):
        assert marker in source


def test_dedicated_wsl_architecture_is_reused_not_docker_mnt_wsl_bind() -> None:
    source = text()
    assert "DEDICATED_ORDINARY_WSL2_CLICKHOUSE_EXT4_V1_ISSUES_402_410" in source
    assert "dedicated_ordinary_wsl2_clickhouse_required = $true" in source
    assert "docker_desktop_external_mnt_wsl_bind_rejected_by_prior_spike = $true" in source
    assert "TOOLING_DISTRO_MISSING" in source
    assert "TOOLING_DISTRO_NOT_WSL2" in source
    assert "TOOLING_DISTRO_FINDMNT_UNAVAILABLE" in source


def test_production_boundary_freezes_main_clickhouse_mount_raw_env_backup_and_recovery() -> None:
    source = text()
    for marker in (
        "Assert-Administrator",
        "Assert-ExactMain 'entry'",
        "Assert-ExactMain 'inventory_before'",
        "Assert-ExactMain 'final'",
        "Assert-ExactMain 'exit'",
        "Assert-RawConsumersStopped",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        ".env changed during CN Warm ext4 provisioning preflight",
        "E:\\DockerDataBackup\\DockerDesktopWSL_20260901_before_recovery",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "961542094848",
    ):
        assert marker in source


def test_tooling_provenance_is_exact_three_file_boundary() -> None:
    source = text()
    assert "AllowedToolingFiles" in source
    assert "preflight-production-cn-warm-ext4-provisioning.ps1" in source
    assert "test_production_cn_warm_ext4_provisioning_preflight_contract.py" in source
    assert "production-cn-warm-ext4-provisioning-preflight-runtime.yml" in source
    assert "$changed.Count -ne 3" in source
    assert "changed outside the exact 3-file boundary" in source


def test_contract_fixture_exercises_imported_helper_closure_size_math_and_collision() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split(
        "\ntry {\n    Write-Host '===== PRODUCTION CN WARM EXT4 PROVISIONING PREFLIGHT ====='",
        1,
    )[0]
    for marker in (
        "Import-AcceptedProductionHelpers",
        "Invoke-NativeText",
        "Assert-ExactMain",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        "Assert-RawConsumersStopped",
        "Get-ProvisioningSizeModel",
        "Retained VHDX collision fixture failed",
        "PRODUCTION_CN_WARM_EXT4_PROVISIONING_CONTRACT_DIRECT_INVOCATION_OK",
    ):
        assert marker in fixture


def test_ready_advances_only_to_separate_implementation_gate() -> None:
    source = text()
    assert "PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_READY" in source
    assert "PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_BLOCKED" in source
    assert "PRODUCTION_CN_WARM_EXT4_PROVISIONING_IMPLEMENTATION" in source
    assert "provisioning_completed = $false" in source
    assert "provisioning_completed=False" in source
    assert "cn_warm_move_authorized=False" in source


def test_workflow_is_windows_ps51_contract_gate_without_apply_or_resume() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in source
    assert "powershell.exe -NoProfile" in source
    assert "-ContractOnly" in source
    assert "AcceptedCnWarmEquivalenceReceiptPath" in source
    assert "must not expose Apply or Resume" in source
    assert "PRODUCTION_CN_WARM_EXT4_PROVISIONING_CONTRACT_DIRECT_INVOCATION_OK" in source
    assert "preflight-production-rebalance-phase2-d-full-sha256.ps1" in source
    assert "concurrency:" in source
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in source
