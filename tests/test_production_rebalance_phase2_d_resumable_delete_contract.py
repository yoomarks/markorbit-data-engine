from pathlib import Path


SCRIPT = Path("scripts/run-production-rebalance-phase2-d-resumable-delete.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_operator_is_bound_to_accepted_authority() -> None:
    text = _text()
    for marker in (
        "74cc3379fc7ff81f29a9235b7c55a0ffda2f4090",
        "6cd4399aaaf47aab3c5dde6dfd87dc7a29be676ce0d3da93d3d6e493f2f35253",
        "AcceptedManifestFileCount = [int64]1146",
        "AcceptedManifestBytes = [int64]57920246250",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_AUTHORITY_JOURNAL_V1",
        r"D:\yoomarks\markorbit-data-engine\raw_data",
        r"F:\MarkOrbitData\raw",
        r"D:\yoomarks\markorbit-data-engine\raw_data\visual_processed",
    ):
        assert marker in text


def test_default_mode_is_full_sha_read_only_dry_run() -> None:
    text = _text()
    for marker in (
        "if (-not $Apply)",
        "Invoke-FullDryRunVerification",
        "Assert-NormalFileExact $entry 'F' $true",
        "Assert-NormalFileExact $entry 'D' $true",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_READY_FOR_APPLY",
        "apply_requested=False",
        "data_mutation_performed=False",
        "phase2_d_file_delete_authorized=False",
        "visual_processed_delete_authorized=False",
        "dry_run_receipt_path=",
    ):
        assert marker in text


def test_apply_requires_explicit_authority_and_acknowledgements() -> None:
    text = _text()
    for marker in (
        "[switch]$Apply",
        "[switch]$AcknowledgeLegacyDRawDuplicateDelete",
        "[switch]$AcknowledgeTemporary20PercentFloor",
        "Actual Apply requires explicit -AcceptedDryRunReceiptPath.",
        "Apply requires duplicate-delete and temporary-20%-floor acknowledgements.",
        "Authority journal changed since accepted dry-run.",
        "Dry-run receipt engine SHA does not match exact main.",
        "Assert-ExactMain 'destructive_boundary_exact_main'",
    ):
        assert marker in text


def test_only_manifest_bound_files_can_be_deleted_and_root_is_never_recursively_removed() -> None:
    text = _text()
    for marker in (
        "function Delete-AuthorizedSourceFile",
        "Delete boundary rejected authority source path.",
        "Test-PathContains $root $source",
        "Test-PathContains $protected $source",
        "[System.IO.File]::Delete($source)",
        "recursive_legacy_raw_root_delete_authorized=False",
        "recursive_legacy_raw_root_delete_performed=$false",
    ):
        assert marker in text
    for forbidden in (
        "Remove-Item",
        "RemoveDirectoryW",
        "Directory]::Delete",
        "Directory.Delete",
        "robocopy",
        "rsync",
    ):
        assert forbidden not in text


def test_per_file_protocol_persists_inflight_hashes_both_sides_then_deletes() -> None:
    text = _text()
    inflight = text.index("Set-ObjectProperty $journal 'inflight_relative_path' $relative")
    save = text.index("Save-JournalAtomic $journalResult.path $journal", inflight)
    f_hash = text.index("Assert-NormalFileExact $entry 'F' $true", save)
    d_hash = text.index("Assert-NormalFileExact $entry 'D' $true", f_hash)
    delete = text.index("Delete-AuthorizedSourceFile $entry", d_hash)
    complete = text.index("$completedPaths += $relative", delete)
    clear = text.index("Set-ObjectProperty $journal 'inflight_relative_path' $null", complete)
    assert inflight < save < f_hash < d_hash < delete < complete < clear


def test_journal_is_durable_and_partial_failure_is_resumable() -> None:
    text = _text()
    for marker in (
        "Save-JournalAtomic",
        "[System.IO.File]::Replace($temporary, $fullPath, $backup, $true)",
        "state' 'MUTATING'",
        "state' 'PARTIAL_FAILURE'",
        "state' 'GO'",
        "partial_failure_requires_explicit_resume",
        "journal_state=PARTIAL_FAILURE",
        "failure_path",
        "failure_message",
    ):
        assert marker in text


def test_resume_is_explicit_and_fails_closed_on_unjournaled_absence_or_tamper() -> None:
    text = _text()
    for marker in (
        "[string]$ResumeJournalPath",
        "[switch]$AcknowledgeResumeAfterPartialFailure",
        "Resume requires -Apply.",
        "Resume requires -AcknowledgeResumeAfterPartialFailure.",
        "Resume requires PARTIAL_FAILURE journal with mutation_started=true.",
        "Pending D authority file absent without journal evidence",
        "authority source expected absent but exists",
        "resume_inflight_source_present_retry=",
        "resume_inflight_recovered_complete=",
        "F authority SHA changed",
        "Journal deletion counters do not match completed paths.",
    ):
        assert marker in text


def test_global_boundaries_remain_fail_closed() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        "Assert-CurrentBindings",
        "Assert-NoReparsePoints",
        "Legacy E roots reappeared.",
        ".env changed since PREPARED authority.",
        "Protected visual_processed changed since PREPARED authority.",
        "D Raw has references outside protected visual_processed.",
        "temporary 20-percent hard floor",
    ):
        assert marker in text


def test_final_go_reverifies_all_f_hashes_and_all_d_absence() -> None:
    text = _text()
    for marker in (
        "phase2_d_final_verification=all_D_absent_and_F_sha_exact",
        "Assert-NormalFileExact $entry 'D' $false",
        "Assert-NormalFileExact $entry 'F' $true",
        "phase2_d_final_hash_progress=",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_GO",
        "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH",
        "preferred_30_percent_floor_claimed=$false",
        "production_invariant_preserved=$true",
        "env_unchanged=$true",
        "protected_visual_processed_unchanged=$true",
    ):
        assert marker in text


def test_no_unrelated_destructive_authority_is_introduced() -> None:
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
        "Package2",
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


def test_contract_fixture_exercises_atomic_journal_completed_present_and_f_tamper() -> None:
    text = _text()
    for marker in (
        "function Invoke-ContractFixture",
        "Atomic journal replace fixture failed.",
        "Completed D-present state did not fail closed.",
        "F tamper did not fail closed.",
        "PHASE2D_RESUMABLE_DELETE_JOURNAL_FIXTURE_PASS",
    ):
        assert marker in text
