from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-dedicated-wsl-clickhouse-full-acceptance-v2.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_full_acceptance_is_exact_main_and_explicit_apply_guarded() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "[switch]$CleanupMounts" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "CLEANUP_MOUNTS_REQUIRED_FOR_ACCEPTANCE" in text
    assert "Working tree must be clean" in text


def test_reuses_existing_runtime_without_reinstall_or_import() -> None:
    text = source()
    assert "RUNTIME_IDENTITY_MISMATCH" in text
    assert "EXACT_CLICKHOUSE_PACKAGE_NOT_READY" in text
    for forbidden in ("--import", "--export", "curl -fL", "dpkg -i", "mkfs.ext4", "--unregister"):
        assert forbidden not in text


def test_mount_and_unmount_acceptance_are_state_authoritative_and_bounded() -> None:
    text = source()
    assert "Invoke-WslDiskCommandBounded" in text
    assert "WaitForExit($TimeoutSeconds * 1000)" in text
    assert "Get-MountProbe" in text
    assert "^ext4\\s" in text
    assert "mount_state=" in text
    assert "cleanup_state=" in text
    assert "detached=[bool](-not $after['ready'])" in text


def test_stop_uses_native_ps_and_powershell_filter_without_shell_quoting() -> None:
    text = source()
    assert "Stop-ConfigScopedServer" in text
    assert "Invoke-RuntimeTextBounded @('ps','-eo','pid=,comm=,args=')" in text
    assert "$comm -notlike 'clickhouse*'" in text
    assert "$argsText.Contains($ConfigPath)" in text
    assert "process_inspection_authority=native_ps_powershell_filter" in text
    assert "Unable to inspect config-scoped ClickHouse processes" in text
    assert "exit=$($probe['exit_code'])" in text
    assert "timed_out=$($probe['timed_out'])" in text
    assert "awk -v needle" not in text
    assert "/proc/[0-9]*" not in text
    assert '"$proc/cmdline"' not in text
    assert "$startupProbeConfigPath" in text
    assert "$fullConfigPath" in text
    assert "server_stopped=$serverStopped" in text
    assert "Full-acceptance ClickHouse process could not be stopped safely." in text


def test_uses_proven_minimal_native_config_with_four_disks_and_policies() -> None:
    text = source()
    assert "prepare_minimal_config" in text
    assert "docker cp" not in text
    assert "production-config.xml" not in text
    for disk in ("hot_cn", "hot_us", "hot_global", "warm"):
        assert f"<{disk}><type>local</type>" in text
    for policy in ("spike_hot_cn", "spike_hot_us", "spike_hot_global", "spike_warm"):
        assert f"<{policy}><volumes>" in text
    assert "system.disks" in text
    assert "system.storage_policies" in text


def test_runs_real_mergetree_acceptance_on_every_disk() -> None:
    text = source()
    assert "$InsertBatchCount = 24" in text
    assert "$RowsPerBatch = 100" in text
    assert "ENGINE=MergeTree" in text
    assert "max(level)" in text
    assert "disk_name" in text
    assert "tmp_insert_*" in text
    assert "permission denied|operation not permitted|cannot rename|failed to rename|tmp_insert_.*rename" in text
    assert "mergetree=" in text


def test_cross_runtime_connectivity_and_decisions_are_explicit() -> None:
    text = source()
    assert "docker_direct_native" in text
    assert "app_direct_http" in text
    assert "docker_stable_native" in text
    assert "app_stable_http" in text
    assert "host.docker.internal" in text
    assert "DEDICATED_WSL_CLICKHOUSE_GO" in text
    assert "WSL_CLICKHOUSE_STORAGE_GO_CONNECTIVITY_BLOCKED" in text
    assert "WSL_CLICKHOUSE_SPIKE_BLOCKED" in text


def test_production_safety_boundaries_remain_explicit() -> None:
    text = source()
    for marker in (
        "runtime_distro_unregister_performed=False",
        "spike_vhdx_delete_performed=False",
        "production_clickhouse_restart_performed=False",
        "production_clickhouse_mutation_performed=False",
        "accepted_volume_mutation_performed=False",
        "corpus_replay_performed=False",
        "DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V2_DONE",
    ):
        assert marker in text
    for forbidden in ("docker','prune", "docker','volume','rm", "2023_5.zip", "-Apply -All"):
        assert forbidden not in text
