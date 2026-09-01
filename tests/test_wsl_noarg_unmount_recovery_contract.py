from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-wsl-noarg-unmount-recovery.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_recovery_operator_is_exact_main_and_explicit_apply() -> None:
    text = source()
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "[switch]$Apply" in text


def test_noarg_unmount_is_permanently_disabled_after_docker_sigbus() -> None:
    text = source()
    for marker in (
        "PERMANENTLY_DISABLED_AFTER_DOCKER_DESKTOP_SIGBUS",
        "WSL_NOARG_UNMOUNT_RECOVERY_PERMANENTLY_DISABLED",
        r"D:\DockerData\DockerDesktopWSL",
        "no_arg_unmount_authorized=False",
        "no_arg_unmount_performed=False",
        "permanently disabled after the Docker Desktop containerd SIGBUS incident",
    ):
        assert marker in text


def test_noarg_unmount_invocation_is_absent() -> None:
    text = source()
    forbidden = (
        "ArgumentList '--unmount'",
        "@('--unmount'",
        'wsl.exe --unmount',
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


def test_apply_path_fails_closed_without_mutation() -> None:
    text = source()
    assert "if ($Apply)" in text
    assert "throw 'No-argument WSL unmount recovery is permanently disabled" in text


def test_disabled_operator_emits_non_mutation_receipt_markers() -> None:
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
        "WSL_NOARG_UNMOUNT_RECOVERY_DISABLED_RECEIPT_DONE",
    ):
        assert marker in text
