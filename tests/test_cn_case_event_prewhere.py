from pathlib import Path


MAIN_CORE = Path("app/main_core.py")


def test_cn_case_observed_events_prewhere_application_before_final_merge() -> None:
    text = MAIN_CORE.read_text(encoding="utf-8")
    block = text.split('"events": _query_dicts(', 1)[1].split('"relations": _query_dicts(', 1)[0]
    assert "FROM markorbit_facts.cn_observed_event FINAL" in block
    assert "PREWHERE application_number = '{safe}'" in block
    assert "\n            WHERE application_number = '{safe}'" not in block
