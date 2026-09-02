from pathlib import Path


SCRIPT = Path("scripts/plan-production-storage-rebalance-apply.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_apply_plan_requires_fresh_temporary_hard_floor_inventory() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_STORAGE_REBALANCE_APPLY_PLAN_V1",
        "profile-production-storage-rebalance-candidates.ps1",
        "PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1",
        "REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND",
        "PRODUCTION_REBALANCE_APPLY_PLAN_WITH_TEMPORARY_20_PERCENT_REVIEW",
        "PRODUCTION_REBALANCE_TEMPORARY_20_PERCENT_APPLY_PLAN_READY",
        "PRODUCTION_REBALANCE_GUARDED_APPLY_WITH_TEMPORARY_20_PERCENT_ACK",
        "candidate_inventory_evidence_strategy=SHALLOW_REPO_REPORTS",
    ):
        assert marker in text


def test_apply_plan_preserves_recommended_vs_hard_floor_truth() -> None:
    text = _text()
    for marker in (
        "d_additional_free_recommended_bytes",
        "d_additional_free_hard_bytes",
        "e_additional_free_recommended_bytes",
        "e_additional_free_hard_bytes",
        "Get-ProjectedFreeBytes",
        "Get-ResidualDeficitBytes",
        "d_recommended_residual_after_bytes",
        "d_hard_residual_after_bytes",
        "e_recommended_residual_after_bytes",
        "temporary_20_percent_review_required=$true",
        "D 30% recommended floor remains an explicit unresolved coexistence gap",
    ):
        assert marker in text


def test_apply_plan_freezes_exact_safe_action_boundaries() -> None:
    text = _text()
    for marker in (
        "DELETE_EXACT_UNREFERENCED_LEGACY_NTFS_CLICKHOUSE_TREE",
        "DELETE_EXACT_UNREFERENCED_LEGACY_NTFS_CLICKHOUSE_LOG_TREE_IF_PRESENT",
        "DELETE_ONLY_FULL_SHA256_VERIFIED_DUPLICATE_RAW_FILES",
        "every deletable source file has a target counterpart with equal SHA256",
        "protected visual_processed subtree is excluded even when empty",
        "files are deleted individually from the verified manifest; no recursive raw-root delete",
        "E phase must be accepted before D phase begins",
        "E legacy NTFS ClickHouse and logs have zero current Docker/Compose references immediately before deletion",
        "all Raw consumer services remain quiescent",
        "accepted production ClickHouse mount remains the accepted named volume",
    ):
        assert marker in text


def test_apply_plan_retains_recovery_and_proof_assets() -> None:
    text = _text()
    for marker in (
        "e_docker_cold_backup_role",
        "d_spike_role",
        "d_runtime_role",
        "e_spike_role",
        "e_tooling_role",
        "f_recovery_role",
        "docker_cold_backup_delete_authorized=$false",
        "docker_cold_backup_move_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "accepted_volume_move_authorized=$false",
    ):
        assert marker in text


def test_apply_plan_never_authorizes_or_performs_storage_mutation() -> None:
    text = _text()
    for marker in (
        "apply_authorized=$false",
        "temporary_20_percent_acknowledgement_authorized=$false",
        "legacy_e_hot_delete_authorized=$false",
        "legacy_raw_delete_authorized=$false",
        "visual_processed_delete_authorized=$false",
        "docker_data_vhdx_move_authorized=$false",
        "docker_data_vhdx_compact_authorized=$false",
        "vhdx_create_authorized=$false",
        "vhdx_resize_authorized=$false",
        "vhdx_mount_authorized=$false",
        "wsl_unmount_authorized=$false",
        "wsl_shutdown_authorized=$false",
        "docker_restart_authorized=$false",
        "docker_prune_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "corpus_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "mutation_performed=$false",
    ):
        assert marker in text

    forbidden = (
        "Remove-Item",
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
    )
    for marker in forbidden:
        assert marker not in text
