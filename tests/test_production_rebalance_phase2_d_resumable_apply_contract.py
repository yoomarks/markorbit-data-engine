from pathlib import Path


SCRIPT = Path("scripts/run-production-rebalance-phase2-d-resumable-apply.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_phase2d_apply_requires_audited_resume_and_both_destructive_acknowledgements() -> None:
    text = _text()
    for marker in (
        "[switch]$Apply",
        "$ResumeJournalPath",
        "$AcknowledgeLegacyDRawDuplicateDelete",
        "$AcknowledgeTemporary20PercentFloor",
        "$AcknowledgeResumeAfterPartialFailure",
        "-Apply is forbidden without -ResumeJournalPath from an audited no-Apply dry-run.",
        "-Apply requires explicit -AcknowledgeLegacyDRawDuplicateDelete.",
        "-Apply requires explicit -AcknowledgeTemporary20PercentFloor.",
        "requires explicit -AcknowledgeResumeAfterPartialFailure after operator audit",
    ):
        assert marker in text


def test_phase2d_apply_freezes_exact_accepted_target_preflight_authority() -> None:
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
        "original_authority_preserved=$true",
        "Verified manifest must remain in the accepted preflight evidence directory.",
        "Verified manifest file count changed",
        "Verified manifest byte total changed",
    ):
        assert marker in text


def test_preflight_to_apply_provenance_is_exactly_three_tooling_files() -> None:
    text = _text()
    for allowed in (
        "scripts/run-production-rebalance-phase2-d-resumable-apply.ps1",
        "tests/test_production_rebalance_phase2_d_resumable_apply_contract.py",
        ".github/workflows/production-rebalance-phase2-d-resumable-apply-runtime.yml",
    ):
        assert allowed in text
    for marker in (
        "git' @('merge-base','--is-ancestor'",
        "preflight_to_current_unexpected_changed_file_count=",
        "preflight_to_current_missing_tooling_file_count=",
        "changes outside the exact resumable-apply tooling delta",
    ):
        assert marker in text


def test_phase2d_apply_never_recursively_deletes_raw_root_or_visual_processed() -> None:
    text = _text()
    for marker in (
        r"D:\yoomarks\markorbit-data-engine\raw_data",
        r"F:\MarkOrbitData\raw",
        "visual_processed",
        "recursive_legacy_raw_root_delete_authorized=False",
        "visual_processed_delete_authorized=False",
        "Delete path is outside the manifest-authorized non-protected D boundary.",
        "[System.IO.File]::Delete($normalized)",
    ):
        assert marker in text
    for forbidden in (
        "Remove-Item",
        "RemoveDirectoryW",
        "DeleteNormalDirectoryChecked",
        "Directory]::Delete",
        "Directory.Delete",
        "robocopy",
        "rsync",
    ):
        assert forbidden not in text


def test_each_source_delete_requires_exact_source_and_f_target_sha256() -> None:
    text = _text()
    for marker in (
        "Assert-NormalFileIdentity",
        "D source before delete",
        "F target before delete",
        "-HashContent",
        "source_sha256",
        "target_sha256",
        "Source length changed immediately before delete",
        "target_sha256_verified_at_each_delete_boundary",
    ):
        assert marker in text


def test_journal_is_atomic_manifest_bound_and_crash_resumable() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_JOURNAL_V1",
        "Save-JournalAtomic",
        "[System.IO.File]::Replace",
        "inflight_relative_path",
        "completed_relative_paths",
        "PREPARED",
        "MUTATING",
        "PARTIAL_FAILURE",
        "GO",
        "recover_completed",
        "retry_inflight",
        "Pending D source is absent without journal completion/inflight evidence",
        "Journal says completed but D source still exists",
        "Authority manifest bytes changed after dry-run",
        "Accepted preflight receipt bytes changed after dry-run",
        "Do not manually delete or blindly rerun",
    ):
        assert marker in text


def test_dry_run_has_no_mutation_and_only_go_advances_to_post_reclaim_refresh() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_READY_FOR_APPLY",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_GO",
        "PRODUCTION_REBALANCE_POST_D_RECLAIM_REFRESH",
        "apply_accepted=$applyAccepted",
        "mutation_performed=$mutationPerformed",
    ):
        assert marker in text
    assert "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_READY_FOR_APPLY" in text
    assert "Fresh direct Apply is forbidden." in text


def test_operational_boundaries_stay_closed() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Get-ProductionClickHouseHealth",
        "Assert-AcceptedProductionMount",
        "Assert-ReferenceBoundary",
        "Assert-EnvBindings",
        "Protected visual_processed metadata changed after dry-run authority was frozen.",
        "Legacy E roots reappeared after Phase1E.",
        "temporary 20-percent hard floor",
        "preferred_30_percent_exception_remains",
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


def test_contract_mode_exercises_delete_resume_and_tamper_without_production_paths() -> None:
    text = _text()
    for marker in (
        "Invoke-ContractFixture",
        "Pending resume disposition fixture failed.",
        "Authorized delete escaped source file boundary in fixture.",
        "Inflight absent recovery disposition fixture failed.",
        "Pending absent source did not fail closed.",
        "Completed source reappearance did not fail closed.",
        "F target tamper did not fail closed.",
        "PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_PS51_CONTRACT_PASS",
    ):
        assert marker in text
