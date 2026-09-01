from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe-dedicated-wsl-clickhouse-startup-v3.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_v3_runs_v2_readonly_preflight_before_any_mount() -> None:
    text = source()
    assert "probe_v3_stage=v2_preflight" in text
    assert "READY_FOR_NATIVE_STARTUP_PROBE_V2" in text
    assert text.index("probe_v3_stage=v2_preflight") < text.index("probe_v3_stage=state_authoritative_mount")


def test_v3_child_powershell_stderr_is_captured_without_stop_promotion() -> None:
    text = source()
    invoke_v2 = text[text.index("function Invoke-V2"):text.index("try {\n    Write-Host '===== DEDICATED WSL CLICKHOUSE STARTUP PROBE V3 ====='")]
    assert "$previous = $ErrorActionPreference" in invoke_v2
    assert "$ErrorActionPreference = 'Continue'" in invoke_v2
    assert "$output = @(& powershell.exe @args 2>&1)" in invoke_v2
    assert "$exitCode = $LASTEXITCODE" in invoke_v2
    assert "finally { $ErrorActionPreference = $previous }" in invoke_v2
    assert "child_powershell_stderr_capture_safe=True" in text
    assert "$lines = @(& powershell.exe @args 2>&1" not in text


def test_v3_does_not_shadow_typed_apply_switch_with_nested_result() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "$v2ApplyResult = Invoke-V2 -ApplyV2" in text
    assert "$nestedDecision = $v2ApplyResult['decision']" in text
    assert "$v2ApplyResult['exit_code']" in text
    assert "$apply = Invoke-V2 -ApplyV2" not in text
    assert "v3_apply_switch_collision_safe=True" in text


def test_v3_accepts_real_ext4_state_not_wsl_exit_code() -> None:
    text = source()
    assert "Get-MountProbe" in text
    assert "findmnt" in text
    assert "^ext4\\s" in text
    assert "mount_acceptance_authority=findmnt_ext4_state" in text
    assert "MOUNT_COMMAND_EXIT_NONZERO_STATE_VERIFIED_" in text
    assert "if (-not $verified)" in text
    assert "if ($mount['exit_code'] -ne 0) { throw" not in text


def test_v3_verifies_actual_detach_after_cleanup() -> None:
    text = source()
    assert "probe_v3_stage=state_authoritative_cleanup" in text
    assert "unmount_acceptance_authority=findmnt_detached_state" in text
    assert "UNMOUNT_COMMAND_EXIT_NONZERO_STATE_VERIFIED_" in text
    assert "NATIVE_STARTUP_PROBE_V3_CLEANUP_BLOCKED" in text
    assert "$detached = [bool](-not $afterCleanup['ready'])" in text


def test_v3_keeps_disk_calls_bounded() -> None:
    text = source()
    assert "Invoke-WslDiskCommandBounded" in text
    assert "MountTimeoutSeconds = 30" in text
    assert "WaitForExit($TimeoutSeconds * 1000)" in text
    assert "command_timed_out" in text


def test_v3_delegates_startup_to_already_accepted_v2() -> None:
    text = source()
    assert "probe-dedicated-wsl-clickhouse-startup-v2.ps1" in text
    assert "probe_v3_stage=v2_apply" in text
    assert "-Apply','-CleanupMounts" in text
    assert "nested_decision" in text


def test_v3_reuses_only_retained_nonprod_vhdx() -> None:
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


def test_v3_preserves_production_safety_markers() -> None:
    text = source()
    for marker in (
        "runtime_distro_unregister_performed=False",
        "spike_vhdx_delete_performed=False",
        "production_clickhouse_restart_performed=False",
        "production_clickhouse_mutation_performed=False",
        "accepted_volume_mutation_performed=False",
        "corpus_replay_performed=False",
        "DEDICATED_WSL_CLICKHOUSE_STARTUP_PROBE_V3_DONE",
    ):
        assert marker in text
