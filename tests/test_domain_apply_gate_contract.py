from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _text(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_shared_apply_gate_dispatches_only_to_read_only_transition_checks() -> None:
    text = _text("assert-domain-apply-gate.ps1")
    assert 'ValidateSet("US_APPLICATION", "US_ASSIGNMENT", "US_TTAB")' in text
    assert "check-us-application-transition.ps1" in text
    assert "check-cn-final-checkpoint.ps1" not in text
    assert "check-us-assignment-transition.ps1" in text
    assert "check-us-ttab-transition.ps1" in text
    assert "-Compact" in text
    assert "replay-" not in text.lower()
    assert "run-us" not in text.lower()
    assert "reset-" not in text.lower()


def test_all_primary_us_mutation_entrypoints_require_apply_gate() -> None:
    expected = {
        "replay-us-deterministic.ps1": "US_APPLICATION",
        "run-us.ps1": "US_APPLICATION",
        "retry-us.ps1": "US_APPLICATION",
        "replay-us-assignment-deterministic.ps1": "US_ASSIGNMENT",
        "run-us-assignment.ps1": "US_ASSIGNMENT",
        "retry-us-assignment.ps1": "US_ASSIGNMENT",
        "replay-us-ttab-deterministic.ps1": "US_TTAB",
        "run-us-ttab.ps1": "US_TTAB",
        "retry-us-ttab.ps1": "US_TTAB",
    }
    for script, target in expected.items():
        text = _text(script)
        assert "assert-domain-apply-gate.ps1" in text, script
        assert f'"{target}"' in text, script


def test_assignment_and_ttab_entrypoints_pin_application_history_part_count() -> None:
    for script in (
        "replay-us-assignment-deterministic.ps1",
        "run-us-assignment.ps1",
        "retry-us-assignment.ps1",
        "replay-us-ttab-deterministic.ps1",
        "run-us-ttab.ps1",
        "retry-us-ttab.ps1",
    ):
        text = _text(script)
        assert "ExpectedApplicationHistoryParts" in text, script
        assert "ExpectedApplicationHistoryParts is required" not in text, script
