from pathlib import Path


SCRIPT = Path("scripts/run-production-rebalance-phase2-d-resumable-apply.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_phase2d_authority_prepare_has_no_apply_or_delete_surface() -> None:
    text = _text()
    for forbidden in (
        "[switch]$Apply",
        "$ResumeJournalPath",
        "$AcknowledgeLegacyDRawDuplicateDelete",
        "$AcknowledgeTemporary20PercentFloor",
        "$AcknowledgeResumeAfterPartialFailure",
        "[System.IO.File]::Delete",
        "Remove-Item",
        "RemoveDirectoryW",
        "DeleteNormalDirectoryChecked",
        "Directory]::Delete",
        "Directory.Delete",
        "robocopy",
        "rsync",
    ):
        assert forbidden not in text
    for marker in (
        "apply_supported=False",
        "data_mutation_performed=False",
        "phase2_d_file_delete_authorized=False",
        "recursive_legacy_raw_root_delete_authorized=False",
        "visual_processed_delete_authorized=False",
    ):
        assert marker in text


def test_phase2d_authority_freezes_exact_accepted_target_preflight() -> None:
    text = _text()
    for marker in (
        "2f20083a0153e0f7f2568ebd86719adaf3d88b48",
        "PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_V1",
        "PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_READY",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_DESIGN",
        "AcceptedManifestFileCount = [int64]1146",
        "AcceptedManifestBytes = [int64]57920246250",
        "authority_manifest_sha256",
        "accepted_preflight_receipt_sha256",
        "Authority manifest is not colocated with the accepted preflight receipt.",
        "Authority manifest file count changed",
        "Authority manifest byte total changed",
        "Authority manifest contains a hash mismatch",
        "Authority SHA pair is invalid",
    ):
        assert marker in text


def test_preflight_to_authority_provenance_is_exactly_three_tooling_files() -> None:
    text = _text()
    for allowed in (
        "scripts/run-production-rebalance-phase2-d-resumable-apply.ps1",
        "tests/test_production_rebalance_phase2_d_resumable_apply_contract.py",
        ".github/workflows/production-rebalance-phase2-d-resumable-apply-runtime.yml",
    ):
        assert allowed in text
    for marker in (
        "git' @('merge-base','--is-ancestor'",
        "preflight_to_current_changed_file_count=",
        "preflight_to_current_unexpected_changed_file_count=",
        "preflight_to_current_missing_tooling_file_count=",
        "exact three-file authority-preparation tooling delta",
    ):
        assert marker in text


def test_exact_d_f_and_visual_processed_boundaries_remain_frozen() -> None:
    text = _text()
    for marker in (
        r"D:\yoomarks\markorbit-data-engine\raw_data",
        r"F:\MarkOrbitData\raw",
        "visual_processed",
        "RAW_DATA_PATH",
        "VISUAL_RAW_PATH",
        "VISUAL_PROCESSED_PATH",
        "D Raw has references outside protected visual_processed.",
        "Protected visual_processed leaked into authority manifest",
    ):
        assert marker in text


def test_prepared_journal_is_atomic_and_contains_zero_mutation_state() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_JOURNAL_V1",
        "Save-JournalAtomic",
        "[System.IO.File]::Replace",
        "state='PREPARED'",
        "phase='awaiting_separate_apply_implementation_and_audit'",
        "mutation_started=$false",
        "completed_relative_paths=@()",
        "inflight_relative_path=$null",
        "deleted_file_count=[int64]0",
        "deleted_bytes=[int64]0",
        "protected_tree_signature",
        "env_sha256",
    ):
        assert marker in text


def test_authority_prepare_revalidates_production_references_and_capacity() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        "Assert-ComposeRawBindings",
        "Assert-NoReparsePoints",
        "Assert-CurrentMetadataMatchesAuthority",
        "Get-ProtectedTreeSignature",
        "Legacy E roots reappeared after accepted Phase1E.",
        "temporary 20-percent hard floor",
        "d_hard_residual_after_projected_bytes=",
        "d_recommended_residual_after_projected_bytes=",
        ".env changed during authority preparation.",
        "Protected visual_processed changed during authority preparation.",
    ):
        assert marker in text
    for forbidden in (
        "docker compose up",
        "docker compose down",
        "docker compose restart",
        "docker system prune",
        "docker volume rm",
        "wsl.exe --shutdown",
        "wsl.exe --unmount",
        "New-VHD",
        "Resize-VHD",
        "Mount-VHD",
        "Dismount-VHD",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    ):
        assert forbidden not in text


def test_success_advances_only_to_separate_apply_implementation() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_PREPARE_V1",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_PREPARED",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_IMPLEMENTATION",
        "read_only=$true",
        "data_mutation_performed=$false",
        "production_invariant_preserved=$true",
        "env_unchanged=$true",
    ):
        assert marker in text
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_GO" not in text
    assert "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH" not in text


def test_contract_mode_exercises_atomic_prepared_journal_and_path_fail_closed() -> None:
    text = _text()
    for marker in (
        "Invoke-ContractFixture",
        "Atomic PREPARED journal fixture failed.",
        "Unsafe authority relative path did not fail closed.",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_PS51_CONTRACT_PASS",
    ):
        assert marker in text
