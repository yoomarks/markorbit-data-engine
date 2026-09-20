from __future__ import annotations

from pathlib import Path


RECOVERY = Path("scripts/recover-accepted-target-runtime.ps1")
RESUME = Path("scripts/resume-accepted-target-runtime.ps1")


def recovery_text() -> str:
    return RECOVERY.read_text(encoding="utf-8").lower()


def resume_text() -> str:
    return RESUME.read_text(encoding="utf-8").lower()


def test_full_recovery_uses_clickhouse_daemon_pidfile_not_shell_background_pid() -> None:
    value = recovery_text()
    assert "--daemon --pid-file='$pidfile'" in value
    assert "nohup clickhouse server" not in value
    assert "echo $!" not in value
    assert "accepted target server pid is invalid" in value
    assert "exited immediately after daemon launch" in value


def test_resume_binds_exact_partial_recovery_boundary() -> None:
    value = resume_text()
    assert "accepted-target-runtime-resume-r1" in value
    assert "8717454aad7b0887230dd5274c397de75a8b6cf3b5b362955aa31e78ba4e03db" in value
    assert "accepted_target_runtime_recovery_journal.json" in value
    assert "prior_journal_sha256" in value
    assert "mounts_accepted_server_absent" in value
    assert "accepted target server already exists" in value
    assert "accepted target ports are already listening" in value
    assert "expected failed server attempt to leave only an empty/whitespace pidfile" in value


def test_resume_revalidates_exact_mounts_and_config_before_server_start() -> None:
    value = resume_text()
    assert "521a7b20-4380-4d6a-8018-2bab78fc2c4b" in value
    assert "274877906944" in value
    assert "2ee74d16-f0bd-461b-ab6a-279603e6c570" in value
    assert "842887331840" in value
    assert "c7240b6c05a96dff2dc4c9e5a801cd524065bd101b5d006f2e8610b63ca56a59" in value
    assert "16b281607c47f9ee1f1bd8e3d09c4fc556320e833f17d05b597dec78aa2eb233" in value
    assert "tail\\s+-f\\s+/dev/null" in value


def test_resume_has_no_storage_attachment_or_destructive_surface() -> None:
    value = resume_text()
    forbidden = (
        "--mount",
        "--unmount",
        "--shutdown",
        "--unregister",
        "new-vhd",
        "resize-vhd",
        "format-volume",
        "mkfs.",
        "docker restart",
        "docker stop",
        "docker rm",
        "insert into",
        "create table",
        "alter table",
        "optimize table",
        "move partition",
        "delete where",
        "truncate table",
    )
    for token in forbidden:
        assert token not in value, token
    assert "mount=$false" in value
    assert "unmount=$false" in value
    assert "format=$false" in value
    assert "resize=$false" in value


def test_resume_uses_daemon_pidfile_and_requires_live_pid() -> None:
    value = resume_text()
    assert "--daemon --pid-file='$($script:pidpath)'" in value
    assert "test -s '$($script:pidpath)' && cat '$($script:pidpath)'" in value
    assert "kill -0 '$serverpidvalue'" in value
    assert "$pid =" not in value
    assert "accepted target daemon exited immediately" in value
    assert "nohup clickhouse server" not in value
    assert "echo $!" not in value


def test_resume_writes_separate_receipt_and_never_rewrites_prior_journal() -> None:
    value = resume_text()
    assert "resume-apply" in value
    assert "accepted_target_runtime_resume_journal.json" in value
    assert "accepted_target_runtime_recovery_receipt.json" in value
    assert "automatic_unmount_or_rollback_performed=false" in value
    assert "decision='accepted_target_runtime_recovered'" in value


def test_keeper_command_line_is_optional_when_windows_hides_it() -> None:
    value = resume_text()
    assert "command_line_visible" in value
    assert "if ($commandlinevisible)" in value
    assert "--list','--running','--quiet" in value
    assert "accepted target distro is not currently running" in value
    assert "process_name=[string]$process.name" in value
    assert "target_distro_running=$true" in value


def test_resume_accepts_only_empty_or_whitespace_failed_pidfile() -> None:
    value = resume_text()
    assert "whitespace_only" in value
    assert "nonempty_data" in value
    assert "tr -d '[:space:]'" in value
    assert "@('empty','whitespace_only')" in value
    assert "empty/whitespace pidfile" in value


def test_runtime_validators_are_policy_aware_and_avoid_readonly_pid_variable() -> None:
    for value in (recovery_text(), resume_text()):
        assert "$pid =" not in value
        assert "t.storage_policy='hot_us_only' and p.disk_name!='hot_us'" in value
        assert "t.storage_policy='warm_cn_only' and p.disk_name!='warm_cn'" in value
        assert "t.storage_policy='default' and p.disk_name!='default'" in value
        assert "placement_mismatches=0" in value
