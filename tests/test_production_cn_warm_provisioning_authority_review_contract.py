from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review-production-cn-warm-provisioning-authority.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-cn-warm-provisioning-authority-review-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_review_is_strictly_read_only_and_never_authorizes_apply() -> None:
    source = text()
    assert "[switch]$Apply" not in source
    assert "[switch]$Resume" not in source
    for marker in (
        "review_only=True",
        "read_only=True",
        "mutation_performed=False",
        "apply_authorized=False",
        "provisioning_authorized=False",
        "cn_warm_move_authorized=False",
        "source_cleanup_authorized=False",
        "vhdx_mutation_authorized=False",
        "wsl_mutation_authorized=False",
        "docker_mutation_authorized=False",
        "clickhouse_mutation_authorized=False",
        "cross_runtime_transfer_authorized=False",
        "cn_replay_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in source


def test_review_is_bound_to_accepted_design_and_checksum_evidence() -> None:
    source = text()
    for marker in (
        "58a719a60997ea09e117b0354394a7c59ba0bc23",
        "03eff11aef70b2b55134d4c402424f4aec9e84f0",
        "07a7af0bff5b97379c1a5203059f456746f789914040da8c037a37b755cfd837",
        "716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231",
        "4aa3ae5f0d9b8c903b6275ea9a341a9b66f20843c19139a4a8355ca07e38d41a",
        "PRODUCTION_CN_WARM_MIGRATION_DESIGN_V1",
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V2",
        "NULL_SAFE_JSON_TUPLE_CITYHASH64_V2",
        "ExpectedChecksumReceiptSha256",
        "Get-FileSha256 $path",
    ):
        assert marker in source


def test_exact_four_units_and_v2_results_are_frozen() -> None:
    source = text()
    expected = (
        ("cn_observed_event", "413031435", "127856495167", "a73f666a756142d3d896ff628dcd65092baa8298f5f4d8cf61e5c5e295dcde95"),
        ("cn_goods_scope_lifecycle_current", "158355910", "4696234780", "4c407a648d6fcf9e2c41df8f2a6201661cdcf4853a65ddf31341eaa0e8f23ab4"),
        ("cn_goods_item_observation", "219463289", "58772877234", "dc7349b10a2e306e96e65ecb735f5f562a090d589e2108e1d1d7fc92db53ea98"),
        ("cn_goods_item_current", "1639720127", "371274428493", "4e8e505aa166d268e4b3e75b47a9198ec4a615aa944a93b65943c565b644550d"),
    )
    for table, rows, byte_count, result_sha in expected:
        assert table in source
        assert rows in source
        assert byte_count in source
        assert result_sha in source
    assert "2430570761" in source
    assert "562600035674" in source
    assert "$script:ExpectedUnits" in source
    assert "table LIKE 'cn_%'" not in source


def test_checksum_receipt_is_canonically_revalidated_not_just_trusted() -> None:
    source = text()
    for marker in (
        "Get-UnitResultSha",
        "V2 checksum unit result hash failed recomputation",
        "V2 checksum manifest failed canonical recomputation",
        "checksum_sum",
        "checksum_xor",
        "design_v1_logical_sql_sha256",
        "logical_sql_v2_sha256",
        "execution_query_sha256",
        "source_identity_sha256",
    ):
        assert marker in source


def test_live_source_identity_is_revalidated_without_full_logical_scan() -> None:
    source = text()
    for marker in (
        "FROM system.tables",
        "FROM system.parts",
        "hash_of_all_files",
        "hash_of_uncompressed_files",
        "uncompressed_hash_of_compressed_files",
        "Get-LiveSchemaFingerprint",
        "Get-PartContentFingerprint",
        "Get-ResidencyFingerprint",
        "Assert-SnapshotMatchesDesign",
        "Get-SourceIdentitySha",
        "SOURCE_IDENTITY_DRIFT",
        "source_identity_ready=",
    ):
        assert marker in source
    assert "toJSONString(tuple(*))" not in source.split("function Invoke-ClickHouseJsonRows", 1)[1]
    assert "sum(cityHash64" not in source
    assert "groupBitXor" not in source


def test_fresh_e_capacity_enforces_30_percent_reserve_against_exact_vhdx_max() -> None:
    source = text()
    for marker in (
        "842887331840",
        "Get-DriveCapacity 'E:\\'",
        "marginAfterMax",
        "recommended_30_percent_admission",
        "E_30_PERCENT_RESERVE_ADMISSION_FAILED",
        "e_total_bytes=",
        "e_free_bytes=",
        "e_margin_after_proposed_max_bytes=",
    ):
        assert marker in source
    assert "*0.30" in source.replace(" ", "")


def test_target_collision_review_covers_vhdx_distro_root_and_mount_name() -> None:
    source = text()
    for marker in (
        "E:\\MarkOrbitData\\production\\clickhouse\\warm_cn.vhdx",
        "MarkOrbit-ClickHouse",
        "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse",
        "markorbit_prod_warm_cn",
        "PRODUCTION_WARM_VHDX_ALREADY_EXISTS",
        "TARGET_RUNTIME_ROOT_ALREADY_EXISTS",
        "TARGET_WSL_DISTRO_ALREADY_REGISTERED",
        "TARGET_WSL_MOUNT_NAME_COLLISION",
        "Get-WslDistros",
        "Get-WslMountInventory",
    ):
        assert marker in source


def test_protected_runtime_and_storage_invariants_remain_fail_closed() -> None:
    source = text()
    for marker in (
        "markorbit-data-engine_clickhouse_data",
        "24.8.14.39",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "961542094848",
        "E:\\DockerDataBackup\\DockerDesktopWSL_20260901_before_recovery",
        "Assert-RawConsumersStopped",
        "Assert-AcceptedProductionMount",
        ".env changed during provisioning authority review",
        "Production Warm VHDX appeared during read-only review",
    ):
        assert marker in source


def test_review_tooling_provenance_is_exact_three_file_boundary() -> None:
    source = text()
    for marker in (
        "$script:AllowedReviewFiles",
        "accepted_checksum_to_current_changed_file_count",
        "accepted_checksum_to_current_unexpected_changed_file_count",
        "accepted_checksum_to_current_missing_review_file_count",
        "changed.Count -ne 3",
        "Provisioning authority review tooling changed outside the exact 3-file boundary",
    ):
        assert marker in source


def test_ready_decision_still_requires_explicit_operator_go() -> None:
    source = text()
    assert "PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_READY_FOR_OPERATOR_GO" in source
    assert "EXPLICIT_OPERATOR_GO_REQUIRED_BEFORE_PRODUCTION_PROVISIONING_APPLY" in source
    assert "PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_BLOCKED" in source
    assert "blocker_count=" in source


def test_no_destructive_storage_runtime_or_clickhouse_command_surface() -> None:
    source = text()
    forbidden = (
        "New-VHD ",
        "Resize-VHD ",
        "Mount-VHD ",
        "Dismount-VHD ",
        "Optimize-VHD ",
        "wsl.exe --mount",
        "wsl.exe --unmount",
        "--shutdown",
        "--unregister",
        "docker system prune",
        "docker volume rm",
        "docker compose restart",
        "ALTER TABLE markorbit_facts",
        "INSERT INTO markorbit_facts",
        "MOVE PARTITION",
        "OPTIMIZE TABLE markorbit_facts",
        "DROP TABLE markorbit_facts",
        "TRUNCATE TABLE markorbit_facts",
    )
    for token in forbidden:
        assert token not in source


def test_contract_fixture_never_touches_target_runtime() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split("try {", 1)[0]
    assert "PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_CONTRACT_DIRECT_INVOCATION_OK" in fixture
    assert "docker" not in fixture.lower()
    assert "wsl.exe" not in fixture.lower()
    assert "Get-DriveCapacity" not in fixture


def test_workflow_runs_windows_ps51_and_python_contract() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "windows-latest",
        "powershell.exe -NoProfile",
        "review-production-cn-warm-provisioning-authority.ps1",
        "ExpectedChecksumReceiptSha256",
        "-ContractOnly",
        "PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_CONTRACT_DIRECT_INVOCATION_OK",
        "ubuntu-latest",
        "python -m pip install pytest",
        "test_production_cn_warm_provisioning_authority_review_contract.py",
        "concurrency:",
    ):
        assert marker in source
