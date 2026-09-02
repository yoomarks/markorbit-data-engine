from pathlib import Path


SCRIPT = Path("scripts/run-production-rebalance-phase2-d-resumable-delete.ps1")
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_apply_surface_is_explicit_and_separate_from_resume():
    for marker in (
        "[switch]$Apply",
        "[switch]$AcknowledgeLegacyDRawDuplicateDelete",
        "[switch]$AcknowledgeTemporary20PercentFloor",
        "[switch]$AcknowledgeResumeAfterPartialFailure",
        "[string]$AuthorityJournalPath",
        "[string]$ResumeJournalPath",
        "[string]$AcceptedFullShaDryRunReceiptPath",
        "[string]$AcceptedBoundaryDryRunReceiptPath",
    ):
        assert marker in TEXT
    assert "Resume uses only -ResumeJournalPath" in TEXT
    assert "Resume requires -Apply" in TEXT
    assert "Apply requires duplicate-delete and temporary-20%-floor acknowledgements" in TEXT


def test_frozen_target_authority_is_exact():
    assert "74cc3379fc7ff81f29a9235b7c55a0ffda2f4090" in TEXT
    assert "5332086661f4a68bbc93622c511fc16f38f4d89f" in TEXT
    assert "6cd4399aaaf47aab3c5dde6dfd87dc7a29be676ce0d3da93d3d6e493f2f35253" in TEXT
    assert "9af7822688fad9c9d8bf1facd5088d830591b51875475dcc31dac82d11732324" in TEXT
    assert "$script:AcceptedManifestFileCount = [int64]1146" in TEXT
    assert "$script:AcceptedManifestBytes = [int64]57920246250" in TEXT
    assert "D:\\yoomarks\\markorbit-data-engine\\raw_data" in TEXT
    assert "F:\\MarkOrbitData\\raw" in TEXT
    assert "visual_processed" in TEXT


def test_full_sha_receipt_is_bound_to_accepted_dry_run_engine_not_future_apply_sha():
    assert "$script:AcceptedFullShaDryRunEngineSha = '5332086661f4a68bbc93622c511fc16f38f4d89f'" in TEXT
    assert "receipt.engine_sha -ne $script:AcceptedFullShaDryRunEngineSha" in TEXT
    assert "Full-SHA dry-run receipt engine SHA is not accepted" in TEXT
    assert "receipt.authority_journal_sha256 -ne $script:AcceptedPreparedJournalSha256" in TEXT
    assert "receipt.hash_mismatch_count -ne 0" in TEXT


def test_first_run_on_apply_engine_is_still_zero_delete_boundary_dry_run():
    assert "if (-not $Apply)" in TEXT
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_BOUNDARY_READY_FOR_APPLY" in TEXT
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_BOUNDARY_DRY_RUN_DONE" in TEXT
    assert "data_mutation_performed=$false" in TEXT
    assert "phase2_d_file_delete_authorized=$false" in TEXT
    dry_run_index = TEXT.index("if (-not $Apply)")
    first_delete_index = TEXT.index("Delete-AuthorizedSourceFile $entry")
    assert dry_run_index < first_delete_index


def test_actual_apply_requires_same_engine_boundary_receipt():
    assert "Actual Apply requires explicit -AcceptedBoundaryDryRunReceiptPath" in TEXT
    assert "receipt.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()" in TEXT
    assert "receipt.authority_journal_sha256 -ne $script:AcceptedPreparedJournalSha256" in TEXT
    assert "receipt.full_sha_dry_run_receipt_sha256 -ne $FullShaReceiptSha" in TEXT


def test_only_authorized_source_file_is_deleted_and_root_is_never_recursively_removed():
    assert "function Delete-AuthorizedSourceFile" in TEXT
    assert "[System.IO.File]::Delete($source)" in TEXT
    assert "Delete boundary rejected protected visual_processed path" in TEXT
    assert "recursive_legacy_raw_root_delete_authorized=False" in TEXT
    assert "[System.IO.Directory]::Delete" not in TEXT
    production_prefix = TEXT.split("function Invoke-ContractFixture", 1)[0]
    assert "Remove-Item" not in production_prefix


