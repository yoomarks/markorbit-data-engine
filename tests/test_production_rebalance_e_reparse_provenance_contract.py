from pathlib import Path


SCRIPT = Path("scripts/profile-production-rebalance-e-reparse-provenance.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_profiler_is_read_only_and_has_no_apply_surface() -> None:
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


def test_profiler_freezes_exact_legacy_e_roots() -> None:
    text = _text()
    assert "E:\\MarkOrbitData\\hot\\clickhouse" in text
    assert "E:\\MarkOrbitData\\hot\\clickhouse-logs" in text
    assert "LegacyEHotRoot must remain the exact approved legacy E ClickHouse root." in text
    assert "LegacyEHotLogsRoot must remain the exact approved legacy E ClickHouse log root." in text


def test_profiler_does_not_traverse_reparse_points() -> None:
    text = _text()
    assert "non_traversing_inventory=$true" in text
    assert "[System.IO.FileAttributes]::ReparsePoint" in text
    assert "$found += (Get-ReparseEntry $normalized $entry)\n                    continue" in text
    assert "Get-ReparseEntry $normalized $normalized" in text


def test_profiler_captures_target_provenance_and_fail_closed_states() -> None:
    text = _text()
    for marker in (
        "link_type=",
        "raw_target_count=",
        "raw_targets=",
        "lexical_target=",
        "target_exists=",
        "lexical_target_inside_candidate_root=",
        "dangling=",
        "target_unresolved=",
        "fsutil_exit_code=",
        "fsutil_output=",
        "REBALANCE_E_REPARSE_PROVENANCE_INTERNAL_LEXICAL_TARGETS",
        "REBALANCE_E_REPARSE_PROVENANCE_UNRESOLVED",
        "REBALANCE_E_REPARSE_PROVENANCE_DANGLING",
        "REBALANCE_E_REPARSE_PROVENANCE_ESCAPES_DELETION_ROOT",
        "REBALANCE_E_REPARSE_PROVENANCE_ENUMERATION_INCOMPLETE",
    ):
        assert marker in text


def test_internal_target_classification_does_not_authorize_deletion() -> None:
    text = _text()
    assert "PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_DESIGN" in text
    for marker in (
        "phase1_delete_authorized=$false",
        "reparse_delete_authorized=$false",
        "legacy_e_hot_delete_authorized=$false",
        "legacy_raw_delete_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "mutation_performed=$false",
    ):
        assert marker in text


def test_profiler_preserves_production_and_bulk_boundaries() -> None:
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
        "PRODUCTION_REBALANCE_E_REPARSE_PROVENANCE_DONE",
    ):
        assert marker in text
