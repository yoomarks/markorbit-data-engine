from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "design-us-secondary-family-hot-migration.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "us-secondary-family-migration-design-runtime.yml"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_design_surface_is_read_only_and_has_no_apply_parameter() -> None:
    source = text()
    assert "[switch]$Apply" not in source
    assert "[switch]$Resume" not in source
    for marker in (
        "read_only=$true",
        "mutation_performed=$false",
        "logical_checksum_execution_performed=$false",
        "connectivity_probe_performed=$false",
        "future_growth_projection_performed=$false",
        "target_schema_apply_authorized=$false",
        "insert_or_copy_authorized=$false",
        "serving_cutover_authorized=$false",
        "source_delete_authorized=$false",
    ):
        assert marker in source

def test_design_is_bound_to_all_assignment_and_ttab_schema_migrations() -> None:
    source = text()
    for marker in (
        "009_us_assignment_m10.sql",
        "010_us_ttab_m10.sql",
        "011_us_ttab_m11_real_rawxml.sql",
        "012_us_ttab_m12_official_bulk.sql",
        "app.us_assignment.publisher",
        "app.us_ttab.publisher",
        "observed_at",
        "REPO_COLUMN_ORDER_DRIFT:",
        "REPO_COLUMN_TYPE_DRIFT:",
    ):
        assert marker in source


def test_exact_eight_tables_and_source_sort_keys_are_frozen() -> None:
    source = text()
    tables = (
        "us_assignment_record_history",
        "us_assignment_assignor_history",
        "us_assignment_assignee_history",
        "us_assignment_property_history",
        "us_ttab_proceeding_history",
        "us_ttab_party_history",
        "us_ttab_property_history",
        "us_ttab_docket_history",
    )
    for table in tables:
        assert table in source
    assert "Expected exactly eight secondary-family tables." in source

def test_live_design_queries_system_metadata_only() -> None:
    source = text()
    for marker in (
        "FROM system.tables",
        "FROM system.columns",
        "FROM system.parts",
        "create_table_query",
        "partition_id",
        "hash_of_all_files",
        "hash_of_uncompressed_files",
        "uncompressed_hash_of_compressed_files",
        "disk_name",
        "system metadata only",
    ):
        assert marker in source


def test_target_ddl_is_source_schema_plus_hot_us_policy_and_fails_closed() -> None:
    source = text()
    assert "function Convert-ToTargetDdl" in source
    assert "storage_policy = '$($script:TargetPolicy)'" in source
    assert "$script:TargetPolicy = 'hot_us_only'" in source
    assert "Source DDL unexpectedly already contains storage_policy." in source
    assert "TARGET_DDL_POLICY_MISSING:" in source
    assert "TARGET_DDL_FAIL_CLOSED_DRIFT:" in source
    assert "IF NOT EXISTS" in source

def test_content_and_residency_fingerprints_remain_separate() -> None:
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


def test_logical_checksum_is_frozen_but_not_executed() -> None:
    source = text()
    assert "sum(cityHash64(tuple(*))) AS checksum_sum" in source
    assert "groupBitXor(cityHash64(tuple(*))) AS checksum_xor" in source
    assert "logical_checksum_execution_performed=$false" in source
    assert "logical_checksum_execution_performed=$false" in source.lower()

def test_migration_order_is_assignment_then_ttab_and_smallest_first() -> None:
    source = text()
    assert "ASSIGNMENT_FIRST_THEN_TTAB_ASCENDING_SOURCE_BYTES_THEN_TABLE_WITHIN_FAMILY" in source
    assert "if ($_.family -eq 'ASSIGNMENT') { 0 } else { 1 }" in source
    assert "[int64]$_.bytes_on_disk" in source
    assert "migration_order" in source
    assert "transfer_unit='WHOLE_TABLE'" in source
    assert "SOURCE_PARTITION_LAYOUT_DRIFT:" in source


