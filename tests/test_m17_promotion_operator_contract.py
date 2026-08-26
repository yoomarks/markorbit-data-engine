from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "check-platformization-m17-promotion.ps1"


def test_promotion_operator_consumes_persisted_json_only() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "CnServingCheckpointPath" in text
    assert "app.release_promotion" in text
    assert "--serving-state-report" in text
    assert "--require-ready" in text
    assert "ConvertFrom-Json" in text
    assert "release_promotion_allowed" in text
    assert "reports" in text

    forbidden = (
        "docker compose",
        "compose run",
        "compose up",
        "restart-service",
        "start-service",
        "app.cn.post_import_acceptance",
        "app.cn.final_checkpoint",
        "app.cn.ingest",
        "2023_5.zip",
        "optimize table",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_promotion_operator_never_changes_version() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "does not change version" in lowered
    assert "set-content" in lowered
    assert "version" not in " ".join(
        line.strip().lower()
        for line in text.splitlines()
        if "set-content" in line.lower()
    )
