from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_m17_platformization_docs_freeze_core_contract_names() -> None:
    text = (ROOT / "docs" / "M17_PLATFORMIZATION.md").read_text(encoding="utf-8")
    assert "Generic Work Engine V1" in text
    assert "Domain Adapter Contract V1" in text
    assert "Data Trust/Freshness V1" in text
    assert "WIPO Madrid" in text
    assert "EUIPO" in text
