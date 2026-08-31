from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe-dedicated-wsl-clickhouse-startup.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_probe_is_explicit_apply_and_exact_main_guarded() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "[switch]$CleanupMounts" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "Working tree must be clean" in text


def test_probe_reuses_only_exact_retained_runtime_and_version() -> None:
    text = source()
    assert "MarkOrbit-ClickHouse-Spike" in text
    assert "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse-Spike" in text
    assert "24.8.14.39" in text
    assert "RUNTIME_IDENTITY_MISMATCH" in text
    assert "EXACT_CLICKHOUSE_PACKAGE_NOT_READY" in text


def test_probe_reuses_four_ext4_vhdx_without_format_or_delete() -> None:
    text = source()
    for path in (
        "D:\\MarkOrbitData\\spike\\hot_cn_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_us_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_global_spike.vhdx",
        "E:\\MarkOrbitData\\spike\\warm_spike.vhdx",
    ):
        assert path in text
    assert "--mount','--vhd" in text
    assert "mkfs.ext4" not in text
    assert "--unregister" not in text
    assert "Remove-Item" not in text


def test_probe_uses_minimal_isolated_config_not_production_copy() -> None:
    text = source()
    assert "DEDICATED_WSL_CLICKHOUSE_STARTUP_PROBE_V1" in text
    assert "/opt/markorbit-clickhouse-startup-probe" in text
    assert "/var/lib/markorbit-clickhouse-startup-probe" in text
    assert "<logger>" in text
    assert "<user_directories>" in text
    assert "native-clickhouse-probe" in text
    assert "docker','cp" not in text
    assert "production-config.xml" not in text


def test_probe_validates_merged_config_with_clickhouse_binary() -> None:
    text = source()
    assert "extract-from-config" in text
    for key in (
        "path",
        "tmp_path",
        "user_files_path",
        "logger.log",
        "logger.errorlog",
        "storage_configuration.disks.*.path",
    ):
        assert key in text
    assert "config_extract_evidence" in text


def test_probe_captures_console_server_and_error_logs() -> None:
    text = source()
    assert "console.log" in text
    assert "log/server.log" in text
    assert "log/error.log" in text
    assert "startup-probe-runtime.log" in text
    assert "Minimal native ClickHouse startup failed" in text


def test_probe_requires_real_sql_readiness_and_exact_version() -> None:
    text = source()
    assert "--query','SELECT 1'" in text
    assert "--query','SELECT version()'" in text
    assert "NATIVE_MINIMAL_CONFIG_STARTUP_GO" in text
    assert "NATIVE_STARTUP_PROBE_BLOCKED" in text


def test_production_safety_invariants_remain_frozen() -> None:
    text = source()
    assert "worker_container_count_before" in text
    assert "worker_container_count_after" in text
    assert "production_clickhouse_before_ready" in text
    assert "production_clickhouse_after_ready" in text
    assert "runtime_distro_unregister_performed=False" in text
    assert "spike_vhdx_delete_performed=False" in text
    assert "production_clickhouse_restart_performed=False" in text
    assert "production_clickhouse_mutation_performed=False" in text
    assert "accepted_volume_mutation_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "docker','prune" not in text
    assert "docker','volume','rm" not in text
