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


def test_apply_requires_post_incident_read_only_profile_before_v2() -> None:
    text = source()
    assert "Invoke-ReadOnlyDiskProfile" in text
    assert "profile-wsl-external-disk-state.ps1" in text
    assert "acceptance_v3_stage=post_incident_read_only_resume_gate" in text
    assert "$profile = Invoke-ReadOnlyDiskProfile" in text
    assert "$resumeGateOrphanFree = [bool]($profileOrphanCount -eq '0')" in text
    assert "$resumeGateForeignMntWslClear = [bool]($profileForeignMntWslMountCount -eq '0')" in text
    assert "$profile['mnt_wsl_safety_authority'] -eq 'foreign_children_excluding_docker_desktop_namespace'" in text
    assert "$profile['production_after_ready'] -eq 'True'" in text
    assert "$profile['accepted_volume_after_present'] -eq 'True'" in text
    assert "$profile['worker_count_after'] -eq '0'" in text
    assert "$profile['no_arg_unmount_authorized'] -eq 'False'" in text
    assert "$profile['wsl_mount_performed'] -eq 'False'" in text
    assert "$profile['wsl_unmount_performed'] -eq 'False'" in text
    assert "$profile['wsl_shutdown_performed'] -eq 'False'" in text
    assert "acceptance_v3_resume_gate=" in text
    assert "resume_gate_ready=" in text
    assert "resume_gate_foreign_mnt_wsl_clear=" in text
    assert "resume_gate_mnt_wsl_authority=" in text


def test_v3_does_not_gate_on_total_mnt_wsl_count() -> None:
    text = source()
    assert "mnt_wsl_mount_count=Get-ReceiptValue" in text
    assert "docker_managed_mnt_wsl_mount_count=Get-ReceiptValue" in text
    assert "foreign_mnt_wsl_mount_count=Get-ReceiptValue" in text
    assert "$profileMntWslMountCount -eq '0'" not in text
    assert "$profileForeignMntWslMountCount -eq '0'" in text
    assert "resume_profile_docker_managed_mnt_wsl_mount_count=" in text
    assert "resume_profile_foreign_mnt_wsl_mount_count=" in text


def test_v3_runs_v2_apply_at_most_once_and_never_recovers_attachments() -> None:
    text = source()
    assert "run-dedicated-wsl-clickhouse-full-acceptance-v2.ps1" in text
    assert "acceptance_v3_stage=v2_single_attempt" in text
    assert text.count("Invoke-V2 -ApplyV2") == 1
    assert "automatic_stale_attachment_recovery_authorized=False" in text
    assert "automatic_stale_attachment_recovery_performed=False" in text
    assert "automatic_stale_attachment_retry_limit=0" in text
    assert "Invoke-WslUnmountIdentityBounded" not in text
    assert "acceptance_v3_stage=stale_attachment_recovery" not in text
    assert "acceptance_v3_stage=v2_retry_once" not in text
    assert "foreach ($identity in @('raw','extended'))" not in text
    assert "--unmount" not in text


def test_resume_gate_does_not_treat_windows_vhd_attachment_state_as_authority() -> None:
    text = source()
    assert "Get-VHD" not in text
    assert "Get-DiskImage" not in text
    assert "attached=" not in text.lower()
    assert "orphan_ext4_1g_candidate_count" in text
    assert "foreign_mnt_wsl_mount_count" in text


def test_non_apply_path_remains_read_only_v2_preflight() -> None:
    text = source()
    assert "if (-not $Apply)" in text
    assert "acceptance_v3_stage=v2_preflight" in text
    assert "$preflight = Invoke-V2" in text


def test_wsl_version_is_captured_as_advisory_evidence() -> None:
    text = source()
    assert "Get-WslVersionEvidence" in text
    assert "& wsl.exe --version" in text
    assert "wsl_version_probe_exit=" in text
    assert "wsl_version_evidence=" in text


def test_post_incident_safety_receipt_is_explicit() -> None:
    text = source()
    for marker in (
        "post_incident_profile_invoked=",
        "resume_gate_ready=",
        "resume_gate_orphan_free=",
        "resume_gate_foreign_mnt_wsl_clear=",
        "resume_gate_mnt_wsl_authority=",
        "resume_gate_production=",
        "resume_gate_accepted_volume=",
        "resume_gate_workers=",
        "resume_gate_profile_read_only=",
        "v2_single_attempt_performed=",
        "no_arg_wsl_unmount_authorized=False",
        "no_arg_wsl_unmount_performed=False",
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


def test_v3_has_no_global_or_destructive_recovery_primitive() -> None:
    text = source().lower()
    for marker in (
        "--unmount",
        "--shutdown",
        "--unregister",
        "mkfs.ext4",
        "docker','prune",
        "docker','volume','rm",
        "2023_5.zip",
        "-apply -all",
    ):
        assert marker not in text
