from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run-clickhouse-hot-path-rename.ps1"


def test_launcher_requires_admin_and_fsutil_positive_probe() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "Test-IsAdministrator" in text
    assert "Run this Hot-path rename operator from an elevated Administrator PowerShell" in text
    assert "fsutil.exe file queryCaseSensitiveInfo" in text
    assert "FSUTIL_OLD_HOT_CASE_SENSITIVE_OK" in text
    assert "is enabled" in lowered
    assert "已启用" in text
    assert "is disabled" in lowered
    assert "已禁用" in text


def test_launcher_reuses_existing_fail_closed_operator() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    lowered = text.lower()

    assert '"MarkOrbit.NativeCaseSensitivity"' in text
    assert "fileInformationClass != 2" in text
    assert "fileInfoBuffer.Flags = 0x00000001" in text
    assert 'Join-Path $PSScriptRoot "rename-clickhouse-hot-path.ps1"' in text
    assert "-OldHotPath $OldHotPath" in text
    assert "-NewHotPath $NewHotPath" in text
    assert "docker stop" not in lowered
    assert "rename-item" not in lowered
    assert "remove-item" not in lowered
