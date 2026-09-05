from pathlib import Path


SCRIPT = Path("scripts/run-production-storage-rebalance-guarded-apply.ps1")
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_legacy_operator_keeps_compatibility_parameter_surface() -> None:
    for marker in (
        "[ValidateSet('Phase1E','Phase2D')]",
        "[string]$ExpectedMainSha",
        "[string]$Phase",
        "[string]$AcceptedVolume",
        "[string]$LegacyRawRoot",
        "[string]$RawTargetRoot",
        "[string]$LegacyEHotRoot",
        "[string]$LegacyEHotLogsRoot",
        "[string]$EvidenceRoot",
        "[switch]$AcknowledgeTemporary20Percent",
        "[switch]$Apply",
    ):
        assert marker in TEXT


def test_legacy_operator_is_fail_closed_and_has_no_mutation_primitive() -> None:
    for marker in (
        "decision=LEGACY_STORAGE_REBALANCE_OPERATOR_RETIRED",
        "mutation_performed=False",
        "Legacy Phase1E recursive-delete path is retired",
        "Legacy generic Phase2D entry point is retired",
    ):
        assert marker in TEXT

    for forbidden in (
        "Remove-Item",
        "Move-Item",
        "Copy-Item",
        "robocopy",
        "rsync",
        "System.IO.File]::Delete",
        "System.IO.Directory]::Delete",
        "docker volume rm",
        "docker system prune",
        "docker compose down",
        "docker compose restart",
        "wsl.exe --shutdown",
        "wsl.exe --unmount",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "Invoke-Expression",
        "Start-Process",
    ):
        assert forbidden not in TEXT


def test_phase1e_points_to_accepted_reparse_safe_operator() -> None:
    assert "scripts/run-production-rebalance-phase1-e-reparse-safe-delete.ps1" in TEXT
    assert "accepted preflight/journal contract" in TEXT


def test_phase2d_remains_available_only_through_dedicated_resumable_operators() -> None:
    assert "scripts/run-production-rebalance-phase2-d-resumable-apply.ps1" in TEXT
    assert "scripts/run-production-rebalance-phase2-d-resumable-delete.ps1" in TEXT
    assert "Phase2D remains available through the dedicated resumable operators" in TEXT


def test_legacy_operator_does_not_forward_or_autorun_successors() -> None:
    assert "& $phase1EReplacement" not in TEXT
    assert "& $phase2DReplacement" not in TEXT
    assert "& $phase2DAuthorityPreparation" not in TEXT
