from pathlib import Path


SCRIPT = Path("scripts/profile-production-rebalance-e-lx-target-mapping.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_gate_is_read_only_and_has_no_apply_surface() -> None:
    text = _text()
    assert "[switch]$Apply" not in text
    for forbidden in (
        "Remove-Item",
        "Move-Item",
        "Copy-Item",
        "robocopy",
        "rsync",
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


def test_gate_freezes_exact_linux_to_host_mapping_contract() -> None:
    text = _text()
    assert "'/var/lib/clickhouse/'" in text
    assert "E:\\MarkOrbitData\\hot\\clickhouse" in text
    assert "LegacyEHotRoot must remain the exact approved legacy E ClickHouse root." in text
    assert "required_native_kind='LX_SYMLINK'" in text
    assert "required_lx_version=2" in text
    assert "$lxVersion -eq 2" in text
    assert "LX_TARGET_PREFIX_REJECTED" in text
    assert "LX_TARGET_ROOT_SELF_REFERENCE" in text
    assert "LX_TARGET_PATH_INVALID" in text
    assert "LX_TARGET_ESCAPES_CANDIDATE_ROOT" in text


def test_gate_requires_fresh_native_v2_receipt() -> None:
    text = _text()
    assert "profile-production-rebalance-e-reparse-native-provenance.ps1" in text
    assert "PRODUCTION_REBALANCE_E_NATIVE_REPARSE_PROVENANCE_V2" in text
    assert "fresh_native_reparse_receipt" in text
    assert "Fresh native reparse provenance exited" in text
    assert "Fresh native reparse receipt SHA mismatch." in text
    assert "Fresh native receipt lost read-only/non-traversing contract." in text


def test_gate_requires_existing_non_reparse_internal_directory_targets() -> None:
    text = _text()
    for marker in (
        "target_exists=",
        "target_is_directory=",
        "target_is_reparse_point=",
        "mapped_target_inside_candidate_root=",
        "LX_TARGET_MISSING",
        "LX_TARGET_NOT_DIRECTORY",
        "LX_TARGET_IS_REPARSE_POINT",
        "REBALANCE_E_LX_MAPPING_INTERNAL_TARGETS",
        "REBALANCE_E_LX_MAPPING_ESCAPES_ROOT",
        "REBALANCE_E_LX_MAPPING_UNSUPPORTED_VERSION",
    ):
        assert marker in text


def test_internal_mapping_only_advances_to_safe_delete_design() -> None:
    text = _text()
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_DESIGN" in text
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_REVIEW_REQUIRED" in text
    for marker in (
        "phase1_delete_authorized=$false",
        "reparse_delete_authorized=$false",
        "legacy_e_hot_delete_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "mutation_performed=$false",
    ):
        assert marker in text


def test_gate_preserves_production_and_bulk_boundaries() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Assert-AcceptedProductionMount",
        "production_clickhouse_ready_before=",
        "production_clickhouse_ready_after=",
        "env_unchanged=",
        "vhdx_create_authorized=$false",
        "wsl_shutdown_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "PRODUCTION_REBALANCE_E_LX_TARGET_MAPPING_DONE",
    ):
        assert marker in text
