from pathlib import Path


SCRIPT = Path("scripts/ensure-wsl-ext4-tooling-distro.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_operator_has_guarded_two_phase_contract() -> None:
    t = text()
    assert "[switch]$Apply" in t
    assert "READY_FOR_WSL_EXT4_TOOLING_APPLY" in t
    assert "WSL_EXT4_TOOLING_READY" in t
    assert "WSL_EXT4_TOOLING_BLOCKED" in t
    assert "WSL_EXT4_TOOLING_DISTRO_V1" in t
    assert "WSL_EXT4_TOOLING_DISTRO_DONE" in t
    assert "if ($Apply)" in t
    assert "requires an elevated Administrator PowerShell session" in t


def test_operator_pins_e_drive_tooling_location_and_wsl2() -> None:
    t = text()
    assert "E:\\MarkOrbitData\\wsl-tooling\\Ubuntu-24.04" in t
    assert "'--install','-d',$DistroName,'--location',$normalizedInstallRoot,'--no-launch','--web-download'" in t
    assert "'--set-version',$DistroName,'2'" in t
    assert "EXISTING_TOOLING_DISTRO_WRONG_LOCATION" in t
    assert "INSTALL_ROOT_EXISTS_WITHOUT_REGISTERED_DISTRO" in t
    assert "DISTRO_NOT_AVAILABLE_ONLINE" in t
    assert "StartsWith('\\\\?\\')" in t
    assert "Substring(4)" in t


def test_operator_verifies_required_ext4_tools_and_preserves_default_fail_safe() -> None:
    t = text()
    for command in ("mkfs.ext4", "lsblk", "blkid", "e2fsck", "resize2fs"):
        assert command in t
    assert "apt-get install -y e2fsprogs util-linux" in t
    assert "$defaultBefore = Get-DefaultWslDistroName" in t
    assert "finally {" in t
    assert "$defaultNow = Get-DefaultWslDistroName" in t
    assert "'--set-default',$defaultBefore" in t
    assert "default_restore_performed" in t


def test_operator_uses_explicit_dictionary_access_for_ps51_receipts() -> None:
    t = text()
    for marker in (
        "$workerProbe['lines']",
        "$volumeProbe['exit_code']",
        "$wslVersion['exit_code']",
        "$toolProbeFinal['ready']",
        "$clickhouseBefore['ready']",
        "$clickhouseAfter['ready']",
    ):
        assert marker in t


def test_operator_keeps_current_production_data_plane_untouched() -> None:
    t = text()
    assert "accepted_clickhouse_volume_present" in t
    assert "clickhouse_before" in t
    assert "clickhouse_after" in t
    assert "worker_container_count_all_states" in t
    assert "wsl_shutdown_performed = $false" in t
    assert "existing_vhdx_mutation_performed = $false" in t
    assert "docker_restart_performed = $false" in t
    assert "clickhouse_mutation_performed = $false" in t
    assert "corpus_replay_performed = $false" in t


def test_operator_does_not_contain_forbidden_destructive_actions() -> None:
    t = text()
    forbidden = (
        "--unregister",
        "wsl.exe' @('--shutdown",
        "New-VHD -Path",
        "Mount-VHD -Path",
        "Resize-VHD",
        "Optimize-VHD",
        "Dismount-VHD",
        "docker system prune",
        "docker volume rm",
        "docker compose down",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "replay-us-deterministic.ps1",
        "2023_5.zip",
        "-Apply -All",
    )
    for marker in forbidden:
        assert marker not in t
