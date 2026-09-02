from pathlib import Path


SCRIPT = Path("scripts/plan-production-hot-warm-sizing.ps1")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_sizing_readiness_uses_shallow_repo_reports_root() -> None:
    text = _text()
    for marker in (
        "function Get-ShallowReadinessRoot",
        "Join-Path 'reports' '_rdy'",
        "readiness_evidence_strategy=SHALLOW_REPO_REPORTS",
        "readiness_evidence_root=",
        "'-EvidenceRoot',$readinessRelativeRoot",
        "Sizing readiness run id must be a short filesystem-safe token.",
    ):
        assert marker in text


def test_nested_sizing_root_is_not_reused_for_readiness_receipt() -> None:
    text = _text()
    assert "Join-Path $SizingRelativeRoot 'readiness'" not in text
    assert "$readinessRelativeRoot = [string]$readinessRoot.relative" in text
    assert "$readinessAbsoluteRoot = [string]$readinessRoot.absolute" in text


def test_sizing_safety_boundaries_remain_closed() -> None:
    text = _text()
    for marker in (
        "vhdx_create_authorized=$false",
        "live_migration_authorized=$false",
        "source_volume_delete_authorized=$false",
        "raw_delete_authorized=$false",
        "full_cn_replay_authorized=$false",
        "us_package_2_authorized=$false",
        "us_bulk_authorized=$false",
        "production_clickhouse_mutation_performed=$false",
        "accepted_volume_mutation_performed=$false",
    ):
        assert marker in text
