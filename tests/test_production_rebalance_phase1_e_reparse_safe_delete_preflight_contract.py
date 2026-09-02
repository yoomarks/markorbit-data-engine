from pathlib import Path


SCRIPT = Path("scripts/preflight-production-rebalance-phase1-e-reparse-safe-delete.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_preflight_is_read_only_and_has_no_apply_surface() -> None:
    text = _text()
    assert "[switch]$Apply" not in text
    for forbidden in (
        "Remove-Item",
        "Move-Item",
        "Copy-Item",
        "robocopy",
        "rsync",
        "DeleteFileW",
        "RemoveDirectoryW",
        "New-VHD",
        "Resize-VHD",
        "Mount-VHD",
        "Dismount-VHD",
        "Optimize-VHD",
        "Format-Volume",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "docker compose restart",
        "wsl.exe --shutdown",
        "wsl.exe --unmount",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    ):
        assert forbidden not in text


def test_preflight_refreshes_same_main_lx_mapping_acceptance() -> None:
    text = _text()
    assert "profile-production-rebalance-e-lx-target-mapping.ps1" in text
    assert "PRODUCTION_REBALANCE_E_LX_TARGET_MAPPING_V1" in text
    assert "REBALANCE_E_LX_MAPPING_INTERNAL_TARGETS" in text
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_DESIGN" in text
    assert "Fresh LX target mapping exited" in text
    assert "LX_MAPPING_RECEIPT_SHA_MISMATCH" in text
    assert "LX_MAPPING_POINT_COUNT_CHANGED" in text
    assert "ExpectedReparsePointCount = 63" in text


def test_preflight_enumerates_without_descending_into_reparse_points() -> None:
    text = _text()
    assert "function Get-NonTraversingDeletionInventory" in text
    assert "[System.IO.Directory]::EnumerateFileSystemEntries" in text
    assert "[System.IO.FileAttributes]::ReparsePoint" in text
    assert "$result.reparse_paths += $fullPath" in text
    assert "$writer.WriteLine(\"R`t0`t" in text
    assert "continue" in text
    assert "SearchOption]::AllDirectories" not in text
    assert "POSTORDER_NORMAL_OBJECTS_NATIVE_UNLINK_REPARSE_NO_FOLLOW" in text
    assert "DO_NOT_DESCEND_INTO_REPARSE_POINTS" in text


def test_preflight_writes_manifest_and_freezes_future_regeneration() -> None:
    text = _text()
    assert "phase1_e_hot_non_traversing_manifest.tsv" in text
    assert "phase1_e_logs_non_traversing_manifest.tsv" in text
    assert "Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256" in text
    assert "TYPE_LENGTH_ATTRIBUTES_BASE64_RELATIVE_PATH" in text
    assert "require_same_main_manifest_regeneration_before_apply=$true" in text
    assert "hot_manifest_sha256=" in text
    assert "logs_manifest_sha256=" in text


def test_preflight_requires_exact_reparse_path_set_equality() -> None:
    text = _text()
    assert "function Compare-PathSets" in text
    assert "accepted_reparse_set_exact=" in text
    assert "REPARSE_PATH_SET_MISMATCH" in text
    assert "ACTUAL_REPARSE_POINT_COUNT_CHANGED" in text
    assert "expected_duplicate_count" in text
    assert "actual_duplicate_count" in text
    assert "missing_paths" in text
    assert "unexpected_paths" in text


def test_preflight_requires_zero_legacy_e_references_and_production_health() -> None:
    text = _text()
    for marker in (
        "Get-AllContainerMounts",
        "Get-ComposeBindMounts",
        "Get-PathReferences",
        "LEGACY_E_HOT_REFERENCE_PRESENT",
        "LEGACY_E_LOGS_REFERENCE_PRESENT",
        "Assert-RawConsumersStopped",
        "Assert-AcceptedProductionMount",
        "production_clickhouse_ready_before=",
        "production_clickhouse_ready_after=",
        "env_unchanged=",
    ):
        assert marker in text


def test_ready_state_only_advances_to_apply_design_and_keeps_mutation_closed() -> None:
    text = _text()
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_READY" in text
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY_DESIGN" in text
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_BLOCKED" in text
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_REVIEW_REQUIRED" in text
    for marker in (
        "phase1_delete_authorized=$false",
        "reparse_delete_authorized=$false",
        "legacy_e_hot_delete_authorized=$false",
        "legacy_e_logs_delete_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "docker_restart_authorized=$false",
        "vhdx_create_authorized=$false",
        "wsl_shutdown_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "mutation_performed=$false",
        "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_DONE",
    ):
        assert marker in text
