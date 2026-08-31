from pathlib import Path


SCRIPT = Path("scripts/inventory-global-multi-disk-host.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_inventory_is_read_only_and_fail_closed() -> None:
    t = text()
    assert "GLOBAL_MULTI_DISK_HOST_INVENTORY_V1" in t
    assert "GLOBAL_MULTI_DISK_HOST_INVENTORY_DONE" in t
    assert "destructive_action_performed = $false" in t
    assert "vhdx_create_performed = $false" in t
    assert "vhdx_mount_performed = $false" in t
    assert "vhdx_resize_performed = $false" in t
    assert "vhdx_move_performed = $false" in t
    assert "docker_restart_performed = $false" in t
    assert "docker_prune_performed = $false" in t
    assert "clickhouse_mutation_performed = $false" in t
    assert "corpus_replay_performed = $false" in t

    forbidden = (
        "New-VHD",
        "Mount-VHD",
        "Resize-VHD",
        "Optimize-VHD",
        "Dismount-VHD",
        "wsl --shutdown",
        "wsl --unregister",
        "docker system prune",
        "docker image prune",
        "docker volume prune",
        "docker volume rm",
        "docker compose down",
        "docker compose up",
        "Remove-Item",
        "Move-Item",
        "-Apply -All",
        "replay-us-deterministic.ps1",
    )
    for marker in forbidden:
        assert marker not in t


def test_inventory_freezes_approved_tier_names_without_equal_split() -> None:
    t = text()
    assert "hot_cn" in t
    assert "hot_us" in t
    assert "hot_global" in t
    assert "host_drive = 'D:'" in t
    assert "name = 'warm'; host_drive = 'E:'; filesystem = 'ext4'" in t
    assert "host_drive = 'F:'" in t
    assert "clickhouse_mergetree_primary_parts_allowed = $false" in t
    assert "capacity_bytes = $null" in t
    assert "sizing_dependency = '#262'" in t
    assert "sizing_dependency = '#340'" in t


def test_inventory_captures_physical_and_vhdx_evidence() -> None:
    t = text()
    assert "Get-CimInstance Win32_LogicalDisk" in t
    assert "Get-CimAssociatedInstance" in t
    assert "Get-Disk -Number" in t
    assert "Get-PhysicalDisk" in t
    assert "DockerData" in t
    assert "MarkOrbitData" in t
    assert "Docker\\wsl" in t
    assert "docker' @('volume','inspect'" in t
