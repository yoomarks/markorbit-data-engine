from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe-dedicated-wsl-clickhouse-startup-v2.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_v2_is_exact_main_and_explicit_apply_guarded() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "[switch]$CleanupMounts" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "Working tree must be clean" in text


def test_v2_bounds_wsl_disk_and_runtime_commands() -> None:
    text = source()
    assert "Invoke-WslDiskCommandBounded" in text
    assert "MountTimeoutSeconds = 30" in text
    assert "RuntimeTimeoutSeconds = 20" in text
    assert "WaitForExit($TimeoutSeconds * 1000)" in text
    assert "exitCode = if ($timedOut) { 124 }" in text
    assert "timeout','--signal=TERM','--kill-after=5s'" in text
    assert "timed_out_step" in text


def test_v2_emits_per_step_progress_markers() -> None:
    text = source()
    for marker in (
        "probe_step=mount_",
        "probe_step=prepare_disk_",
        "probe_step=write_config",
        "probe_step=config_extract_",
        "probe_step=start_daemon",
        "probe_step=readiness_wait",
        "probe_step=collect_logs",
        "probe_step=stop_server",
        "probe_step=cleanup_unmount_",
    ):
        assert marker in text


def test_v2_uses_clickhouse_daemon_not_nohup_backgrounding() -> None:
    text = source()
    assert "--daemon --pid-file=" in text
    assert "nohup clickhouse server" not in text
    assert "NATIVE_MINIMAL_CONFIG_STARTUP_V2_GO" in text
    assert "NATIVE_STARTUP_PROBE_V2_BLOCKED" in text


def test_v2_reuses_only_retained_nonprod_disks() -> None:
    text = source()
    for path in (
        "D:\\MarkOrbitData\\spike\\hot_cn_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_us_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_global_spike.vhdx",
        "E:\\MarkOrbitData\\spike\\warm_spike.vhdx",
    ):
        assert path in text
    assert "mkfs.ext4" not in text
    assert "--unregister" not in text
    assert "Remove-Item" not in text
    assert "spike_vhdx_delete_performed=False" in text


def test_v2_keeps_production_safety_invariants() -> None:
    text = source()
    assert "worker_container_count_before" in text
    assert "worker_container_count_after" in text
    assert "production_clickhouse_before_ready" in text
    assert "production_clickhouse_after_ready" in text
    assert "runtime_distro_unregister_performed=False" in text
    assert "production_clickhouse_restart_performed=False" in text
    assert "production_clickhouse_mutation_performed=False" in text
    assert "accepted_volume_mutation_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "docker','prune" not in text
    assert "docker','volume','rm" not in text
