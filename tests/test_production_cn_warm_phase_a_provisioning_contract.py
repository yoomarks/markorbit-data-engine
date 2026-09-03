from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-production-cn-warm-phase-a-provisioning.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-cn-warm-phase-a-provisioning-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def compact() -> str:
    return "".join(text().split())


def test_phase_a_binds_exact_operator_go_and_review_engine() -> None:
    source = text()
    for marker in (
        "4be4ef8615ed16ff8e3aafb962b476fe2605f5ef",
        "PHASE_A_CN_WARM_PROVISIONING_GO_ISSUE_506_COMMENT_5521853975",
        "$script:OperatorGoIssue = 506",
        "$script:OperatorGoCommentId = '5521853975'",
        "PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_READY_FOR_OPERATOR_GO",
        "EXPLICIT_OPERATOR_GO_REQUIRED_BEFORE_PRODUCTION_PROVISIONING_APPLY",
        "ExpectedAuthorityReviewReceiptSha256",
        "Authority review receipt SHA256 mismatch",
    ):
        assert marker in source


def test_phase_a_binds_full_accepted_evidence_chain() -> None:
    source = text()
    for marker in (
        "07a7af0bff5b97379c1a5203059f456746f789914040da8c037a37b755cfd837",
        "ddd17889b5d7f513515fc7b3e53b1e697e5671ddcd49b7409b5a877e59c587f0",
        "716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231",
        "4aa3ae5f0d9b8c903b6275ea9a341a9b66f20843c19139a4a8355ca07e38d41a",
        "2430570761",
        "562600035674",
        "candidate_count",
        "migration_unit_count",
        "accepted_design.receipt_path",
        "accepted_checksum.receipt_path",
    ):
        assert marker in source


def test_phase_a_freezes_all_four_source_identities() -> None:
    source = text()
    expected = {
        "cn_observed_event": "59118b96ccd4e6ba728b36670becf6d45bc85eb007b8f9cffd2fcfd590dd63ab",
        "cn_goods_scope_lifecycle_current": "4ad4dbfc7b8527ea512ffca5b79dcf9c381e8b7fe45a750e0ac999be3dac862a",
        "cn_goods_item_observation": "c591139333260615687087caddcd9cc91378785d64658d2796218170e6279776",
        "cn_goods_item_current": "5c1bf56661de5fbdb7cbfb4f3c9d0f797d7f23b5a55c9922e299fdbf6bf5eae3",
    }
    for table, sha in expected.items():
        assert table in source
        assert sha in source
    for marker in (
        "FROM system.tables",
        "FROM system.parts",
        "Get-LiveSchemaFingerprint",
        "Get-PartContentFingerprint",
        "Get-ResidencyFingerprint",
        "Get-SourceIdentitySha",
        "SOURCE_IDENTITY_DRIFT",
        "source_identity_ready=",
    ):
        assert marker in source
    assert "toJSONString(tuple(*))" not in source
    assert "sum(cityHash64" not in source


def test_only_exact_warm_vhdx_is_created_formatted_and_path_unmounted() -> None:
    source = text()
    normalized = compact()
    for marker in (
        "E:\\MarkOrbitData\\production\\clickhouse\\warm_cn.vhdx",
        "$script:ExpectedWarmVhdxMaxBytes = [int64]842887331840",
        "$script:ExpectedWarmVhdxMaxMiB = [int64]803840",
        "type=expandable",
        "mkfs.ext4",
        "mo_warm_cn_prod",
        "markorbit_prod_warm_cn",
        "Dismount-ExactVhdx",
        "Refusing to unmount any VHDX except the exact production Warm path",
    ):
        assert marker in source
    assert "@('--unmount',$VhdxPath)" in normalized
    assert "@('--unmount')" not in normalized
    assert "wsl.exe --unmount" not in source
    assert "Remove-Item" not in source
    assert "target_vhdx_delete_authorized=False" in source
    assert "target_vhdx_delete_performed=False" in source


def test_dedicated_wsl_runtime_uses_validated_export_import_only() -> None:
    source = text()
    normalized = compact()
    for marker in (
        "MarkOrbit-ClickHouse",
        "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse",
        "--export",
        "--import",
        "--version",
        "tooling-rootfs.tar",
        "runtime_imported",
        "WSL default distro changed",
    ):
        assert marker in source
    assert "--shutdown" not in source
    assert "--unregister" not in source
    assert "wsl --shutdown" not in source.lower()
    assert "runtime_distro_unregister_authorized=False" in source
    assert "runtime_distro_unregister_performed=False" in source
    assert "@('--import',$script:ExpectedRuntimeDistro,$script:ExpectedRuntimeRoot,$exportTar,'--version','2')" in normalized


def test_target_clickhouse_foundation_is_exact_version_and_isolated() -> None:
    source = text()
    for marker in (
        "24.8.14.39",
        "clickhouse-common-static_",
        "packages.clickhouse.com",
        "dpkg-deb -f",
        "sha256sum",
        "$script:TargetHttpPort = 28123",
        "$script:TargetNativePort = 29000",
        '<listen_host replace="replace">127.0.0.1</listen_host>',
        "warm_cn_only",
        "<disk>warm_cn</disk>",
        "/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/",
        "SELECT count() FROM system.parts WHERE disk_name='warm_cn'",
        "empty_for_cn_migration=$true",
    ):
        assert marker in source


