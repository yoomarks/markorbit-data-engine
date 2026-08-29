from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPLY_GATE = ROOT / "scripts" / "assert-domain-apply-gate.ps1"
US_APPLICATION_TRANSITION = ROOT / "scripts" / "check-us-application-transition.ps1"
PYTHON_TRANSITION = ROOT / "app" / "us" / "application_transition_gate.py"
PYTHON_HOST_PROTOCOL = ROOT / "app" / "us" / "application_transition_host_protocol.py"


def test_us_application_apply_gate_preserves_dedicated_transition_readiness_gate() -> None:
    text = APPLY_GATE.read_text(encoding="utf-8")

    assert '"US_APPLICATION"' in text
    assert 'check-us-application-transition.ps1' in text
    assert 'ExpectedApplicationHistoryParts is required for the US Application apply gate.' in text
    assert '"-ExpectedHistoryParts", "$ExpectedApplicationHistoryParts"' in text
    assert 'apply_gate_cn_to_us_application_' in text
    assert 'check-cn-final-checkpoint.ps1' not in text
    assert 'check-cn-serving-state.ps1' not in text


def test_us_application_transition_uses_lightweight_cn_checkpoint_not_full_acceptance() -> None:
    powershell = US_APPLICATION_TRANSITION.read_text(encoding="utf-8")
    python = PYTHON_TRANSITION.read_text(encoding="utf-8")
    host = PYTHON_HOST_PROTOCOL.read_text(encoding="utf-8")

    assert 'app.us.application_transition_host_protocol' in powershell
    assert 'from app.us.application_transition_gate import' in host
    assert 'build_transition_gate' in host
    assert 'metadata-only serving-state checkpoint' in python
    assert 'app.cn.serving_state_checkpoint' in python
    assert 'build_serving_state_checkpoint' in python
    assert 'app.cn.final_checkpoint' not in python
    assert 'build_final_checkpoint' not in python


def test_us_application_transition_powershell_parses_only_flat_host_summary() -> None:
    powershell = US_APPLICATION_TRANSITION.read_text(encoding="utf-8")

    assert 'MARKORBIT_US_APPLICATION_TRANSITION_SUMMARY`t' in powershell
    assert 'MARKORBIT_US_APPLICATION_TRANSITION_EVIDENCE`t' in powershell
    assert '$report = $summaryJson | ConvertFrom-Json' in powershell
    assert '$evidenceJson | Set-Content -Encoding UTF8 $OutputPath' in powershell
    assert '$evidenceJson | ConvertFrom-Json' not in powershell
    assert '$json | ConvertFrom-Json' not in powershell
    assert 'expected exactly 1' in powershell
    assert 'US_APPLICATION_TRANSITION_HOST_V1' in powershell
    assert 'CN_TO_US_APPLICATION_TRANSITION_V2' in powershell


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
