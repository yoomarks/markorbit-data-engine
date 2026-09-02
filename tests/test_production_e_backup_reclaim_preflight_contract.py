from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight-production-e-backup-reclaim.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-e-backup-reclaim-preflight-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_preflight_has_no_destructive_surface() -> None:
    source = text()
    forbidden = (
        "[switch]$Apply",
        "[switch]$Resume",
        "Remove-Item",
        "[System.IO.File]::Delete",
        "[System.IO.Directory]::Delete",
        "docker prune",
        "docker restart",
        "wsl --shutdown",
        "wsl --unmount",
        "Unregister",
        "Resize-VHD",
        "Dismount-VHD",
        "Mount-VHD",
        "OPTIMIZE TABLE",
        "ALTER TABLE",
        " MOVE ",
    )
    for marker in forbidden:
        assert marker not in source


def test_preflight_binds_exact_accepted_layout_receipt() -> None:
    source = text()
    assert "a3b9de462f1be1a7f7627446280c8e0df7f3fbf9" in source
    assert "PRODUCTION_STORAGE_LAYOUT_REPLAN_V1" in source
    assert "PRODUCTION_STORAGE_LAYOUT_REPLAN_READY" in source
    assert "PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT" in source
    assert "Assert-ToolingProvenance" in source
    assert "changed.Count -ne 3" in source
    assert "e_backup.technical_reclaim_candidate" in source
    assert "e_backup.delete_authorized" in source
    assert "duplicate_identity_proven" in source


def test_preflight_imports_transitive_phase2d_optional_array_helper() -> None:
    source = text()
    assert "'Get-OptionalPropertyValue','Get-OptionalArrayProperty','Get-DotEnvValues'" in source
    assert "'Get-AllContainerMounts','Get-ComposeBindMounts','Get-BackupReferenceInventory'" in source
    assert "Get-OptionalArrayProperty $optionalFixture 'Mounts'" in source
    assert "Get-OptionalArrayProperty $optionalFixture 'Missing'" in source


def test_preflight_uses_actual_allocated_bytes_not_only_logical_projection() -> None:
    source = text()
    assert "GetCompressedFileSizeW" in source
    assert "Get-AllocatedFileBytes" in source
    assert "allocated_bytes" in source
    assert "e_backup_actual_allocated_reclaim_bytes" in source
    assert "projected_free_after_actual_allocated_reclaim_bytes" in source
    assert "Get-NewAllocationBudget" in source
    assert "scenario_e_projected_recommended_fit_after_actual_reclaim" in source


def test_preflight_requires_no_follow_unreferenced_detached_backup() -> None:
    source = text()
    assert "Get-DirectoryInventoryNoFollowAllocated" in source
    assert "FileAttributes]::ReparsePoint" in source
    assert "Get-BackupReferenceInventory" in source
    assert "Get-ProcessBackupReferences" in source
    assert "Get-DiskImage" in source
    assert "attachment.proof_available" in source
    assert "not [bool]$attachment.attached" in source
    assert "e_backup_structural_safety_ready" in source


def test_full_sha_only_after_length_and_structural_gates() -> None:
    source = text()
    length_pos = source.index("e_f_recovery_vhdx_length_equal")
    structural_pos = source.index("e_backup_structural_safety_ready")
    sha_start_pos = source.index("full_sha_identity_scan_started=True")
    e_hash_pos = source.index("Get-Sha256WithProgress $eSingleVhdx.path")
    f_hash_pos = source.index("Get-Sha256WithProgress $expectedF")
    assert structural_pos < sha_start_pos
    assert length_pos < sha_start_pos
    assert sha_start_pos < e_hash_pos < f_hash_pos
    assert "full_sha256_equal" in source
    assert "full_sha256_mismatch" in source
    assert "vhdx_length_mismatch_requires_functional_recovery_provenance_review" in source


def test_long_hash_is_followed_by_stability_and_production_rechecks() -> None:
    source = text()
    assert "e_backup_inventory_stable_after_hash" in source
    assert "f_recovery_vhdx_metadata_stable_after_hash" in source
    assert "Assert-ExactMain 'preflight_final'" in source
    assert "Assert-ProductionBoundary 'final'" in source
    assert "env_unchanged" in source
    assert "e_backup_reference_count_final" in source


def test_success_advances_only_to_separate_apply_implementation() -> None:
    source = text()
    assert "PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_READY" in source
    assert "PRODUCTION_E_BACKUP_RECLAIM_APPLY_IMPLEMENTATION" in source
    assert "PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_REVIEW" in source
    assert "PRODUCTION_STORAGE_COEXISTENCE_REDESIGN" in source
    assert "e_backup_delete_authorized=False" in source
    assert "cn_warm_move_authorized=False" in source
    assert "vhdx_create_authorized=False" in source


def test_workflow_is_windows_ps51_contract_gate() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in source
    assert "powershell.exe -NoProfile" in source
    assert "-ContractOnly" in source
    assert "PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_CONTRACT_DIRECT_INVOCATION_OK" in source
    assert "concurrency:" in source
