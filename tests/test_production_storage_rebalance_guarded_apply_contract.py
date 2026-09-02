from pathlib import Path

SCRIPT = Path("scripts/run-production-storage-rebalance-guarded-apply.ps1")
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_guarded_apply_has_explicit_two_phase_and_ack_contract() -> None:
    for marker in (
        "[ValidateSet('Phase1E','Phase2D')]",
        "[switch]$AcknowledgeTemporary20Percent",
        "[switch]$Apply",
        "-Apply requires explicit -AcknowledgeTemporary20Percent.",
        "PRODUCTION_STORAGE_REBALANCE_GUARDED_APPLY_V1",
        "PRODUCTION_REBALANCE_PHASE1_E_READY_FOR_APPLY",
        "PRODUCTION_REBALANCE_PHASE1_E_GO",
        "PRODUCTION_REBALANCE_PHASE2_D_READY_FOR_APPLY",
        "PRODUCTION_REBALANCE_PHASE2_D_GO",
        "PRODUCTION_HOT_WARM_SIZING_REFRESH_AFTER_REBALANCE",
        "PRODUCTION_STORAGE_REBALANCE_GUARDED_APPLY_DONE",
    ):
        assert marker in TEXT


def test_phase1_is_exact_e_only_and_keeps_other_storage_out_of_scope() -> None:
    assert "E:\\MarkOrbitData\\hot\\clickhouse" in TEXT
    assert "E:\\MarkOrbitData\\hot\\clickhouse-logs" in TEXT
    assert "Remove-Item -LiteralPath $legacyEHotNormalized -Recurse -Force" in TEXT
    assert "Remove-Item -LiteralPath $legacyELogsNormalized -Recurse -Force" in TEXT
    assert TEXT.count("Remove-Item -LiteralPath $legacyEHotNormalized -Recurse -Force") == 1
    assert TEXT.count("Remove-Item -LiteralPath $legacyELogsNormalized -Recurse -Force") == 1
    for marker in (
        "docker_cold_backup_delete_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "accepted_volume_move_authorized=$false",
        "docker_prune_authorized=$false",
        "vhdx_create_authorized=$false",
        "wsl_shutdown_authorized=$false",
        "wsl_unmount_authorized=$false",
        "production_clickhouse_mutation_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in TEXT


def test_phase2_deletes_only_full_sha256_verified_files_individually() -> None:
    for marker in (
        "guarded_apply_stage=phase2_d_full_sha256_parity",
        "Get-FileHash -LiteralPath $entry.source_path -Algorithm SHA256",
        "Get-FileHash -LiteralPath $targetPath -Algorithm SHA256",
        "phase2_d_hash_mismatch_count=",
        "phase2_d_source_manifest_stable=",
        "phase2_d_verified_sha256_manifest.json",
        "Remove-Item -LiteralPath $entry.source_path -Force",
        "recursive_legacy_raw_root_delete_authorized=$false",
        "visual_processed_delete_authorized=$false",
        "Protected visual_processed file reached deletion boundary",
    ):
        assert marker in TEXT
    assert TEXT.count("Remove-Item -LiteralPath $entry.source_path -Force") == 1
    assert "Remove-Item -LiteralPath $legacyRawNormalized -Recurse" not in TEXT
    assert "Remove-Item -LiteralPath $LegacyRawRoot -Recurse" not in TEXT
    assert "Remove-Item -LiteralPath $legacyRawNormalized" not in TEXT
    assert "Remove-Item -LiteralPath $LegacyRawRoot" not in TEXT


def test_phase2_requires_accepted_same_main_phase1_receipt() -> None:
    for marker in (
        "Find-AcceptedPhase1Receipt",
        "No accepted same-main PHASE1_E guarded-apply receipt found under reports.",
        "[string]$receipt.engine_sha -eq $ExpectedMainSha.Trim().ToLowerInvariant()",
        "accepted_phase1_receipt_path=$acceptedPhase1['path']",
        "Phase2D requires E recommended coexistence deficit to be zero",
        "Phase2D requires both legacy E ClickHouse roots to remain absent",
    ):
        assert marker in TEXT


def test_raw_consumers_and_compose_bindings_are_revalidated() -> None:
    for service in ("api", "worker", "mark-image-worker", "qcc-acquisition"):
        assert f"'{service}'" in TEXT
    for target in ("/data/raw", "/data/visual-raw", "/data/visual-processed"):
        assert target in TEXT
    for marker in (
        "Assert-RawConsumersStopped",
        "Assert-ComposeRawBindings",
        "accepted_production_mount_ready=",
        "Production ClickHouse data mount is not the accepted named volume.",
    ):
        assert marker in TEXT


def test_reparse_points_are_fail_closed_before_recursive_e_delete_and_raw_walk() -> None:
    for marker in (
        "Assert-NoReparsePoints $legacyEHotNormalized",
        "Assert-NoReparsePoints $legacyELogsNormalized",
        "Assert-NoReparsePoints $legacyRawNormalized",
        "Assert-NoReparsePoints $rawTargetNormalized",
        "Reparse point found inside guarded deletion tree",
    ):
        assert marker in TEXT


def test_forbidden_broad_or_unrelated_mutation_primitives_are_absent() -> None:
    for forbidden in (
        "Move-Item",
        "Copy-Item",
        "robocopy",
        "rsync",
        "New-VHD",
        "Resize-VHD",
        "Mount-VHD",
        "Dismount-VHD",
        "Optimize-VHD",
        "Format-Volume",
        "mkfs.ext4",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "docker compose restart",
        "wsl.exe --shutdown",
        "wsl.exe --unmount",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    ):
        assert forbidden not in TEXT


def test_exact_main_and_environment_invariants_wrap_destructive_boundaries() -> None:
    for marker in (
        "Assert-ExactMain 'entry'",
        "Assert-ExactMain 'phase1_e_before_delete'",
        "Assert-ExactMain 'phase2_d_before_delete'",
        "Assert-ExactMain 'exit'",
        ".env changed during Phase1E guarded apply.",
        ".env changed during Phase2D guarded apply.",
        "production_invariant_preserved=$true",
        "env_unchanged=$true",
    ):
        assert marker in TEXT
