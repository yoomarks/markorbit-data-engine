from pathlib import Path


SCRIPT = Path("scripts/preflight-global-multi-disk-ext4-spike.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_preflight_is_read_only_and_has_required_decisions() -> None:
    t = text()
    assert "GLOBAL_MULTI_DISK_EXT4_SPIKE_PREFLIGHT_V1" in t
    assert "GLOBAL_MULTI_DISK_EXT4_SPIKE_PREFLIGHT_DONE" in t
    assert "READY_FOR_BOUNDED_EXT4_SPIKE" in t
    assert "SPIKE_PREFLIGHT_BLOCKED" in t
    assert "destructive_action_performed = $false" in t
    assert "vhdx_create_performed = $false" in t
    assert "vhdx_mount_performed = $false" in t
    assert "filesystem_format_performed = $false" in t
    assert "docker_restart_performed = $false" in t
    assert "clickhouse_mutation_performed = $false" in t
    assert "corpus_replay_performed = $false" in t


def test_preflight_checks_wsl_docker_clickhouse_and_vhd_primitives() -> None:
    t = text()
    assert "wsl.exe' @('--version')" in t
    assert "wsl.exe' @('--mount','--help')" in t
    assert "mkfs.ext4" in t
    assert "docker' @('info','--format','{{json .}}')" in t
    assert "docker' @('volume','inspect',$AcceptedVolume)" in t
    assert "clickhouse-client','--query','SELECT 1'" in t
    assert "Get-Command New-VHD" in t
    assert "Get-Command diskpart.exe" in t


def test_worker_probe_uses_stable_ps51_result_shape() -> None:
    t = text()
    assert "$workerProbe = Invoke-NativeText 'docker' @('ps','-aq'" in t
    assert "$workerIds = @($workerProbe['lines'] | Where-Object" in t
    assert "@(Invoke-NativeText 'docker' @('ps','-aq'" not in t


def test_preflight_does_not_contain_mutating_spike_actions() -> None:
    t = text()
    forbidden = (
        "New-VHD -Path",
        "& New-VHD",
        "Mount-VHD -Path",
        "& Mount-VHD",
        "Resize-VHD",
        "Optimize-VHD",
        "Dismount-VHD",
        "wsl.exe --mount",
        "wsl --shutdown",
        "wsl --unregister",
        "mkfs.ext4 /dev/",
        "docker system prune",
        "docker volume rm",
        "docker compose down",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "replay-us-deterministic.ps1",
        "-Apply -All",
    )
    for marker in forbidden:
        assert marker not in t
