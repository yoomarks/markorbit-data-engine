from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-wsl-noarg-unmount-recovery.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_recovery_is_exact_main_admin_and_explicit_apply() -> None:
    text = source()
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "[switch]$Apply" in text
    assert "requires elevated Administrator PowerShell" in text
    assert "READY_FOR_WSL_NOARG_UNMOUNT_RECOVERY" in text
    assert "WSL_NOARG_UNMOUNT_RECOVERY_GO" in text
    assert "WSL_NOARG_UNMOUNT_RECOVERY_BLOCKED" in text


def test_recovery_gate_requires_single_expected_orphan_and_no_external_mounts() -> None:
    text = source()
    for marker in (
        "$expectedSpikeVirtualBytes = 1073741824",
        "$expectedOrphanLabel = 'mo_hot_cn_spike'",
        "one_expected_orphan",
        "no_foreign_spike_shape",
        "no_external_mounts",
        "single_expected_labeled_orphan_and_zero_external_mounts",
        "spike_shape_candidate_count_before",
        "expected_orphan_candidate_count_before",
        "foreign_spike_shape_candidate_count_before",
        "external_mount_count_before",
        "expected_orphan_candidate_count_after",
        "external_mount_count_after",
    ):
        assert marker in text


def test_recovery_preserves_retained_spike_file_scope_and_production_invariants() -> None:
    text = source()
    for path in (
        r"D:\MarkOrbitData\spike\hot_cn_spike.vhdx",
        r"D:\MarkOrbitData\spike\hot_us_spike.vhdx",
        r"D:\MarkOrbitData\spike\hot_global_spike.vhdx",
        r"E:\MarkOrbitData\spike\warm_spike.vhdx",
    ):
        assert path in text
    for marker in (
        "Get-ProductionClickHouseHealth",
        "Get-WorkerContainerCount",
        "Test-AcceptedVolumePresent",
        "worker_container_count_before=",
        "worker_container_count_after=",
        "production_clickhouse_before_ready=",
        "production_clickhouse_after_ready=",
        "accepted_volume_before_present=",
        "accepted_volume_after_present=",
    ):
        assert marker in text


def test_recovery_allows_exactly_one_noarg_unmount_and_forbids_stronger_mutations() -> None:
    text = source()
    assert text.count("ArgumentList '--unmount'") == 1
    assert "no_arg_unmount_attempt_limit=1" in text
    assert "no_arg_unmount_exit_authority=evidence_only_post_lsblk_state_is_authoritative" in text
    forbidden = (
        "@('--mount'",
        "'--shutdown'",
        "--unregister",
        "mkfs.ext4",
        "Format-Volume",
        "Dismount-VHD",
        "Mount-VHD",
        "docker','restart",
        "docker','prune",
        "docker','volume','rm",
        "2023_5.zip",
        "-Apply -All",
    )
    for marker in forbidden:
        assert marker not in text


def test_recovery_emits_explicit_non_mutation_receipt_markers() -> None:
    text = source()
    for marker in (
        "wsl_mount_performed=False",
        "wsl_shutdown_performed=False",
        "runtime_distro_unregister_performed=False",
        "spike_vhdx_mutation_performed=False",
        "production_clickhouse_restart_performed=False",
        "production_clickhouse_mutation_performed=False",
        "accepted_volume_mutation_performed=False",
        "corpus_replay_performed=False",
        "WSL_NOARG_UNMOUNT_RECOVERY_DONE",
    ):
        assert marker in text
