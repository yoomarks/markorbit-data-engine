from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "check-m17-target-promotion-evidence.ps1"


def test_target_promotion_operator_composes_only_lightweight_evidence_gates() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    serving = "check-cn-serving-state.ps1"
    promotion = "check-platformization-m17-promotion.ps1"

    assert serving in text
    assert promotion in text
    assert text.index(serving) < text.index(promotion)
    assert 'ExpectedFileName = "2023_5.zip"' in text
    assert "LIGHTWEIGHT_SERVING_CHECKPOINT" in text
    assert "release_promotion_allowed" in text
    assert "ConvertFrom-Json" in text
    assert "OutputDirectory" in text
    assert "cn_m16_lightweight_serving_checkpoint_" in text
    assert "platformization_m17_promotion_" in text

    forbidden = (
        "post_import_acceptance",
        "final_checkpoint",
        "replay-cn",
        "scan-cn",
        "migrate-clickhouse",
        "docker compose up",
        "docker compose run",
        "docker compose restart",
        "start-process",
        "restart-service",
        "stop-service",
        "optimize table",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_target_promotion_operator_does_not_modify_release_version() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "set-content" not in lowered
    assert "update_file" not in lowered
    assert "release version is changed" in lowered
    assert "no release version is changed" in lowered
