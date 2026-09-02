from pathlib import Path


SCRIPT = Path("scripts/profile-production-rebalance-e-reparse-native-provenance.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_native_profiler_is_read_only_and_has_no_apply_surface() -> None:
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


def test_native_profiler_uses_locale_independent_win32_reparse_query() -> None:
    text = _text()
    for marker in (
        "FSCTL_GET_REPARSE_POINT",
        "DeviceIoControl",
        "FILE_FLAG_OPEN_REPARSE_POINT",
        "IO_REPARSE_TAG_SYMLINK",
        "IO_REPARSE_TAG_MOUNT_POINT",
        "IO_REPARSE_TAG_LX_SYMLINK",
        "ParseBuffer",
        "parser_locale_independent=$true",
    ):
        assert marker in text
    assert "Get-OptionalPropertyValue" not in text
    assert "fsutil.exe" not in text


def test_native_profiler_non_traversing_and_fail_closed() -> None:
    text = _text()
    assert "non_traversing_inventory=$true" in text
    assert "[System.IO.FileAttributes]::ReparsePoint" in text
    assert "$found += Get-NativeReparseEntry $normalized $entry\n                        continue" in text
    for decision in (
        "REBALANCE_E_NATIVE_REPARSE_INTERNAL_LEXICAL_TARGETS",
        "REBALANCE_E_NATIVE_REPARSE_UNRESOLVED",
        "REBALANCE_E_NATIVE_REPARSE_DANGLING",
        "REBALANCE_E_NATIVE_REPARSE_ESCAPES_DELETION_ROOT",
        "REBALANCE_E_NATIVE_REPARSE_ENUMERATION_INCOMPLETE",
    ):
        assert decision in text


def test_native_profiler_preserves_production_and_delete_boundaries() -> None:
    text = _text()
    for marker in (
        "Assert-RawConsumersStopped",
        "Assert-AcceptedProductionMount",
        "production_clickhouse_ready_before=",
        "production_clickhouse_ready_after=",
        "phase1_delete_authorized=$false",
        "reparse_delete_authorized=$false",
        "accepted_volume_delete_authorized=$false",
        "vhdx_create_authorized=$false",
        "wsl_shutdown_authorized=$false",
        "clickhouse_mutation_authorized=$false",
        "cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "mutation_performed=$false",
        "PRODUCTION_REBALANCE_E_NATIVE_REPARSE_PROVENANCE_DONE",
    ):
        assert marker in text
