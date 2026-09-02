from pathlib import Path


SCRIPT = Path("scripts/profile-production-storage-rebalance-candidates.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_rebalance_inventory_reuses_fresh_authoritative_sizing() -> None:
    text = _text()
    for marker in (
        "PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1",
        "plan-production-hot-warm-sizing.ps1",
        "PRODUCTION_HOT_WARM_SIZING_PLAN_V1",
        "PRODUCTION_HOT_WARM_SIZING_PLAN_READY",
        "PRODUCTION_STORAGE_REBALANCE_PLAN",
        "REBALANCE_REQUIRED_BEFORE_PROVISION",
        "accepted_production_mount_ready",
        "markorbit-data-engine_clickhouse_data",
    ):
        assert marker in text


def test_rebalance_inventory_profiles_preferred_and_retained_candidates() -> None:
    text = _text()
    for marker in (
        r"D:\yoomarks\markorbit-data-engine\raw_data",
        r"F:\MarkOrbitData\raw",
        r"E:\MarkOrbitData\hot\clickhouse",
        r"E:\MarkOrbitData\hot\clickhouse-logs",
        r"E:\DockerDataBackup",
        r"D:\MarkOrbitData\spike",
        r"D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike",
        r"E:\MarkOrbitData\spike",
        r"E:\MarkOrbitData\wsl-tooling\Ubuntu-24.04",
        r"F:\MarkOrbitData\recovery",
        "legacy_raw_to_f_metadata_parity_exact",
        "protected_visual_processed_stats",
        "COLD_RECOVERY_BACKUP_SECONDARY_CANDIDATE_ONLY",
        "RETAINED_ARCHITECTURE_PROOF",
        "RETAINED_WSL_RUNTIME_PROOF",
    ):
        assert marker in text


def test_rebalance_inventory_separates_recommended_and_hard_floor_deficits() -> None:
    text = _text()
    for marker in (
        "Get-ReserveBytes",
        "Get-AdditionalFreeRequired",
        "d_additional_free_recommended_bytes",
        "d_additional_free_hard_bytes",
        "e_additional_free_recommended_bytes",
        "e_additional_free_hard_bytes",
        "REBALANCE_RECOMMENDED_FLOOR_CANDIDATES_FOUND",
        "REBALANCE_TEMPORARY_HARD_FLOOR_CANDIDATES_FOUND",
        "REBALANCE_HARD_FLOOR_CANDIDATES_FOUND",
        "REBALANCE_CANDIDATE_EVIDENCE_INSUFFICIENT",
        "PRODUCTION_REBALANCE_APPLY_PLAN_WITH_TEMPORARY_20_PERCENT_REVIEW",
    ):
        assert marker in text


def test_rebalance_inventory_checks_container_and_compose_references() -> None:
    text = _text()
    for marker in (
        "docker' @('ps','-a','-q')",
        "Get-AllContainerMounts",
        "Get-ComposeBindMounts",
        "Get-PathReferences",
        "all_container_reference_count",
        "running_container_reference_count",
        "compose_reference_count",
        "unexpectedLegacyRawComposeRefs",
        "unexpectedLegacyRawContainerRefs",
    ):
        assert marker in text


def test_rebalance_inventory_never_authorizes_or_performs_mutation() -> None:
    text = _text()
    required_false_markers = (
        "temporary_20_percent_floor_apply_authorized=$false",
        "legacy_e_hot_delete_authorized=$false",
        "legacy_raw_delete_authorized=$false",
        "visual_processed_delete_authorized=$false",
        "docker_cold_backup_delete_authorized=$false",
        "docker_cold_backup_move_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "accepted_volume_move_authorized=$false",
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
        "source_copy_authorized=$false",
        "corpus_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "file_delete_performed=$false",
        "file_move_performed=$false",
        "file_copy_performed=$false",
        "vhdx_create_performed=$false",
        "vhdx_resize_performed=$false",
        "vhdx_mount_performed=$false",
        "vhdx_move_performed=$false",
        "wsl_unmount_performed=$false",
        "wsl_shutdown_performed=$false",
        "docker_restart_performed=$false",
        "docker_prune_performed=$false",
        "production_clickhouse_mutation_performed=$false",
        "accepted_volume_mutation_performed=$false",
        "source_copy_performed=$false",
        "corpus_replay_performed=$false",
    )
    for marker in required_false_markers:
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