def test_per_file_hashes_are_checked_immediately_before_delete():
    sequence = (
        "Assert-NormalFileExact $entry 'F' $true\n"
        "            Assert-NormalFileExact $entry 'D' $true\n"
        "            Delete-AuthorizedSourceFile $entry"
    )
    assert sequence in TEXT


def test_journal_is_persisted_before_delete_and_after_completion():
    inflight = TEXT.index("Set-ObjectProperty $journal 'inflight_relative_path' $relative")
    save_before = TEXT.index("Save-JournalAtomic $journalResult.path $journal", inflight)
    delete = TEXT.index("Delete-AuthorizedSourceFile $entry", save_before)
    completed = TEXT.index("$completedPaths += $relative", delete)
    clear = TEXT.index("Set-ObjectProperty $journal 'inflight_relative_path' $null", completed)
    save_after = TEXT.index("Save-JournalAtomic $journalResult.path $journal", clear)
    assert inflight < save_before < delete < completed < clear < save_after


def test_resume_accepts_catch_failure_and_hard_interruption_states():
    assert "$resumeState -ne 'PARTIAL_FAILURE' -and $resumeState -ne 'MUTATING'" in TEXT
    assert "Resume requires PARTIAL_FAILURE or interrupted MUTATING journal" in TEXT
    assert "Assert-FrozenResumeReceipts $journal" in TEXT
    assert "Assert-ResumeState $journal $manifest.entries" in TEXT


def test_inflight_is_reconciled_before_resume_capacity_math():
    reconcile = TEXT.index("$reconcileResult = Reconcile-InflightForResume")
    remaining = TEXT.index("$remainingBytes = [int64]$script:AcceptedManifestBytes - [int64]$journal.deleted_bytes")
    assert reconcile < remaining
    assert "resume_inflight_recovered_complete" in TEXT
    assert "resume_inflight_source_present_retry" in TEXT


def test_resume_state_fails_closed_for_completed_present_pending_absent_and_f_tamper():
    assert "$Side authority file expected absent but exists" in TEXT
    assert "Pending D authority file absent without journal evidence" in TEXT
    assert "$Side authority SHA changed" in TEXT
    assert "Inflight path is already completed" in TEXT


def test_partial_failure_is_durable_and_never_advises_manual_delete_or_blind_rerun():
    assert "Set-ObjectProperty $journal 'state' 'PARTIAL_FAILURE'" in TEXT
    assert "partial_failure_requires_explicit_resume" in TEXT
    assert "journal_state=PARTIAL_FAILURE" in TEXT
    lowered = TEXT.lower()
    assert "manual delete" not in lowered
    assert "blind rerun" not in lowered


def test_final_go_requires_full_counts_f_hashes_production_and_hard_floor():
    assert "Deletion counters did not reach full frozen authority" in TEXT
    assert "phase2_d_final_verification=all_D_absent_and_F_sha_exact" in TEXT
    assert "Assert-NormalFileExact $entry 'F' $true" in TEXT
    assert "Assert-GlobalBoundary $journal 'final'" in TEXT
    assert "D free space did not reach temporary 20-percent hard floor" in TEXT
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_DELETE_GO" in TEXT
    assert "next_gate='PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH'" in TEXT
    assert "preferred_30_percent_floor_claimed=$false" in TEXT


def test_unrelated_destructive_authorities_remain_false():
    for marker in (
        "accepted_volume_delete_authorized=False",
        "docker_restart_authorized=False",
        "docker_prune_authorized=False",
        "vhdx_mutation_authorized=False",
        "wsl_mutation_authorized=False",
        "clickhouse_mutation_authorized=False",
        "replay_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in TEXT
