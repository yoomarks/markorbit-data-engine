from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "freeze-production-cn-warm-source-logical-checksums.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-cn-warm-source-logical-checksum-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_gate_is_strictly_read_only_and_never_authorizes_apply() -> None:
    source = text()
    assert "[switch]$Apply" not in source
    for marker in (
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
        "cn_replay_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in source


def test_gate_is_bound_to_the_accepted_design_snapshot() -> None:
    source = text()
    for marker in (
        "58a719a60997ea09e117b0354394a7c59ba0bc23",
        "PRODUCTION_CN_WARM_MIGRATION_DESIGN_V1",
        "PRODUCTION_CN_WARM_MIGRATION_DESIGN_READY_FOR_REVIEW",
        "716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231",
        "2430570761",
        "562600035674",
        "accepted_provisioning.receipt_sha256",
        "accepted_equivalence.receipt_sha256",
    ):
        assert marker in source


def test_exact_candidate_units_are_derived_from_design_not_wildcards() -> None:
    source = text()
    assert "$receipt.candidates" in source
    assert "$plan.partitions" in source
    assert "Assert-SafeTableName" in source
    assert "table LIKE 'cn_%'" not in source
    assert "table LIKE 'cn_goods_%'" not in source


def test_logical_checksum_query_matches_frozen_cross_runtime_contract() -> None:
    source = text()
    assert "sum(cityHash64(tuple(*))) AS checksum_sum" in source
    assert "groupBitXor(cityHash64(tuple(*))) AS checksum_xor" in source
    assert "WHERE _partition_id = 'all'" in source
    assert "Assert-FrozenLogicalSql" in source
    assert "FORMAT TabSeparatedRaw" in source
    assert "exact unsigned decimal strings" in source


def test_checksum_scans_are_sequential_and_resource_bounded() -> None:
    source = text()
    for marker in (
        "[ValidateRange(1,4)]",
        "[int]$MaxThreads = 2",
        "[int]$MaxExecutionSeconds = 14400",
        "max_threads = $MaxThreads",
        "max_execution_time = $MaxExecutionSeconds",
        "max_memory_usage = 4294967296",
        "use_uncompressed_cache = 0",
        "sequential_units=$true",
    ):
        assert marker in source
    assert "ForEach-Object -Parallel" not in source
    assert "Start-Job" not in source


def test_live_schema_parts_content_and_residency_are_revalidated_before_and_after() -> None:
    source = text()
    for marker in (
        "FROM system.tables",
        "FROM system.parts",
        "create_table_query",
        "hash_of_all_files",
        "hash_of_uncompressed_files",
        "uncompressed_hash_of_compressed_files",
        "Get-LiveSchemaFingerprint",
        "Get-PartContentFingerprint",
        "Get-ResidencyFingerprint",
        "Assert-SnapshotMatchesDesign $before",
        "Assert-SnapshotMatchesDesign $after",
        "TABLE_RESIDENCY_DRIFT",
        "UNIT_RESIDENCY_DRIFT",
        "Source identity changed during checksum scan",
    ):
        assert marker in source


def test_resume_is_evidence_only_and_requires_exact_identity() -> None:
    source = text()
    assert "$ResumeEvidenceDirectory" in source
    for marker in (
        "design_receipt_sha256",
        "Checksum journal main SHA changed",
        "Checksum journal execution settings changed",
        "Resume identity drift",
        "Resume result hash drift",
        "source_identity_sha256",
        "logical_sql_sha256",
        "execution_query_sha256",
        "result_sha256",
    ):
        assert marker in source
    assert "ResumeEvidenceDirectory" in source
    assert "[switch]$Resume" not in source


def test_result_receipt_freezes_per_unit_and_overall_checksum_identity() -> None:
    source = text()
    for marker in (
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V1",
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_READY_FOR_REVIEW",
        "EXPLICIT_OPERATOR_REVIEW_OF_PROVISIONING_AUTHORIZATION",
        "logical_checksum_execution_performed=$true",
        "checksum_manifest_sha256",
        "checksum_sum",
        "checksum_xor",
        "result_sha256",
        "migration_unit_count",
    ):
        assert marker in source


def test_protected_storage_and_runtime_invariants_remain_fail_closed() -> None:
    source = text()
    for marker in (
        "markorbit-data-engine_clickhouse_data",
        "E:\\MarkOrbitData\\production\\clickhouse\\warm_cn.vhdx",
        "F:\\MarkOrbitData\\recovery\\docker_data_precompact_20260828_023021.vhdx",
        "961542094848",
        "24.8.14.39",
        "Assert-RawConsumersStopped",
        "Assert-AcceptedProductionMount",
        ".env changed during logical checksum gate",
        "Production Warm VHDX was created during read-only checksum gate",
    ):
        assert marker in source


def test_no_destructive_storage_runtime_or_clickhouse_command_surface() -> None:
    source = text()
    forbidden = (
        "New-VHD ",
        "Resize-VHD ",
        "Mount-VHD ",
        "Dismount-VHD ",
        "Optimize-VHD ",
        "--mount",
        "--unmount",
        "--shutdown",
        "--unregister",
        "docker system prune",
        "docker volume rm",
        "ALTER TABLE markorbit_facts",
        "INSERT INTO markorbit_facts",
        "MOVE PARTITION",
        "OPTIMIZE TABLE markorbit_facts",
        "DROP TABLE markorbit_facts",
        "TRUNCATE TABLE markorbit_facts",
    )
    for token in forbidden:
        assert token not in source


def test_contract_fixture_never_touches_docker_or_target_data() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split("try {", 1)[0]
    assert "Assert-FrozenLogicalSql" in fixture
    assert "Get-ExecutionQuery" in fixture
    assert "Get-UnitResultSha" in fixture
    assert "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_CONTRACT_DIRECT_INVOCATION_OK" in fixture
    assert "docker" not in fixture.lower()


def test_workflow_runs_windows_ps51_contract_and_python_static_contract() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "windows-latest",
        "powershell.exe -NoProfile",
        "-ContractOnly",
        "AcceptedDesignReceiptPath",
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_CONTRACT_DIRECT_INVOCATION_OK",
        "ubuntu-latest",
        "python -m pip install pytest",
        "test_production_cn_warm_source_logical_checksum_contract.py",
        "concurrency:",
    ):
        assert marker in source