def test_cross_runtime_transport_reuses_accepted_network_pull_pattern() -> None:
    source = text()
    for marker in (
        "TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE",
        "source_and_target_are_distinct_clickhouse_runtimes=$true",
        "filesystem_copy_between_runtimes_allowed=$false",
        "blind_replay_allowed=$false",
        "future_target_to_source_native_connectivity_preflight_required=$true",
        "source_endpoint_frozen=$false",
        "credential_material_persisted=$false",
        "network_pull_execution_performed=$false",
        "future_copy_authorized=$false",
    ):
        assert marker in source

def test_acceptance_contract_covers_schema_rows_checksums_residency_and_reserve() -> None:
    source = text()
    for marker in (
        "source_schema_repo_contract_match",
        "source_rows_bytes_parts_and_residency_frozen",
        "all_eight_target_tables_absent",
        "hot_us_only_policy_and_reserve_revalidated",
        "target_schema_matches_source_except_hot_us_storage_policy",
        "source_logical_checksum_recorded",
        "row_count_equivalence",
        "logical_checksum_equivalence",
        "target_parts_only_on_hot_us",
        "source_row_and_checksum_unchanged",
        "hot_us_reserve_floor_revalidated",
    ):
        assert marker in source


def test_rollback_keeps_source_authoritative_and_target_out_of_serving() -> None:
    source = text()
    for marker in (
        "source_remains_authoritative_and_retained_until_separate_cutover_acceptance",
        "failed_target_tables_are_never_served",
        "no_source_cleanup_or_archive_mutation",
        "target_discard_or_drop_requires_separate_explicit_rollback_review",
        "serving_stays_on_pre_migration_runtime_until_final_acceptance",
    ):
        assert marker in source

def test_phase_separation_stops_at_schema_apply_review() -> None:
    source = text()
    for marker in (
        "TARGET_SCHEMA_APPLY_REVIEW",
        "TARGET_SCHEMA_APPLY",
        "TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT",
        "BOUNDED_ASSIGNMENT_TABLE_COPY",
        "ASSIGNMENT_TARGET_ACCEPTANCE",
        "BOUNDED_TTAB_TABLE_COPY",
        "TTAB_TARGET_ACCEPTANCE",
        "SERVING_CUTOVER_REVIEW",
        "SOURCE_CLEANUP_REVIEW",
        "TARGET_SCHEMA_APPLY_REVIEW' } else",
    ):
        assert marker in source
    assert "authorized=$true" not in source


def test_no_destructive_runtime_or_data_command_surface() -> None:
    source = text()
    forbidden = (
        "[switch]$Apply",
        "New-VHD ",
        "Resize-VHD ",
        "Mount-VHD ",
        "Dismount-VHD ",
        "Optimize-VHD ",
        "docker system prune",
        "docker volume rm",
        "--shutdown",
        "--unregister",
    )
    for marker in forbidden:
        assert marker not in source

def test_contract_fixture_is_runtime_independent() -> None:
    source = text()
    fixture = source.split("function Invoke-ContractFixture", 1)[1].split(
        "if ($ContractOnly)", 1
    )[0]
    assert "US_SECONDARY_FAMILY_MIGRATION_DESIGN_CONTRACT_PASS" in fixture
    assert "Convert-ToTargetDdl" in fixture
    assert "Get-LogicalChecksumSql" in fixture
    assert "Invoke-NativeText 'docker'" not in fixture
    assert "wsl.exe" not in fixture.lower()


def test_workflow_runs_ps51_and_python_contract_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in workflow
    assert "powershell.exe -NoProfile" in workflow
    assert "-ContractOnly" in workflow
    assert "US_SECONDARY_FAMILY_MIGRATION_DESIGN_CONTRACT_PASS" in workflow
    assert "ubuntu-latest" in workflow
    assert "test_us_secondary_family_migration_design_contract.py" in workflow
    assert "concurrency:" in workflow
    assert "paths:" in workflow
