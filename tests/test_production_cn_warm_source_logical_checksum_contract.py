from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "freeze-production-cn-warm-source-logical-checksums-v2.ps1"
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


def test_v2_is_bound_to_accepted_design_and_failed_v1_engine() -> None:
    source = text()
    for marker in (
        "58a719a60997ea09e117b0354394a7c59ba0bc23",
        "55bd797274c5d678a8ab6f7d1262458eea9fcf62",
        "PRODUCTION_CN_WARM_MIGRATION_DESIGN_V1",
        "PRODUCTION_CN_WARM_MIGRATION_DESIGN_READY_FOR_REVIEW",
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V2",
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_JOURNAL_V2",
        "NULL_SAFE_JSON_TUPLE_CITYHASH64_V2",
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


def test_v1_design_sql_is_preserved_but_execution_is_deterministically_upgraded_to_v2() -> None:
    source = text()
    assert "$script:V1RowHashExpression = 'cityHash64(tuple(*))'" in source
    assert "$script:V2RowHashExpression = 'cityHash64(toJSONString(tuple(*)))'" in source
    assert "function Assert-DesignV1LogicalSql" in source
    assert "function ConvertTo-V2LogicalSql" in source
    assert ".Replace($script:V1RowHashExpression, $script:V2RowHashExpression)" in source
    assert "Accepted V1 design must contain exactly two frozen row-hash expressions" in source
    assert "V2 checksum derivation did not produce exactly two NULL-safe row-hash expressions" in source
    assert "sum\\(cityHash64\\(tuple" in source
    assert "toJSONString(tuple(*))" in source


def test_v2_checksum_is_order_independent_null_safe_and_exact_decimal() -> None:
    source = text()
    for marker in (
        "cityHash64(toJSONString(tuple(*)))",
        "sum(cityHash64",
        "groupBitXor(cityHash64",
        "FORMAT TabSeparatedRaw",
        "exact unsigned decimal strings",
        "null_safe=$true",
        "row_serialization='toJSONString(tuple(*))'",
    ):
        assert marker in source


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
        "Source identity changed during V2 checksum scan",
    ):
        assert marker in source


def test_v2_resume_is_evidence_only_and_v1_journal_cannot_be_reused() -> None:
    source = text()
    assert "$ResumeEvidenceDirectory" in source
    for marker in (
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_JOURNAL_V2",
        "V2 checksum journal main SHA changed",
        "V2 checksum journal design receipt SHA changed",
        "V2 checksum journal definition changed",
        "V2 checksum journal execution settings changed",
        "V2 resume identity drift",
        "V2 resume result hash drift",
        "source_identity_sha256",
        "design_v1_logical_sql_sha256",
        "logical_sql_v2_sha256",
        "execution_query_sha256",
        "result_sha256",
    ):
        assert marker in source
    assert "[switch]$Resume" not in source


def test_evidence_directory_is_printed_before_any_long_scan() -> None:
    source = text()
    evidence_print = source.index('Write-Host "Evidence directory: $evidenceDir"')
    unit_loop = source.index("foreach ($plan in $acceptedDesign.plans)", evidence_print)
    assert evidence_print < unit_loop
    assert "production_cn_warm_source_logical_checksum_v2_" in source
    assert "production_cn_warm_source_logical_checksum_v2_journal.json" in source


def test_result_receipt_freezes_v1_and_v2_query_identity() -> None:
    source = text()
    for marker in (
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V2_READY_FOR_REVIEW",
        "EXPLICIT_OPERATOR_REVIEW_OF_PROVISIONING_AUTHORIZATION",
        "logical_checksum_execution_performed=$true",
        "checksum_manifest_sha256",
        "checksum_sum",
        "checksum_xor",
        "design_v1_logical_sql_sha256",
        "logical_sql_v2_sha256",
        "result_sha256",
        "migration_unit_count",
        "frozen_v1_checksum_sql_preserved=$true",
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
        ".env changed during V2 logical checksum gate",
        "Production Warm VHDX was created during read-only V2 checksum gate",
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
    assert "ConvertTo-V2LogicalSql" in fixture
    assert "Get-ExecutionQuery" in fixture
    assert "Get-UnitResultSha" in fixture
    assert "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V2_CONTRACT_DIRECT_INVOCATION_OK" in fixture
    assert "docker" not in fixture.lower()


def test_workflow_runs_ps51_python_and_real_clickhouse_nullable_contract() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "freeze-production-cn-warm-source-logical-checksums-v2.ps1",
        "windows-latest",
        "powershell.exe -NoProfile",
        "-ContractOnly",
        "AcceptedDesignReceiptPath",
        "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V2_CONTRACT_DIRECT_INVOCATION_OK",
        "python -m pip install pytest",
        "test_production_cn_warm_source_logical_checksum_contract.py",
        "clickhouse/clickhouse-server:24.8.14.39",
        "Nullable(Int32)",
        "toJSONString(tuple(*))",
        "nullable_v2_checksum_rows=3",
        "concurrency:",
    ):
        assert marker in source
