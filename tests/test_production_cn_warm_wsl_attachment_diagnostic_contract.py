from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose-production-cn-warm-wsl-attachment.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-cn-warm-wsl-attachment-diagnostic-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def compact() -> str:
    return "".join(text().split())


def test_diagnostic_binds_exact_incident_and_latest_remediation_failure() -> None:
    source = text()
    for marker in (
        "$script:DiagnosticIssue = 512",
        "cf9a2489f057b70b96c28cf35835f796eb6d4c74",
        "111908335714292ae4d42e54b3664156d19d64ca",
        "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_JOURNAL_V1",
        "PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_JOURNAL_V2",
        "MOUNT_ALREADY_DETACHED",
        "WSL_E_DISK_ALREADY_MOUNTED",
        "production_cn_warm_phase_a_mount_remediation_journal.json",
    ):
        assert marker in source


def test_diagnostic_has_no_apply_or_wsl_disk_mutation_primitives() -> None:
    source = text()
    normalized = compact().lower()
    assert "[switch]$apply" not in normalized
    for forbidden in (
        "@('--mount'",
        '@("--mount"',
        "@('--unmount'",
        '@("--unmount"',
        "--shutdown",
        "--unregister",
        "--import",
        "mkfs.ext4",
        "diskpart.exe",
        "create vdisk",
        "remove-item",
    ):
        assert forbidden not in normalized
    for marker in (
        "diagnostic_only=True",
        "read_only=True",
        "mutation_performed=False",
        "wsl_mount_authorized=False",
        "wsl_unmount_authorized=False",
        "vhdx_mutation_authorized=False",
        "cn_data_transfer_authorized=False",
        "cn_warm_move_authorized=False",
    ):
        assert marker in source


def test_uuid_probe_uses_only_read_only_linux_inventory_commands() -> None:
    source = text()
    for marker in (
        "blkid -t UUID=",
        "lsblk -b -P -o NAME,PATH,TYPE,FSTYPE,UUID,SIZE,MOUNTPOINTS",
        "findmnt -rn -S",
        "findmnt -rn -T",
        "findmnt -rn -R /mnt/wsl",
        "ls -la /mnt/wsl",
        "Get-UuidNamespaceProbe",
    ):
        assert marker in source


def test_attachment_classification_is_fail_closed_and_explicit() -> None:
    source = text()
    for marker in (
        "UUID_DEVICE_ABSENT",
        "UUID_DEVICE_NAMED_MOUNT_VISIBLE",
        "UUID_DEVICE_MOUNTED_ELSEWHERE",
        "UUID_DEVICE_ATTACHED_UNMOUNTED_VISIBLE_BOTH",
        "UUID_DEVICE_NAMESPACE_DIVERGENCE",
        "attachment_classification=",
        "OPERATOR_REVIEW_OF_WSL_ATTACHMENT_DIAGNOSTIC",
    ):
        assert marker in source


def test_diagnostic_revalidates_runtime_source_protected_and_capacity() -> None:
    source = text()
    for marker in (
        "Assert-RuntimeIdentity",
        "Assert-RawConsumersStopped",
        "Assert-SourceMount",
        "accepted_production_mount_ready=",
        "Assert-ProtectedAndCapacity",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "E_30_PERCENT_RESERVE_ADMISSION_FAILED",
        "warm_vhdx_length_bytes=",
    ):
        assert marker in source
    assert source.count("Assert-RawConsumersStopped") >= 3
    assert source.count("Assert-SourceMount") >= 3
    assert source.count("Assert-ProtectedAndCapacity") >= 3


def test_diagnostic_preserves_exact_three_file_boundary() -> None:
    source = text()
    for marker in (
        "$script:AllowedDiagnosticFiles = @(",
        "scripts/diagnose-production-cn-warm-wsl-attachment.ps1",
        "tests/test_production_cn_warm_wsl_attachment_diagnostic_contract.py",
        ".github/workflows/production-cn-warm-wsl-attachment-diagnostic-runtime.yml",
        "diagnostic_changed_file_count=",
        "diagnostic_unexpected_changed_file_count=",
        "diagnostic_missing_file_count=",
        "merge-base",
        "--is-ancestor",
    ):
        assert marker in source


def test_receipt_is_diagnostic_only_and_stops_at_operator_review() -> None:
    source = text()
    for marker in (
        "PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_V1",
        "PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_COMPLETE",
        "production_cn_warm_wsl_attachment_diagnostic.json",
        "wsl_mount_performed=$false",
        "wsl_unmount_performed=$false",
        "vhdx_mutation_performed=$false",
        "source_clickhouse_mutation_performed=$false",
        "cn_data_transfer_performed=$false",
        "cn_warm_move_performed=$false",
        "source_cleanup_performed=$false",
        "PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_DONE",
    ):
        assert marker in source


def test_workflow_runs_windows_ps51_contract_and_python_static_contract() -> None:
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-powershell51" in wf
    assert "python-contract" in wf
    assert "powershell.exe" in wf
    assert "-ContractOnly" in wf
    assert "test_production_cn_warm_wsl_attachment_diagnostic_contract.py" in wf
