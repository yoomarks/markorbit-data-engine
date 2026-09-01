from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "profile-wsl-external-disk-state.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_profile_is_exact_main_admin_and_read_only() -> None:
    text = source()
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "requires elevated Administrator PowerShell" in text
    assert "decision='WSL_EXTERNAL_DISK_STATE_PROFILE_DONE'" in text
    assert "no_arg_unmount_authorized=$false" in text
    assert "wsl_mount_performed=$false" in text
    assert "wsl_unmount_performed=$false" in text
    assert "wsl_shutdown_performed=$false" in text


def test_profile_collects_linux_and_windows_attachment_evidence() -> None:
    text = source()
    for marker in (
        "profile_step=wsl_version",
        "profile_step=wsl_list_verbose",
        "profile_step=runtime_lsblk",
        "profile_step=runtime_findmnt",
        "profile_step=runtime_blkid",
        "profile_step=runtime_dmesg_warnings",
        "profile_step=windows_get_disk",
        "profile_step=windows_get_vhd",
        "Get-VHD",
        "Get-Disk",
        "orphan_ext4_1g_candidate_count",
        "mnt_wsl_mount_count",
        "expected_spike_virtual_bytes",
    ):
        assert marker in text


def test_profile_scopes_known_spike_vhdx_and_does_not_mutate_wsl() -> None:
    text = source()
    for path in (
        r"D:\MarkOrbitData\spike\hot_cn_spike.vhdx",
        r"D:\MarkOrbitData\spike\hot_us_spike.vhdx",
        r"D:\MarkOrbitData\spike\hot_global_spike.vhdx",
        r"E:\MarkOrbitData\spike\warm_spike.vhdx",
    ):
        assert path in text
    forbidden = (
        "@('--mount'",
        "@('--unmount'",
        "'--shutdown'",
        "--unregister",
        "mkfs.ext4",
        "Format-Volume",
        "Dismount-VHD",
        "Mount-VHD",
        "docker','restart",
        "docker','prune",
        "docker','volume','rm",
        "2023_5.zip",
        "-Apply -All",
    )
    for marker in forbidden:
        assert marker not in text


def test_profile_preserves_production_invariants() -> None:
    text = source()
    for marker in (
        "Get-ProductionClickHouseHealth",
        "Get-WorkerContainerCount",
        "Test-AcceptedVolumePresent",
        "worker_container_count_before=",
        "worker_container_count_after=",
        "production_clickhouse_before_ready=",
        "production_clickhouse_after_ready=",
        "accepted_volume_before_present=",
        "accepted_volume_after_present=",
        "production_clickhouse_restart_performed=False",
        "production_clickhouse_mutation_performed=False",
        "accepted_volume_mutation_performed=False",
        "corpus_replay_performed=False",
        "WSL_EXTERNAL_DISK_STATE_READ_ONLY_PROFILE_DONE",
    ):
        assert marker in text
