from pathlib import Path


SCRIPT = Path("scripts/plan-production-hot-warm-sizing.ps1")


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_default_pilot_discovery_is_canonical_and_not_current_run_output() -> None:
    text = source()
    assert "if ($PilotReceiptPath)" in text
    assert "Join-Path $repoRoot 'reports'" in text
    assert 'Write-Host "pilot_evidence_discovery_root=$pilotEvidenceRoot"' in text
    assert "Get-ChildItem -LiteralPath $pilotEvidenceRoot -Recurse -Filter 'pilot_receipt.json'" in text
    assert "Get-ChildItem -LiteralPath $EvidenceRoot -Recurse -Filter 'pilot_receipt.json'" not in text
    assert "canonical reports root $pilotEvidenceRoot" in text


def test_explicit_pilot_path_remains_higher_priority_than_canonical_discovery() -> None:
    text = source()
    override = text.index("if ($PilotReceiptPath)")
    canonical = text.index("$pilotEvidenceRoot = Join-Path $repoRoot 'reports'")
    assert override < canonical
    assert "return (Resolve-Path -LiteralPath $PilotReceiptPath).Path" in text


def test_evidence_root_still_controls_current_sizing_output_only() -> None:
    text = source()
    assert '$sizingRelativeRoot = Join-Path $EvidenceRoot "production_hot_warm_sizing_$timestamp"' in text
    assert "$evidenceDir = Join-Path $repoRoot $sizingRelativeRoot" in text
    assert "us_pilot_receipt_path=$pilotPath" in text


def test_pilot_acceptance_contract_is_unchanged() -> None:
    text = source()
    for marker in (
        "US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1",
        "[bool]$receipt.safe",
        "[bool]$receipt.projection_input_ready",
        "$receipt.status -eq 'PASS'",
        "US bounded pilot receipt is not accepted projection input.",
    ):
        assert marker in text
