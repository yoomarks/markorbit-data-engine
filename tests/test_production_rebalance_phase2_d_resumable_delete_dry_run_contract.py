from pathlib import Path


SCRIPT = Path("scripts/preflight-production-rebalance-phase2-d-resumable-delete.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_dry_run_is_bound_to_accepted_prepared_authority() -> None:
    text = _text()
    for marker in (
        "74cc3379fc7ff81f29a9235b7c55a0ffda2f4090",
        "6cd4399aaaf47aab3c5dde6dfd87dc7a29be676ce0d3da93d3d6e493f2f35253",
        "AcceptedManifestFileCount = [int64]1146",
        "AcceptedManifestBytes = [int64]57920246250",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_JOURNAL_V1",
        "Dry-run requires untouched PREPARED authority journal.",
        "Dry-run authority journal already records deletion.",
        "Dry-run authority journal unexpectedly has inflight path.",
    ):
        assert marker in text


def test_dry_run_has_no_apply_or_delete_surface() -> None:
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
        "Directory]::Delete",
        "Directory.Delete",
        "robocopy",
        "rsync",
    ):
        assert forbidden not in text
    for marker in (
        "read_only=True",
        "apply_supported=False",
        "data_mutation_performed=False",
        "phase2_d_file_delete_authorized=False",
        "recursive_legacy_raw_root_delete_authorized=False",
        "visual_processed_delete_authorized=False",
    ):
        assert marker in text


def test_full_sha_revalidates_every_d_and_f_authority_file() -> None:
    text = _text()
    for marker in (
        "Invoke-FullShaDryRun",
        "Assert-NormalFileExact $entry 'F'",
        "Assert-NormalFileExact $entry 'D'",
        "Get-Sha256 $path",
        "phase2_d_dry_run_hash_progress=",
        "verified_file_count=[int64]$manifest.entries.Count",
        "verified_bytes=$verifiedBytes",
        "hash_mismatch_count=[int64]0",
    ):
        assert marker in text


def test_exact_roots_and_visual_processed_remain_frozen() -> None:
    text = _text()
    for marker in (
        r"D:\yoomarks\markorbit-data-engine\raw_data",
        r"F:\MarkOrbitData\raw",
        r"D:\yoomarks\markorbit-data-engine\raw_data\visual_processed",
        "Protected visual_processed leaked into authority:",
        "Protected visual_processed changed since PREPARED authority.",
        "Assert-NoReparsePoints",
    ):
        assert marker in text


def test_current_runtime_references_and_production_are_rechecked() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        "Assert-CurrentBindings",
        "RAW_DATA_PATH",
        "VISUAL_RAW_PATH",
        "VISUAL_PROCESSED_PATH",
        "D Raw has references outside protected visual_processed.",
        "Legacy E roots reappeared.",
        "Assert-ExactMain 'entry'",
        "Assert-ExactMain 'exit'",
    ):
        assert marker in text


def test_capacity_advances_only_if_temporary_hard_floor_is_met() -> None:
    text = _text()
    for marker in (
        "required_hard_free_bytes",
        "required_recommended_free_bytes",
        "d_hard_residual_after_projected_bytes",
        "d_recommended_residual_after_projected_bytes",
        "Dry-run no longer clears temporary 20-percent hard floor.",
    ):
        assert marker in text


def test_ready_receipt_advances_only_to_separate_apply_implementation() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_DRY_RUN_V1",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_READY_FOR_APPLY",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_APPLY_IMPLEMENTATION",
        "dry_run_receipt_path=",
        "production_invariant_preserved=$true",
        "env_unchanged=$true",
        "protected_visual_processed_unchanged=$true",
    ):
        assert marker in text
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_GO" not in text


def test_no_unrelated_destructive_authority() -> None:
    text = _text()
    for forbidden in (
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
    for marker in (
        "accepted_volume_delete_authorized=False",
        "docker_restart_authorized=False",
        "vhdx_mutation_authorized=False",
        "clickhouse_mutation_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in text


def test_contract_mode_exercises_ps51_json_array_and_safe_path() -> None:
    text = _text()
    for marker in (
        "Expand-JsonArrayForPowerShell51",
        "PS5.1 top-level JSON array expansion failed.",
        "Unsafe relative path did not fail closed.",
        "PHASE2D_RESUMABLE_DELETE_DRY_RUN_PS51_CONTRACT_PASS",
    ):
        assert marker in text
