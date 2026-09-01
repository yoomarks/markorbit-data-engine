from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "profile-production-multi-disk-migration-readiness.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_readiness_is_exact_main_admin_and_read_only() -> None:
    text = source()
    assert "[string]$ExpectedMainSha" in text
    assert "Production migration readiness must run from local main." in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "requires elevated Administrator PowerShell" in text
    assert "read_only=$true" in text
    assert "PRODUCTION_MULTI_DISK_MIGRATION_READINESS_DONE" in text


def test_readiness_freezes_accepted_dedicated_wsl_architecture() -> None:
    text = source()
    assert "$acceptedArchitectureDecision = 'DEDICATED_WSL_CLICKHOUSE_GO'" in text
    assert "$acceptedArchitectureProofSha = '1d990dc8ab44bdb827538961309c6c33fb38234f'" in text
    assert "stable_endpoint='host.docker.internal'" in text
    assert "dedicated_wsl_clickhouse_required=$true" in text
    assert "docker_application_plane_allowed=$true" in text


def test_readiness_reuses_read_only_host_inventory_and_proves_production_source() -> None:
    text = source()
    for marker in (
        "inventory-global-multi-disk-host.ps1",
        "GLOBAL_MULTI_DISK_HOST_INVENTORY_NOT_PASS",
        "Get-ProductionClickHouseHealth",
        "Get-WorkerContainerCount",
        "SELECT name, path, free_space, total_space FROM system.disks WHERE name = 'default'",
        "SELECT countDistinct(table), count(), coalesce(sum(rows),0), coalesce(sum(bytes_on_disk),0) FROM system.parts WHERE active",
        "ACCEPTED_VOLUME_IDENTITY_MISMATCH",
        "RAW_DATA_PATH_NOT_ON_F",
        "drive_D_total_bytes=",
        "drive_E_total_bytes=",
        "drive_F_total_bytes=",
    ):
        assert marker in text


def test_target_layout_stays_unsized_until_262_and_340_inputs_are_admitted() -> None:
    text = source()
    assert "hot_cn=[ordered]@{ host_drive='D:'; filesystem='ext4'; capacity_bytes=$null; sizing_dependency='#262'" in text
    assert "hot_us=[ordered]@{ host_drive='D:'; filesystem='ext4'; capacity_bytes=$null; sizing_dependency='#340'" in text
    assert "warm=[ordered]@{ host_drive='E:'; filesystem='ext4'; capacity_bytes=$null; sizing_dependency='#262/#340'" in text
    assert "raw_cold=[ordered]@{ host_drive='F:'; filesystem='native_windows'" in text
    assert "WAITING_FOR_SIZING_PLAN" in text


def test_readiness_explicitly_blocks_legacy_windows_bind_and_live_cutover() -> None:
    text = source()
    for marker in (
        "legacy_windows_bind_cutover_authorized=$false",
        "live_migration_authorized=$false",
        "vhdx_create_authorized=$false",
        "vhdx_resize_authorized=$false",
        "vhdx_mount_authorized=$false",
        "source_volume_delete_authorized=$false",
        "forward_only_copy_required=$true",
        "source_volume_retained_until_final_acceptance=$true",
        "parity_gate_required=$true",
        "rollback_gate_required=$true",
        "full_cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
    ):
        assert marker in text


def test_readiness_does_not_execute_mutation_or_copy_primitives() -> None:
    text = source()
    forbidden = (
        "@('--mount'",
        "@('--unmount'",
        "'--shutdown'",
        "--unregister",
        "Mount-VHD",
        "Dismount-VHD",
        "Resize-VHD",
        "New-VHD",
        "mkfs.ext4",
        "Format-Volume",
        "docker','restart",
        "docker','prune",
        "docker','volume','rm",
        "docker','compose','stop",
        "docker','compose','down",
        "Copy-Item",
        "robocopy",
        "rsync",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in text


def test_receipt_makes_no_recopy_no_replay_and_no_mutation_explicit() -> None:
    text = source()
    for marker in (
        "raw_source_recopy_required=$false",
        "frozen_source_recopy_authorized=$false",
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
    ):
        assert marker in text
