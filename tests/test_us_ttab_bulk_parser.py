from pathlib import Path

from app.us_ttab.parser import iter_ttab_bundles


def test_official_bulk_daily_preserves_raw_codes_and_source_ids() -> None:
    bundle = next(
        iter_ttab_bundles(Path("tests/fixtures/us_ttab_bulk_daily_real_shape.xml"))
    )
    p = bundle.proceeding
    assert p.proceeding_number == "79412016"
    assert p.proceeding_type == ""
    assert p.proceeding_type_code == "EXA"
    assert p.status_text == ""
    assert p.status_code == "2"
    assert p.location_code == "845"
    assert p.day_in_location_raw == "20260414"

    party = bundle.parties[0]
    assert party.side == "ROLE_P"
    assert party.role == "P"
    assert party.party_id == "1248285"
    assert party.correspondent_address_id == "2284247"
    assert party.correspondent_address_type_code == "C"

    prop = bundle.properties[0]
    assert prop.source_property_id == "1691989"
    assert prop.serial_number == "79412016"
    assert prop.mark_text == "OEGEN"

    docket = bundle.docket_entries[0]
    assert docket.identifier == "1"
    assert docket.entry_code == "158"
    assert docket.entry_type_code == "X"
    assert docket.history_text == "APPEAL TO BOARD"


def test_official_bulk_historical_preserves_rare_tma_proceeding() -> None:
    bundle = next(
        iter_ttab_bundles(Path("tests/fixtures/us_ttab_bulk_historical_tma_real_shape.xml"))
    )
    prop = bundle.properties[0]
    assert prop.tma_proceeding_number == "2024-101552"
    assert prop.tma_proceeding_type_code == "R"


def test_ttab_parser_uses_streaming_iterparse_contract() -> None:
    source = Path("app/us_ttab/parser.py").read_text(encoding="utf-8")
    assert "context = ET.iterparse(source" in source
    assert "ET.parse(source)" not in source
