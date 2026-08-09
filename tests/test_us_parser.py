from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from app.us.parser import USParseError, iter_case_bundles, parse_case_element, parse_uspto_date


FIXTURE = Path("tests/fixtures/us_m1_daily.xml")


def test_uspto_date_preserves_partial_dates_as_unknown() -> None:
    assert parse_uspto_date("20220115") == date(2022, 1, 15)
    assert parse_uspto_date("2022-01-15") == date(2022, 1, 15)
    assert parse_uspto_date("20190600") is None
    assert parse_uspto_date("00000000") is None
    assert parse_uspto_date("") is None


def test_streaming_fixture_parses_case_owner_class_event_statement() -> None:
    bundles = list(iter_case_bundles(FIXTURE))
    assert len(bundles) == 2

    first = bundles[0]
    assert first.case.serial_number == "97123456"
    assert first.case.registration_number == "7123456"
    assert first.case.filing_date == date(2022, 1, 15)
    assert first.case.status_code == "700"
    assert first.case.mark_identification == "ORBIT ALPHA"
    assert first.case.use_1a is True
    assert first.case.intent_to_use_1b is False
    assert first.owners[0].party_name == "Orbit Alpha LLC"
    assert first.owners[0].nationality_state == "DE"
    assert first.classifications[0].international_codes == ("009",)
    assert first.classifications[0].us_codes == ("021", "023")
    assert first.events[0].event_code == "NWAP"
    assert first.events[1].event_code == "PUBO"
    assert [item.type_code for item in first.statements] == ["GS0091", "D10000"]


def test_madrid_and_partial_first_use_are_not_invented() -> None:
    second = list(iter_case_bundles(FIXTURE))[1]
    assert second.case.serial_number == "79345678"
    assert second.case.madrid_66a is True
    assert second.case.international_registration_number == "1734567"
    classification = second.classifications[0]
    assert classification.primary_code == "025"
    assert classification.first_use_anywhere_raw == "20190600"
    assert classification.first_use_anywhere is None
    assert classification.first_use_commerce_raw == "00000000"
    assert classification.first_use_commerce is None


def test_invalid_serial_number_fails_closed() -> None:
    element = ET.fromstring(
        "<case-file><serial-number>ABC</serial-number><case-file-header /></case-file>"
    )
    with pytest.raises(USParseError, match="Invalid USPTO serial number"):
        parse_case_element(element, "bad.xml")
