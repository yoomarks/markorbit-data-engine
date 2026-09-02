from pathlib import Path


SCRIPT = Path("scripts/run-production-rebalance-phase1-e-reparse-safe-delete.ps1")


def test_phase1e_lx_tag_is_ps51_safe_unsigned_value():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "$script:LxSymlinkReparseTag = [Convert]::ToUInt32('A000001D', 16)" in text
    assert "[uint32]0xA000001D" not in text
    assert "[uint32]$identity.Tag -ne $script:LxSymlinkReparseTag" in text
    assert "UnlinkChecked([string]$entry.full_path, $script:LxSymlinkReparseTag, [uint32]2, $true)" in text
