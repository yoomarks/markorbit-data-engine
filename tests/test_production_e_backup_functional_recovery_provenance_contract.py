from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight-production-e-backup-functional-recovery-provenance.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-e-backup-functional-recovery-provenance-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_review_has_no_destructive_surface() -> None:
    source = text()
    forbidden = (
        "[switch]$Apply",
        "[switch]$Resume",
        "Remove-Item",
        "[System.IO.File]::Delete",
        "[System.IO.Directory]::Delete",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "Optimize-VHD",
        "wsl --shutdown",
        "wsl --unmount",
        "wsl --unregister",
        "docker restart",
        "docker prune",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        " MOVE ",
    )
    for marker in forbidden:
        assert marker not in source


def test_review_binds_exact_blocked_preflight_receipt() -> None:
    source = text()
    assert "5c516d461149f9b46c22cbe4ab654670800f6f84" in source
    assert "PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_V1" in source
    assert "PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_BLOCKED" in source
    assert "PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_REVIEW" in source
    assert "duplicate_identity.proven" in source
    assert "e_backup.vhdx_count -ne 3" in source
    assert "capacity.projected_recommended_fit" in source
    assert "Assert-ToolingProvenance" in source
    assert "changed.Count -ne 3" in source


def test_review_imports_complete_dynamic_helper_closure() -> None:
    source = text()
    for marker in (
        "Get-OptionalPropertyValue",
        "Get-OptionalArrayProperty",
        "Test-PathsOverlap",
        "Get-AllContainerMounts",
        "Get-ComposeBindMounts",
        "Get-WslBasePaths",
        "Get-BackupReferenceInventory",
        "Get-DirectoryInventoryNoFollowAllocated",
        "Get-AllocatedFileBytes",
    ):
        assert marker in source
    assert "Get-BackupReferenceInventory 'C:\\fixture\\backup' @() @() $fakeEnv" in source
    assert "Required helper missing" in source


def test_review_classifies_expected_docker_desktop_snapshot_roles() -> None:
    source = text()
    assert "disk\\docker_data.vhdx" in source
    assert "docker_data_primary_snapshot" in source
    assert "disk\\docker_data.empty.vhdx" in source
    assert "docker_data_empty_placeholder_snapshot" in source
    assert "main\\ext4.vhdx" in source
    assert "docker_desktop_system_distro_snapshot" in source
    assert "expectedRoleSetReady" in source
    assert "unresolved" in source


def test_review_correlates_e_snapshot_to_current_d_counterparts() -> None:
    source = text()
    assert "D:\\DockerData\\DockerDesktopWSL" in source
    assert "current_counterpart_exists" in source
    assert "currentCounterpartsReady" in source
    assert "d_counterpart relative_path=" in source
    assert "current_counterpart_missing_count" in source


def test_review_uses_read_only_vhd_metadata_and_attachment_evidence() -> None:
    source = text()
    assert "Get-VHD -Path" in source
    assert "Get-DiskImage -ImagePath" in source
    for marker in (
        "vhd_format",
        "vhd_type",
        "virtual_size",
        "block_size",
        "parent_path",
        "disk_identifier",
        "fragmentation_percentage",
    ):
        assert marker in source
    assert "SAME_VIRTUAL_DISK_IDENTIFIER" in source
    assert "COMPATIBLE_VIRTUAL_DISK_SHAPE_ONLY" in source
    assert "DISTINCT_VIRTUAL_DISK_METADATA" in source


def test_review_surfaces_small_non_vhdx_provenance_file() -> None:
    source = text()
    assert "Get-SmallFilePreview" in source
    assert "65536" in source
    assert "utf8_preview" in source
    assert "e_non_vhdx path=" in source


def test_review_fail_closed_and_never_authorizes_reclaim() -> None:
    source = text()
    assert "PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_BLOCKED" in source
    assert "PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_READY_FOR_OPERATOR_DECISION" in source
    assert "PRODUCTION_E_BACKUP_RECLAIM_OPERATOR_ACK_REVIEW" in source
    assert "PRODUCTION_E_BACKUP_PRESERVE_AND_REPLAN" in source
    assert "e_backup_delete_authorized=False" in source
    assert "cn_warm_move_authorized=False" in source
    assert "vhdx_mutation_authorized=False" in source


def test_review_rechecks_inventory_production_env_and_exact_main() -> None:
    source = text()
    assert "inventory.metadata_manifest_sha256" in source
    assert "e_backup_inventory_stable=$inventoryStable" in source
    assert "Assert-ExactMain 'review_final'" in source
    assert "Assert-ProductionBoundary 'final'" in source
    assert "env_unchanged" in source
    assert "Assert-ExactMain 'exit'" in source


def test_workflow_is_windows_ps51_contract_gate() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in source
    assert "powershell.exe -NoProfile" in source
    assert "-ContractOnly" in source
    assert "PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_CONTRACT_DIRECT_INVOCATION_OK" in source
    assert "concurrency:" in source
