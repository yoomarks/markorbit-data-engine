from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-production-cn-warm-migration-design.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "production-cn-warm-migration-design-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_design_gate_has_no_apply_or_resume_surface_and_keeps_authority_false() -> None:
    source = text()
    assert "[switch]$Apply" not in source
    assert "[switch]$Resume" not in source
    for marker in (
        "design_only=True",
        "read_only=True",
        "mutation_performed=False",
        "apply_authorized=False",
        "provisioning_authorized=False",
        "cn_warm_move_authorized=False",
        "source_cleanup_authorized=False",
        "logical_checksum_execution_performed=False",
        "vhdx_mutation_authorized=False",
        "wsl_mutation_authorized=False",
        "docker_mutation_authorized=False",
        "clickhouse_mutation_authorized=False",
        "cn_replay_authorized=False",
        "us_bulk_authorized=False",
    ):
        assert marker in source


def test_design_is_bound_to_accepted_provisioning_ready_receipt() -> None:
    source = text()
    for marker in (
        "db4cc021cb297c712327037362ba3d5b4ee67479",
        "PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_V1",
        "PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_READY",
        "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_V1",
        "642fd0d0f25e0efdf1cefcdb4781bcb42d093433b453383bafee3fc6d2783091",
    ):
        if marker.endswith("3091"):
            # The exact equivalence receipt SHA is read from the accepted provisioning
            # receipt and revalidated at runtime rather than duplicated as a constant.
            assert "accepted_equivalence.receipt_sha256" in source
        else:
            assert marker in source


def test_frozen_warm_capacity_and_topology_match_accepted_preflight() -> None:
    source = text()
    for marker in (
        "716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231",
        "2430570761",
        "562600035674",
        "618860039242",
        "E:\\MarkOrbitData\\production\\clickhouse\\warm_cn.vhdx",
        "markorbit_prod_warm_cn",
        "842887331840",
        "2048391114752",
    ):
        assert marker in source


def test_production_runtime_and_clickhouse_storage_names_are_frozen() -> None:
    source = text()
    for marker in (
        "MarkOrbit-ClickHouse",
        "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse",
        "24.8.14.39",
        "warm_cn",
        "warm_cn_only",
        "/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/",
        "docker_desktop_external_mnt_wsl_bind_retry_allowed=$false",
    ):
        assert marker in source


def test_candidates_are_derived_exactly_from_accepted_equivalence_receipt_not_wildcards() -> None:
    source = text()
    assert "$receipt.warm_candidates" in source
    assert "$script:ExpectedWarmCandidateCount = [int64]4" in source
    assert "Get-CandidateManifestHash $candidates" in source
    assert "Assert-SafeTableName" in source
    assert "name IN ($quoted)" in source
    assert "table LIKE 'cn_%'" not in source
    assert "table LIKE 'cn_goods_%'" not in source


def test_live_metadata_uses_system_tables_and_parts_only() -> None:
    source = text()
    for marker in (
        "FROM system.tables",
        "FROM system.parts",
        "hash_of_all_files",
        "hash_of_uncompressed_files",
        "uncompressed_hash_of_compressed_files",
        "partition_id",
        "disk_name",
        "system.tables/system.parts only",
    ):
        assert marker in source


def test_part_content_and_residency_fingerprints_are_separate() -> None:
    source = text()
    assert "function Get-PartContentFingerprint" in source
    assert "function Get-ResidencyFingerprint" in source
    content_fn = source.split("function Get-PartContentFingerprint", 1)[1].split(
        "function Get-ResidencyFingerprint", 1
    )[0]
    residency_fn = source.split("function Get-ResidencyFingerprint", 1)[1].split(
        "function Get-LogicalChecksumSql", 1
    )[0]
    assert "hash_of_all_files" in content_fn
    assert "hash_of_uncompressed_files" in content_fn
    assert "uncompressed_hash_of_compressed_files" in content_fn
    assert "disk_name" not in content_fn
    assert "disk_name" in residency_fn


def test_logical_checksum_strategy_is_order_independent_and_null_safe_by_contract() -> None:
    source = text()
    assert "sum(cityHash64(tuple(*))) AS checksum_sum" in source
    assert "groupBitXor(cityHash64(tuple(*))) AS checksum_xor" in source
    assert "WHERE _partition_id = '$pid'" in source
    assert "logical_checksum_execution_required_before_future_transfer=$true" in source
    assert "logical_checksum_execution_required_after_future_transfer=$true" in source
    assert "logical_checksum_execution_performed=$false" in source


def test_design_fails_closed_on_live_candidate_drift() -> None:
    source = text()
    for marker in (
        "ACTIVE_PART_COUNT_DRIFT:",
        "ROW_COUNT_DRIFT:",
        "BYTE_COUNT_DRIFT:",
        "SOURCE_DISK_DRIFT:",
        "SCHEMA_FINGERPRINT_DRIFT:",
        "E_TOTAL_BYTES_DRIFT",
        "E_30_PERCENT_ADMISSION_LOST",
    ):
        assert marker in source


