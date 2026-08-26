from __future__ import annotations

from pathlib import Path


SCRIPT = Path("scripts/check-platformization-m17.ps1")


def test_m17_operator_supports_exactly_one_persisted_cn_evidence_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[string]$CnAcceptanceReceiptPath" in text
    assert "[string]$CnServingCheckpointPath" in text
    assert "--cn-acceptance-receipt" in text
    assert "--cn-serving-checkpoint" in text
    assert "$CnAcceptanceReceiptPath -and $CnServingCheckpointPath" in text
    assert "Specify exactly one CN runtime evidence path" in text
    assert "Runtime evidence mode" in text
    assert "Promotion basis" in text


def test_m17_operator_is_local_first_and_docker_is_explicit_only() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "[switch]$UseDocker" in text
    assert "if ($UseDocker)" in text
    assert ".venv\\Scripts\\python.exe" in text
    assert "Get-Command python" in text
    assert 'Get-Command py' in text
    assert '"--no-deps"' in text

    # Docker is supported only inside the explicit opt-in branch; the script
    # must not contain an unconditional service-start path.
    assert "compose up" not in lowered
    assert "docker desktop" not in lowered
    assert "start-process" not in lowered
    assert "restart-service" not in lowered
    assert "post_import_acceptance" not in lowered
    assert "final_checkpoint" not in lowered
