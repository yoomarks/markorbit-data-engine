from pathlib import Path


SCRIPT = Path("scripts/preflight-dedicated-wsl-clickhouse-spike.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_preflight_is_read_only_and_emits_decision() -> None:
    t = text()
    assert "DEDICATED_WSL_CLICKHOUSE_PREFLIGHT_V1" in t
    assert "READY_FOR_DEDICATED_WSL_CLICKHOUSE_SPIKE" in t
    assert "DEDICATED_WSL_CLICKHOUSE_PREFLIGHT_BLOCKED" in t
    assert "DEDICATED_WSL_CLICKHOUSE_PREFLIGHT_DONE" in t
    assert "destructive_action_performed = $false" in t
    assert "wsl_export_performed = $false" in t
    assert "wsl_import_performed = $false" in t
    assert "distro_registration_changed = $false" in t
    assert "vhdx_mount_performed = $false" in t
    assert "filesystem_format_performed = $false" in t
    assert "clickhouse_install_performed = $false" in t
    assert "production_clickhouse_mutation_performed = $false" in t
    assert "corpus_replay_performed = $false" in t


def test_preflight_reuses_retained_four_spike_vhdx_files() -> None:
    t = text()
    for path in (
        "D:\\MarkOrbitData\\spike\\hot_cn_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_us_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_global_spike.vhdx",
        "E:\\MarkOrbitData\\spike\\warm_spike.vhdx",
    ):
        assert path in t
    for mount in (
        "markorbit_hot_cn_spike",
        "markorbit_hot_us_spike",
        "markorbit_hot_global_spike",
        "markorbit_warm_spike",
    ):
        assert mount in t
    assert "VHDX_MISSING" in t
    assert "VHDX_STILL_MOUNTED" in t


def test_preflight_freezes_dedicated_runtime_identity_and_clean_landing_zone() -> None:
    t = text()
    assert "MarkOrbit-ClickHouse-Spike" in t
    assert "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse-Spike" in t
    assert "F:\\MarkOrbitData\\spike\\MarkOrbit-ClickHouse-Spike-base.tar" in t
    assert "SPIKE_RUNTIME_DISTRO_ALREADY_REGISTERED" in t
    assert "SPIKE_RUNTIME_ROOT_ALREADY_EXISTS" in t
    assert "SPIKE_EXPORT_TAR_ALREADY_EXISTS" in t
    assert "D_DRIVE_FREE_SPACE_BELOW_10GIB" in t
    assert "F_DRIVE_FREE_SPACE_BELOW_10GIB" in t


def test_preflight_checks_production_safety_and_current_clickhouse_version() -> None:
    t = text()
    assert "WORKER_CONTAINER_PRESENT" in t
    assert "ACCEPTED_CLICKHOUSE_VOLUME_MISSING" in t
    assert "PRODUCTION_CLICKHOUSE_NOT_READY" in t
    assert "SELECT version()" in t
    assert "production_clickhouse_version=" in t
    assert "markorbit-data-engine_clickhouse_data" in t


def test_preflight_checks_tooling_distro_and_reserved_ports() -> None:
    t = text()
    assert "Ubuntu-24.04" in t
    for tool in ("mkfs.ext4", "lsblk", "blkid", "findmnt", "tar"):
        assert tool in t
    assert "18123" in t
    assert "19000" in t
    assert "SPIKE_HTTP_PORT_${SpikeHttpPort}_IN_USE" in t
    assert "SPIKE_NATIVE_PORT_${SpikeNativePort}_IN_USE" in t
    assert "CLICKHOUSE_PACKAGE_ENDPOINT_UNREACHABLE_INSTALL_METHOD_MUST_DECIDE" in t


def test_preflight_does_not_mutate_runtime_or_existing_data_plane() -> None:
    t = text()
    forbidden = (
        "wsl.exe' @('--export'",
        "wsl.exe' @('--import'",
        "wsl.exe' @('--unregister'",
        "wsl.exe' @('--mount'",
        "mkfs.ext4','-F",
        "docker compose down",
        "docker system prune",
        "docker volume rm",
        "docker restart",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
        "replay-us-deterministic.ps1",
        "Remove-Item",
    )
    for marker in forbidden:
        assert marker not in t
