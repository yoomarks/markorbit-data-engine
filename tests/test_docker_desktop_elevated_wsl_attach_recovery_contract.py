from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-docker-desktop-elevated-wsl-attach-recovery.ps1"
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "docker-desktop-elevated-wsl-attach-recovery-runtime.yml"
)


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def compact() -> str:
    return "".join(text().split()).lower()


def test_recovery_binds_exact_go_and_three_file_boundary() -> None:
    source = text()
    for marker in (
        "$script:RecoveryIssue = 517",
        "6d9d160ee7ad4714b5143ea2774a94605b47da97",
        "$script:OperatorGoCommentId = '5533302373'",
        "DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_GO_ISSUE_517_COMMENT_5533302373",
        "$script:AllowedRecoveryFiles = @(",
        "scripts/run-docker-desktop-elevated-wsl-attach-recovery.ps1",
        "tests/test_docker_desktop_elevated_wsl_attach_recovery_contract.py",
        ".github/workflows/docker-desktop-elevated-wsl-attach-recovery-runtime.yml",
        "recovery_changed_file_count=",
        "recovery_unexpected_changed_file_count=",
        "recovery_missing_file_count=",
    ):
        assert marker in source


def test_recovery_requires_apply_administrator_and_exact_preserved_vhdx() -> None:
    source = text()
    for marker in (
        "[switch]$Apply",
        "Test-IsAdministrator",
        "Administrator PowerShell is required",
        "D:\\DockerData\\DockerDesktopWSL\\disk\\docker_data.vhdx",
        "852286767104",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "961542094848",
        "E:\\MarkOrbitData\\production\\clickhouse\\warm_cn.vhdx",
        "13895729152",
        "DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_READY_FOR_APPLY",
    ):
        assert marker in source


def test_only_authorized_process_mutations_are_present() -> None:
    source = text()
    normalized = compact()
    assert "@('desktop','stop','--timeout','30')" in source
    assert "Start-Process -FilePath $script:DockerDesktopExe -Verb RunAs" in source
    assert "--force" not in normalized
    assert "stop-process" not in normalized
    assert "taskkill" not in normalized
    assert "docker desktop restart" not in normalized
    assert "docker desktop reset" not in normalized
    assert "factory reset" not in normalized
    assert "wsl.exe' @('--shutdown" not in normalized
    assert "wsl.exe' @('--unmount" not in normalized
    assert "wsl.exe' @('--mount" not in normalized
    assert "--unregister" not in normalized
    assert "--import" not in normalized
    assert "docker volume rm" not in normalized
    assert "docker system prune" not in normalized


def test_docker_checks_are_bounded_and_fail_closed() -> None:
    source = text()
    for marker in (
        "Invoke-BoundedProcess",
        "WaitForExit($TimeoutMs)",
        "$process.Kill()",
        "Docker Engine did not become responsive within bounded recovery window.",
        "Graceful Docker Desktop stop did not complete successfully.",
        "Docker Desktop/backend processes remain after graceful stop.",
    ):
        assert marker in source
    assert "@('version','--format','{{.Server.Version}}')" in source


def test_recovery_revalidates_consumer_volume_clickhouse_and_warm_nonmutation() -> None:
    source = text()
    for marker in (
        "$script:RawConsumers = @('api','worker','mark-image-worker','qcc-acquisition')",
        "Assert-RawConsumersStopped",
        "markorbit-data-engine_clickhouse_data",
        "accepted_volume_ready=",
        "SELECT version()",
        "24.8.14.39",
        "volume|$($script:AcceptedVolume)|/var/lib/clickhouse",
        "Source ClickHouse did not reach healthy state",
        "CN Warm VHDX changed during Docker recovery probe.",
    ):
        assert marker in source
    assert source.count("Assert-RawConsumersStopped") >= 3


def test_receipt_stops_at_fresh_read_only_cn_warm_gate() -> None:
    source = text()
    for marker in (
        "DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_V1",
        "DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_SUCCEEDED",
        "FRESH_READ_ONLY_CN_WARM_ATTACHMENT_GATE",
        "DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_FAILED",
        "BLOCKED_FOR_INFRASTRUCTURE_RECOVERY_REVIEW",
        "wsl_shutdown_performed = $false",
        "wsl_unmount_performed = $false",
        "wsl_mount_performed = $false",
        "docker_reset_performed = $false",
        "vhdx_mutation_performed = $false",
        "cn_warm_remount_performed = $false",
        "cn_warm_provisioning_performed = $false",
        "cn_data_transfer_performed = $false",
        "source_cleanup_performed = $false",
        "DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_DONE",
    ):
        assert marker in source


def test_workflow_runs_ps51_contract_and_static_contract() -> None:
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-powershell51" in wf
    assert "python-contract" in wf
    assert "powershell.exe" in wf
    assert "-ContractOnly" in wf
    assert "test_docker_desktop_elevated_wsl_attach_recovery_contract.py" in wf
    assert "concurrency:" in wf
    assert "cancel-in-progress:" in wf
