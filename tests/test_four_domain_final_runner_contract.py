from pathlib import Path


SCRIPT = Path("scripts/run-four-domain-final-acceptance.ps1").read_text(encoding="utf-8")


def test_final_runner_preserves_frozen_audit_order():
    ordered_calls = [
        "status-domain-lifecycle.ps1",
        "audit-m16-acceptance.ps1",
        "audit-us-real-data.ps1",
        "audit-us-assignment-corpus.ps1",
        "audit-us-ttab-corpus.ps1",
        "audit-four-domain-acceptance.ps1",
    ]
    positions = [SCRIPT.index(name) for name in ordered_calls]
    assert positions == sorted(positions)


def test_final_runner_requires_lifecycle_final_acceptance_phase():
    assert '$lifecycle.current_phase -ne "FINAL_ACCEPTANCE"' in SCRIPT
    assert '$lifecycle.status -ne "FINAL_ACCEPTANCE_REQUIRED"' in SCRIPT
    assert "Four-domain final acceptance is not unlocked" in SCRIPT


def test_final_runner_pins_application_coverage_policy():
    assert "[int]$ExpectedApplicationHistoryParts" in SCRIPT
    assert "[string]$ExpectedApplicationDailyThrough" in SCRIPT
    assert "-ExpectedApplicationHistoryParts $ExpectedApplicationHistoryParts" in SCRIPT
    assert "-ExpectedApplicationDailyThrough $ExpectedApplicationDailyThrough" in SCRIPT
    assert "ExpectedHistoryParts = $ExpectedApplicationHistoryParts" in SCRIPT


def test_final_runner_uses_manifest_acceptance_reports_for_assignment_and_ttab():
    assert "03_us_assignment_manifest_acceptance.json" in SCRIPT
    assert "04_us_ttab_manifest_acceptance.json" in SCRIPT
    assert "audit-us-assignment-corpus.ps1" in SCRIPT
    assert "audit-us-ttab-corpus.ps1" in SCRIPT
    assert "audit-us-assignment-real-data.ps1" not in SCRIPT
    assert "audit-us-ttab-real-data.ps1" not in SCRIPT


def test_final_runner_retains_auditable_artifact_manifest():
    assert 'run_version = "MARKORBIT_FOUR_DOMAIN_FINAL_RUN_V1"' in SCRIPT
    assert "Get-FileHash -LiteralPath $path -Algorithm SHA256" in SCRIPT
    assert "git rev-parse HEAD" in SCRIPT
    assert "run_manifest.json" in SCRIPT


def test_final_runner_does_not_start_or_replay_any_domain():
    forbidden = [
        "start-worker.ps1",
        "replay-cn-full.ps1",
        "replay-us-deterministic.ps1",
        "replay-us-assignment-deterministic.ps1",
        "replay-us-ttab-deterministic.ps1",
        "reset-us-clean-rebuild.ps1",
    ]
    for token in forbidden:
        assert token not in SCRIPT
