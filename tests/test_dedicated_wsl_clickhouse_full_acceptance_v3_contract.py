from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-dedicated-wsl-clickhouse-full-acceptance-v3.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_v3_is_exact_main_and_explicit_apply_guarded() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "Full acceptance V3 requires elevated Administrator PowerShell." in text


def test_v3_reuses_v2_and_retries_only_once_for_stable_receipt_mount_failure() -> None:
    text = source()
    assert "run-dedicated-wsl-clickhouse-full-acceptance-v2.ps1" in text
    assert "$first['runtime_stage'] -eq 'mount_external_disks'" in text
    assert "$first['server_stopped'] -eq 'True'" in text
    assert "$first['production_after_ready'] -eq 'True'" in text
    assert "$first['accepted_volume_after_present'] -eq 'True'" in text
    assert "$first['worker_count_after'] -eq '0'" in text
    assert "recovery_gate_authority=stable_ascii_v2_receipt" in text
    assert "acceptance_v3_recovery_gate=" in text
    assert "acceptance_v3_stage=v2_retry_once" in text
    assert "stale_attachment_retry_limit=1" in text
    assert text.count("Invoke-V2 -ApplyV2") == 2
    assert "WSL_E_DISK_ALREADY_MOUNTED" not in text


def test_stale_recovery_is_exact_bounded_unmount_for_retained_spike_vhdx_only() -> None:
    text = source()
    for path in (
        r"D:\MarkOrbitData\spike\hot_cn_spike.vhdx",
        r"D:\MarkOrbitData\spike\hot_us_spike.vhdx",
        r"D:\MarkOrbitData\spike\hot_global_spike.vhdx",
        r"E:\MarkOrbitData\spike\warm_spike.vhdx",
    ):
        assert path in text
    assert "Invoke-WslUnmountBounded" in text
    assert "WaitForExit($TimeoutSeconds * 1000)" in text
    assert "@('--unmount'" not in text  # no unbounded native invocation helper path
    assert "stale_attachment_recovery_scope=retained_spike_vhdx_only" in text
    assert "wsl --shutdown" not in text.lower()
    assert "--unregister" not in text
    assert "mkfs.ext4" not in text


def test_recovery_gate_emits_each_stable_ascii_safety_fact() -> None:
    text = source()
    for marker in (
        "recovery_gate_decision=",
        "recovery_gate_stage=",
        "recovery_gate_server_stopped=",
        "recovery_gate_production=",
        "recovery_gate_accepted_volume=",
        "recovery_gate_workers=",
    ):
        assert marker in text


def test_child_powershell_stderr_capture_and_safety_receipt_are_explicit() -> None:
    text = source()
    assert "$ErrorActionPreference = 'Continue'" in text
    assert "$LASTEXITCODE" in text
    for marker in (
        "wsl_shutdown_performed=False",
        "runtime_distro_unregister_performed=False",
        "spike_vhdx_delete_performed=False",
        "production_clickhouse_restart_performed=False",
        "production_clickhouse_mutation_performed=False",
        "accepted_volume_mutation_performed=False",
        "corpus_replay_performed=False",
        "DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V3_DONE",
    ):
        assert marker in text
