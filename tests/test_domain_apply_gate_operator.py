from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPLY_GATE = ROOT / "scripts" / "assert-domain-apply-gate.ps1"


def test_us_application_apply_gate_uses_lightweight_cn_serving_checkpoint() -> None:
    text = APPLY_GATE.read_text(encoding="utf-8")

    assert '"US_APPLICATION"' in text
    assert 'check-cn-serving-state.ps1' in text
    assert 'apply_gate_cn_serving_to_us_application_' in text
    assert '"-Compact"' in text
    assert 'check-cn-final-checkpoint.ps1' not in text


def test_us_application_apply_gate_does_not_manage_service_or_replay_lifecycle() -> None:
    lowered = APPLY_GATE.read_text(encoding="utf-8").lower()

    forbidden = (
        "docker compose up",
        "docker compose down",
        "docker compose restart",
        "docker restart",
        "docker stop",
        "replay-us-deterministic.ps1",
        "replay-cn",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_assignment_and_ttab_transition_gates_remain_present() -> None:
    text = APPLY_GATE.read_text(encoding="utf-8")

    assert 'check-us-assignment-transition.ps1' in text
    assert 'check-us-ttab-transition.ps1' in text
    assert 'ExpectedApplicationHistoryParts is required for the US Assignment apply gate.' in text
    assert 'ExpectedApplicationHistoryParts is required for the US TTAB apply gate.' in text
