from pathlib import Path


SCRIPT = Path("scripts/run-production-storage-rebalance-guarded-apply.ps1")


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_legacy_storage_rebalance_operator_has_no_delete_primitive() -> None:
    text = _script_text()
    assert "Remove-Item" not in text
    assert "System.IO.File]::Delete" not in text
    assert "System.IO.Directory]::Delete" not in text
    assert "LEGACY_STORAGE_REBALANCE_OPERATOR_RETIRED" in text
    assert "mutation_performed=False" in text


def test_legacy_phase1e_points_only_to_accepted_safe_successor() -> None:
    text = _script_text()
    assert "run-production-rebalance-phase1-e-reparse-safe-delete.ps1" in text
    assert "Legacy Phase1E recursive-delete path is retired" in text


def test_phase2d_capability_remains_on_dedicated_resumable_path() -> None:
    text = _script_text()
    assert "run-production-rebalance-phase2-d-resumable-apply.ps1" in text
    assert "run-production-rebalance-phase2-d-resumable-delete.ps1" in text
    assert "Phase2D remains available through the dedicated resumable operators" in text
