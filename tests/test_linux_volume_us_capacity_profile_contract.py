from pathlib import Path


SCRIPT = Path("scripts/profile-linux-volume-us-capacity-target-host.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_operator_is_read_only_and_full_corpus_fail_closed() -> None:
    t = text()
    assert "LINUX_VOLUME_US_CAPACITY_PROFILE_V1" in t
    assert "full_corpus_import_authorized = $false" in t
    assert "docker_prune_performed = $false" in t
    assert "volume_resize_performed = $false" in t
    assert "service_restart_performed = $false" in t
    assert "schema_apply_performed = $false" in t
    assert "corpus_replay_performed = $false" in t
    assert "LINUX_VOLUME_US_CAPACITY_PROFILE_DONE" in t

    forbidden = (
        "docker system prune",
        "docker image prune",
        "docker volume prune",
        "docker volume rm",
        "Resize-VHD",
        "Optimize-VHD",
        "wsl --shutdown",
        "docker compose down",
        "docker compose up",
        "Remove-Item",
        "-Apply -All",
        "replay-us-deterministic.ps1",
    )
    for marker in forbidden:
        assert marker not in t


def test_operator_uses_current_linux_volume_and_remaining_plan_inputs() -> None:
    t = text()
    assert "ExpectedMainSha" in t
    assert 'AcceptedVolume = "markorbit-data-engine_clickhouse_data"' in t
    assert "HotFloorPercent = 30" in t
    assert "assert-clickhouse-active-hot-storage-contract.ps1" in t
    assert "app.storage_headroom" in t
    assert "app.us.remaining_capacity_inventory" in t
    assert "pilot_receipt.json" in t
    assert "docker' @('volume','inspect'" in t
    assert "@('system','df')" in t
    assert "DockerDesktopWSL" in t
    assert "Docker\\wsl" in t
    assert "minimum_linux_filesystem_total_bytes_for_remaining_us" in t
    assert "minimum_additional_linux_filesystem_total_bytes" in t


def test_operator_preserves_json_stdout_and_storage_contract_field_names() -> None:
    t = text()
    assert "worker python @PythonArgs 2>&1" not in t
    assert "$storageContract.mount_rw" in t
    assert "$storageContract.actual_mount_rw" not in t
