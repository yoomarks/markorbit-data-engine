from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "resume-production-cn-warm-phase-a-mount-remediation.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-cn-warm-phase-a-mount-remediation-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def compact() -> str:
    return "".join(text().split())


def test_remediation_binds_exact_incident_and_go() -> None:
    source = text()
    for marker in (
        "111908335714292ae4d42e54b3664156d19d64ca",
        "production_cn_warm_phase_a_provisioning_20260903_072812",
        "Production runtime cannot see named Warm ext4 mount.",
        "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_JOURNAL_V1",
        "PHASE_A_CN_WARM_PROVISIONING_GO_ISSUE_506_COMMENT_5521853975",
        "$script:RemediationIssue=508",
        "runtime_import_started",
        "runtime_imported",
        "Incident journal is not exact mount-visibility failure",
    ):
        assert marker in source


def test_remediation_is_exact_three_file_descendant_boundary() -> None:
    source = text()
    for marker in (
        "$script:AllowedRemediationFiles=@(",
        "scripts/resume-production-cn-warm-phase-a-mount-remediation.ps1",
        "tests/test_production_cn_warm_phase_a_mount_remediation_contract.py",
        ".github/workflows/production-cn-warm-phase-a-mount-remediation-runtime.yml",
        "incident_to_current_changed_file_count=",
        "incident_to_current_unexpected_changed_file_count=",
        "incident_to_current_missing_remediation_file_count=",
        "merge-base",
        "--is-ancestor",
    ):
        assert marker in source


def test_only_exact_path_specific_unmount_and_remount_are_allowed() -> None:
    source = text()
    normalized = compact()
    for marker in (
        "E:\\MarkOrbitData\\production\\clickhouse\\warm_cn.vhdx",
        "markorbit_prod_warm_cn",
        "Dismount-ExactWarmVhdx",
        "@('--unmount',$script:ExpectedWarmVhdxPath)",
        "@('--mount','--vhd',$script:ExpectedWarmVhdxPath,'--name',$script:ExpectedWarmMountName)",
        "exact_path_unmount_performed",
        "named_remount_performed",
    ):
        assert marker in source or marker in normalized
    assert "@('--unmount')" not in normalized
    for forbidden in (
        "--shutdown",
        "--unregister",
        "mkfs.ext4",
        "create vdisk",
        "diskpart.exe",
        "Remove-Item",
    ):
        assert forbidden not in source


def test_remediation_refuses_fresh_provisioning_and_recreation() -> None:
    source = text()
    for marker in (
        "fresh_provisioning_authorized=False",
        "vhdx_create_authorized=False",
        "vhdx_format_authorized=False",
        "vhdx_delete_authorized=False",
        "runtime_import_authorized=False",
        "runtime_unregister_authorized=False",
        "vhdx_create_performed=$false",
        "vhdx_format_performed=$false",
        "runtime_import_performed=$false",
        "target_vhdx_delete_performed=$false",
    ):
        assert marker in source
    assert "--import" not in source
    assert "--export" not in source


def test_incident_physical_state_requires_exact_existing_runtime_uuid_and_empty_disk() -> None:
    source = text()
    for marker in (
        "MarkOrbit-ClickHouse",
        "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse",
        "Incident runtime identity mismatch",
        "Incident tooling mount is not ready",
        "Incident mount visibility failure is no longer present",
        "Get-MountUuid",
        "Incident ext4 UUID mismatch",
        "Assert-WarmEmpty",
        "Warm clickhouse-data is not empty",
        "incident_partial_state_ready=True",
    ):
        assert marker in source


def test_remediation_revalidates_authority_source_capacity_and_protected_state() -> None:
    source = text()
    for marker in (
        "07a7af0bff5b97379c1a5203059f456746f789914040da8c037a37b755cfd837",
        "ddd17889b5d7f513515fc7b3e53b1e697e5671ddcd49b7409b5a877e59c587f0",
        "716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231",
        "4aa3ae5f0d9b8c903b6275ea9a341a9b66f20843c19139a4a8355ca07e38d41a",
        "Assert-RawConsumersStopped",
        "Get-SourceHealth",
        "Assert-LiveSource",
        "SOURCE_IDENTITY_DRIFT",
        "Assert-Protected",
        "Assert-Capacity",
        "E_30_PERCENT_RESERVE_ADMISSION_FAILED",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
    ):
        assert marker in source
    assert source.count("Assert-RawConsumersStopped") >= 3
    assert source.count("Assert-LiveSource$authority") >= 2


def test_remediation_preserves_source_and_transfer_safety_boundaries() -> None:
    source = text()
    for marker in (
        "docker_mutation_authorized=False",
        "accepted_volume_mutation_authorized=False",
        "source_clickhouse_mutation_authorized=False",
        "cn_data_transfer_authorized=False",
        "cross_runtime_transfer_authorized=False",
        "cn_warm_move_authorized=False",
        "source_cleanup_authorized=False",
        "docker_mutation_performed=$false",
        "accepted_volume_mutation_performed=$false",
        "source_clickhouse_mutation_performed=$false",
        "cn_data_transfer_performed=$false",
        "cross_runtime_transfer_performed=$false",
        "cn_warm_move_performed=$false",
        "source_cleanup_performed=$false",
    ):
        assert marker in source
    lowered = source.lower()
    for forbidden in (
        "docker compose restart",
        "docker restart",
        "docker system prune",
        "docker volume rm",
        "move partition",
        "move part",
        "alter table markorbit_facts",
        "insert into markorbit_facts",
        "remote(",
        "remotesecure(",
    ):
        assert forbidden not in lowered


def test_target_clickhouse_remains_exact_version_isolated_and_empty() -> None:
    source = text()
    for marker in (
        "24.8.14.39",
        "28123",
        "29000",
        "clickhouse-common-static_",
        "sha256sum",
        "dpkg-deb -f",
        '<listen_host replace="replace">127.0.0.1</listen_host>',
        "warm_cn_only",
        "<disk>warm_cn</disk>",
        "SELECT count() FROM system.parts WHERE disk_name='warm_cn'",
        "warm_part_count",
        "empty_for_cn_migration=$true",
    ):
        assert marker in source


def test_success_stops_at_phase_b_acceptance_gate() -> None:
    source = text()
    for marker in (
        "PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_COMPLETE",
        "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE",
        "PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE",
        "PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_DONE",
        "production_cn_warm_phase_a_mount_remediation.json",
        "production_cn_warm_phase_a_provisioning_remediated.json",
    ):
        assert marker in source


def test_workflow_runs_windows_ps51_contract_and_python_static_contract() -> None:
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-powershell51" in wf
    assert "python-contract" in wf
    assert "powershell.exe" in wf
    assert "-ContractOnly" in wf
    assert "test_production_cn_warm_phase_a_mount_remediation_contract.py" in wf