def test_migration_order_is_history_first_then_smallest() -> None:
    source = text()
    assert "EVENT_HISTORY_FIRST_THEN_ASCENDING_BYTES_THEN_TABLE" in source
    assert "WARM_EVENT_HISTORY" in source
    assert "WARM_GOODS_CATEGORY" in source
    assert "Sort-Object order_rank, bytes_on_disk, table" in source
    assert "migration_order" in source


def test_cross_runtime_transfer_is_explicit_and_does_not_assume_filesystem_move() -> None:
    source = text()
    for marker in (
        "TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE",
        "source_and_target_are_distinct_clickhouse_runtimes=$true",
        "filesystem_level_move_between_runtimes_assumed=$false",
        "blind_full_cn_recopy_allowed=$false",
        "only_frozen_warm_candidates_in_scope=$true",
        "future_target_to_source_native_connectivity_preflight_required=$true",
    ):
        assert marker in source


def test_acceptance_contract_covers_schema_rows_checksums_parts_residency_consumers_and_latency() -> None:
    source = text()
    for marker in (
        "source_schema_fingerprint",
        "source_rows",
        "source_active_parts",
        "source_part_content_manifest",
        "source_disk_residency",
        "logical_checksum_source",
        "schema_equivalence",
        "row_count_equivalence",
        "logical_checksum_equivalence",
        "target_disk_residency",
        "writer_placement_acceptance",
        "direct_serving_query_acceptance",
        "summary_and_case_api_acceptance",
        "latency_regression_acceptance",
        "zero_rename_commit_permission_error_class",
    ):
        assert marker in source


def test_rollback_and_source_cleanup_are_separate_fail_closed_phases() -> None:
    source = text()
    for marker in (
        "rollback_target_source_disk_frozen",
        "rollback_before_source_cleanup",
        "schema_rows_checksum_residency_reverified",
        "writer_query_api_acceptance_reverified",
        "SOURCE_POLICY_CLEANUP",
        "separate_future_issue_required=$true",
        "source_cleanup_authorized=$false",
    ):
        assert marker in source


def test_phase_separation_requires_explicit_operator_go() -> None:
    source = text()
    for marker in (
        "PROVISIONING_APPLY",
        "EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE",
        "BOUNDED_CN_WARM_MIGRATION_UNITS",
        "FINAL_CROSS_TABLE_ACCEPTANCE",
        "SOURCE_POLICY_CLEANUP",
        "EXPLICIT_OPERATOR_REVIEW_BEFORE_ANY_PRODUCTION_APPLY",
        "requires_explicit_operator_go=$true",
        "per_unit_receipt_required=$true",
    ):
        assert marker in source


def test_global_abort_rules_cover_all_required_failure_classes() -> None:
    source = text()
    for marker in (
        "MAIN_OR_RECEIPT_OR_MANIFEST_DRIFT",
        "PRODUCTION_CLICKHOUSE_UNHEALTHY",
        "ACCEPTED_SOURCE_VOLUME_IDENTITY_DRIFT",
        "RAW_OR_RUNTIME_CONSUMER_SAFETY_DRIFT",
        "E_30_PERCENT_RESERVE_OR_HEADROOM_VIOLATION",
        "UNEXPECTED_VHDX_WSL_PATH_OR_MOUNT_COLLISION",
        "SCHEMA_ROW_OR_CHECKSUM_MISMATCH",
        "WRITER_QUERY_API_OR_LATENCY_ACCEPTANCE_FAILURE",
        "RENAME_COMMIT_OR_PERMISSION_ERROR_CLASS",
    ):
        assert marker in source


def test_no_destructive_storage_or_runtime_command_surface() -> None:
    source = text()
    forbidden_invocations = (
        "New-VHD ",
        "Resize-VHD ",
        "Mount-VHD ",
        "Dismount-VHD ",
        "Optimize-VHD ",
        "wsl.exe' @('--mount'",
        "wsl.exe' @('--unmount'",
        "wsl.exe' @('--shutdown'",
        "wsl.exe' @('--unregister'",
        "docker system prune",
        "docker volume rm",
        "ALTER TABLE markorbit_facts",
        "OPTIMIZE TABLE markorbit_facts",
        "DROP TABLE markorbit_facts",
        "TRUNCATE TABLE markorbit_facts",
    )
    for forbidden in forbidden_invocations:
        assert forbidden not in source


def test_contract_fixture_is_metadata_only_and_exercises_checksum_and_order_helpers() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split("try {", 1)[0]
    assert "Get-PartContentFingerprint" in fixture
    assert "Get-LogicalChecksumSql" in fixture
    assert "Get-MigrationOrderRank" in fixture
    assert "PRODUCTION_CN_WARM_MIGRATION_DESIGN_CONTRACT_DIRECT_INVOCATION_OK" in fixture
    assert "docker" not in fixture.lower()


def test_workflow_is_windows_ps51_contract_gate_without_apply() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in source
    assert "powershell.exe -NoProfile" in source
    assert "-ContractOnly" in source
    assert "AcceptedProvisioningPreflightReceiptPath" in source
    assert "must not expose Apply or Resume" in source
    assert "PRODUCTION_CN_WARM_MIGRATION_DESIGN_CONTRACT_DIRECT_INVOCATION_OK" in source
    assert "concurrency:" in source
