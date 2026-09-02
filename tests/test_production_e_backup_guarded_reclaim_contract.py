from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-production-e-backup-guarded-reclaim.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-e-backup-guarded-reclaim-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_apply_requires_explicit_delete_acknowledgement() -> None:
    source = text()
    assert "[switch]$Apply" in source
    assert "[switch]$AcknowledgeSupersededEBackupDelete" in source
    assert "Apply requires -AcknowledgeSupersededEBackupDelete" in source
    assert "AcceptedBoundaryReceiptPath" in source
    assert "PRODUCTION_E_BACKUP_GUARDED_RECLAIM_BOUNDARY_READY_FOR_APPLY" in source


def test_operator_is_manifest_bound_to_exact_four_snapshot_files() -> None:
    source = text()
    for marker in (
        "settings-store.json",
        "disk\\docker_data.empty.vhdx",
        "main\\ext4.vhdx",
        "disk\\docker_data.vhdx",
        "frozen_manifest_file_count",
        "853980217998",
    ):
        assert marker in source
    assert "Frozen E reclaim manifest must contain exactly four files" in source
    assert "E backup metadata manifest SHA changed" in source
    assert "E backup manifest path set changed" in source


def test_no_recursive_or_broad_delete_surface() -> None:
    source = text()
    forbidden = (
        "Remove-Item",
        "-Recurse",
        "Directory]::Delete($Root, $true)",
        "Directory]::Delete($dir, $true)",
        "docker system prune",
        "docker volume rm",
        "wsl --shutdown",
        "wsl --unmount",
        "wsl --unregister",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "Optimize-VHD",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
    )
    for marker in forbidden:
        assert marker not in source
    assert "[System.IO.File]::Delete($path)" in source
    assert "[System.IO.Directory]::GetFileSystemEntries" in source
    assert "[System.IO.Directory]::Delete($dir, $false)" in source
    assert "[System.IO.Directory]::Delete($Root, $false)" in source


def test_provenance_and_recovery_invariants_are_frozen() -> None:
    source = text()
    assert "a371fcbc2a35bd67f64ca1954ed497ed9ebe5444" in source
    assert "PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_V1" in source
    assert "PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_READY_FOR_OPERATOR_DECISION" in source
    assert "inventory_stable" in source
    assert "expected_role_set_ready" in source
    assert "current_counterparts_ready" in source
    assert "all_detached_proof_ready" in source
    assert "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx" in source
    assert "961542094848" in source
    assert "Assert-CurrentDRuntimeCounterparts" in source
    assert "Assert-FRecoveryPreserved" in source
    assert "Assert-EVhdxDetached" in source


def test_journal_is_persisted_before_and_after_each_exact_file_delete() -> None:
    source = text()
    function = source.split("function Delete-ExactManifestFile", 1)[1].split(
        "function Remove-OnlyEmptySnapshotDirectories", 1
    )[0]
    first_save = function.index("Save-JournalAtomic")
    delete = function.index("[System.IO.File]::Delete($path)")
    second_save = function.index("Save-JournalAtomic", first_save + 1)
    assert first_save < delete < second_save
    assert "inflight_relative_path" in function
    assert "completed_relative_paths" in function
    assert "deleted_file_count" in function
    assert "deleted_bytes" in function


def test_partial_failure_resume_is_fail_closed_and_manifest_bound() -> None:
    source = text()
    assert "[string]$ResumeJournalPath" in source
    assert "[switch]$AcknowledgeResumePartialReclaim" in source
    assert "PARTIAL_FAILURE" in source
    assert "Resume requires PARTIAL_FAILURE journal with mutation_started=true" in source
    assert "Completed E reclaim file reappeared" in source
    assert "resume_recovered_absent_inflight" in source
    assert "resume_retry_inflight" in source
    assert "Pending manifest file missing" in source


def test_apply_rechecks_production_exact_main_env_and_refs() -> None:
    source = text()
    for marker in (
        "Assert-ExactMain 'apply_before_mutation'",
        "Assert-ProductionBoundary 'apply_before_mutation'",
        "Assert-ExactMain 'apply_final'",
        "Assert-ProductionBoundary 'apply_final'",
        "e_backup_reference_count_",
        ".env changed during guarded reclaim apply",
    ):
        assert marker in source


def test_success_advances_only_to_cn_warm_equivalence_preflight() -> None:
    source = text()
    assert "PRODUCTION_E_BACKUP_GUARDED_RECLAIM_GO" in source
    assert "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT" in source
    assert "cn_warm_move_authorized=False" in source
    assert "vhdx_mutation_authorized=False" in source
    assert "accepted_volume_mutation_authorized=False" in source
    assert "docker_restart_authorized=False" in source


def test_workflow_is_windows_ps51_contract_gate() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in source
    assert "powershell.exe -NoProfile" in source
    assert "-ContractOnly" in source
    assert "PRODUCTION_E_BACKUP_GUARDED_RECLAIM_CONTRACT_DIRECT_INVOCATION_OK" in source
    assert "concurrency:" in source
