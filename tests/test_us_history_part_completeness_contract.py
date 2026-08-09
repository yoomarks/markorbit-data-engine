from pathlib import Path


def test_strict_history_audit_requires_part_01_and_pinned_tail() -> None:
    source = Path("app/us/audit_real_data_v2.py").read_text(encoding="utf-8")
    assert 'range(1, observed_max + 1)' in source
    assert 'historical_tail_part_count_not_pinned' in source
    assert 'historical_part_sequence_incomplete' in source
    assert 'expected_historical_parts_missing' in source
    assert 'historical_parts_exceed_expected_count' in source
    assert '--expected-history-parts' in source


def test_acceptance_fixture_pins_history_tail_without_renaming_contract() -> None:
    source = Path("app/us/validate_acceptance_fixture.py").read_text(encoding="utf-8")
    assert 'expected_history_parts=1' in source
    assert 'apc18840407-20251231-01.zip' in source
    assert '"contract": "US_M1.3_REAL_DATA_ACCEPTANCE_FIXTURE"' in source
    assert '"history_part_policy": "V2_01_TO_PINNED_N"' in source
    assert '"audit_version": report["audit_version"]' in source