def test_phase_a_never_transfers_or_moves_cn_data() -> None:
    source = text()
    forbidden = (
        "INSERT INTO markorbit_facts",
        "ALTER TABLE markorbit_facts",
        "MOVE PARTITION",
        "MOVE PART",
        "OPTIMIZE TABLE markorbit_facts",
        "TRUNCATE TABLE markorbit_facts",
        "DROP TABLE markorbit_facts",
        "remote(",
        "remoteSecure(",
        "clickhouse-copier",
        "BACKUP TABLE markorbit_facts",
        "RESTORE TABLE markorbit_facts",
    )
    for token in forbidden:
        assert token not in source
    for marker in (
        "cn_data_transfer_authorized=False",
        "cross_runtime_transfer_authorized=False",
        "cn_warm_move_authorized=False",
        "source_cleanup_authorized=False",
        "cn_data_transfer_performed=False",
        "cross_runtime_transfer_performed=False",
        "cn_warm_move_performed=False",
        "source_cleanup_performed=False",
    ):
        assert marker in source


def test_source_docker_plane_is_read_only() -> None:
    source = text()
    for marker in (
        "markorbit-data-engine_clickhouse_data",
        "Assert-AcceptedProductionMount",
        "Assert-SourceRuntimeReady",
        "docker' @('cp'",
        "source_clickhouse_mutation_authorized=False",
        "accepted_volume_mutation_authorized=False",
        "docker_mutation_authorized=False",
        "source_clickhouse_mutation_performed=$false",
        "accepted_volume_mutation_performed=$false",
        "docker_mutation_performed=$false",
    ):
        assert marker in source
    forbidden = (
        "docker compose restart",
        "docker compose down",
        "docker restart",
        "docker rm",
        "docker volume rm",
        "docker system prune",
        "docker volume prune",
        "docker container prune",
    )
    lowered = source.lower()
    for token in forbidden:
        assert token not in lowered


def test_fresh_capacity_and_protected_paths_are_rechecked_before_and_after() -> None:
    source = text()
    for marker in (
        "2048391114752",
        "842887331840",
        "0.30",
        "E_30_PERCENT_RESERVE_ADMISSION_FAILED",
        "recommended_30_percent_admission=",
        "D:\\MarkOrbitData\\spike\\hot_cn_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_us_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_global_spike.vhdx",
        "E:\\MarkOrbitData\\spike\\warm_spike.vhdx",
        "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse-Spike\\ext4.vhdx",
        "E:\\MarkOrbitData\\wsl-tooling\\Ubuntu-24.04\\ext4.vhdx",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "961542094848",
        "Assert-ProtectedState",
        "Assert-FreshCapacity",
    ):
        assert marker in source
    assert source.count("Assert-ProtectedState") >= 3
    assert source.count("Assert-FreshCapacity") >= 3
    assert source.count("Assert-LiveSourceIdentity $authority") >= 2


def test_phase_a_is_journaled_and_resume_is_provenance_bound() -> None:
    source = text()
    for marker in (
        "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_JOURNAL_V1",
        "ResumeEvidenceDirectory",
        "Assert-JournalIdentity",
        "Assert-ResumeArtifacts",
        "authority_review_sha256",
        "operator_go_token_sha256",
        "vhdx_create_started",
        "ext4_format_started",
        "named_mount_started",
        "tooling_export_started",
        "runtime_import_started",
        "clickhouse_install_started",
        "config_write_started",
        "server_start_started",
        "last_error",
        "journal_path=",
    ):
        assert marker in source


def test_success_stops_at_empty_disk_acceptance_gate() -> None:
    source = text()
    assert "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE" in source
    assert "PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE" in source
    assert "phase_a_apply_performed=True" in source
    assert "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_DONE" in source
    assert "phase_a_scope=EMPTY_EXT4_VHDX_DEDICATED_WSL_CLICKHOUSE_STORAGE_FOUNDATION_ONLY" in source


def test_tooling_provenance_is_exact_three_file_boundary() -> None:
    source = text()
    for marker in (
        "$script:AllowedPhaseAFiles",
        "accepted_review_to_current_changed_file_count",
        "accepted_review_to_current_unexpected_changed_file_count",
        "accepted_review_to_current_missing_phase_a_file_count",
        "changed.Count -ne 3",
        "Phase A tooling changed outside the exact 3-file boundary",
    ):
        assert marker in source


def test_contract_only_has_no_host_dependency() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split("$journal=$null", 1)[0]
    assert "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_CONTRACT_OK" in fixture
    assert "diskpart" not in fixture.lower()
    assert "wsl.exe" not in fixture.lower()
    assert "docker" not in fixture.lower()
    assert "Get-DriveCapacity" not in fixture


def test_workflow_exercises_windows_ps51_and_python_contract() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "windows-latest",
        "powershell.exe -NoProfile",
        "run-production-cn-warm-phase-a-provisioning.ps1",
        "-ContractOnly",
        "PHASE_A_CN_WARM_PROVISIONING_GO_ISSUE_506_COMMENT_5521853975",
        "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_CONTRACT_OK",
        "ubuntu-latest",
        "python -m pytest -q tests/test_production_cn_warm_phase_a_provisioning_contract.py",
        "concurrency:",
    ):
        assert marker in source
