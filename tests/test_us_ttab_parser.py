from datetime import date
from io import BytesIO
from pathlib import Path

from app.us_ttab.parser import iter_ttab_bundles


def test_ttabvue_shaped_fixture_parses_official_fact_families() -> None:
    bundles = list(iter_ttab_bundles(Path("tests/fixtures/us_ttab_synthetic.xml")))
    assert len(bundles) == 1
    bundle = bundles[0]
    proceeding = bundle.proceeding
    assert proceeding.proceeding_number == "92081234"
    assert proceeding.proceeding_type == "Cancellation"
    assert proceeding.filing_date == date(2024, 7, 25)
    assert proceeding.status_text == "Pending"
    assert proceeding.status_date == date(2024, 7, 30)
    assert proceeding.interlocutory_attorney == "TEST ATTORNEY"
    assert proceeding.paralegal_name == "TEST PARALEGAL"

    assert [(p.side, p.party_name) for p in bundle.parties] == [
        ("DEFENDANT", "Alpha Brand LLC"),
        ("PLAINTIFF", "Beta Holdings Inc."),
    ]
    assert len(bundle.properties) == 1
    prop = bundle.properties[0]
    assert prop.party_side == "DEFENDANT"
    assert prop.serial_number == "88123456"
    assert prop.registration_number == "6123456"
    assert prop.mark_text == "ORBIT TEST"

    assert len(bundle.docket_entries) == 2
    assert bundle.docket_entries[0].entry_number == "2"
    assert bundle.docket_entries[0].due_date == date(2024, 9, 8)
    assert bundle.docket_entries[0].history_text.startswith("NOTICE AND TRIAL DATES")


def test_partial_or_invalid_dates_remain_raw_without_typed_date() -> None:
    xml = b"""<root><proceeding><proceeding-number>91234567</proceeding-number>
    <proceeding-type>Opposition</proceeding-type><filing-date>2024-07</filing-date>
    <proceeding-status>Pending</proceeding-status><status-date>UNKNOWN</status-date>
    <prosecution-history-entry><entry-number>1</entry-number><filing-date>2024-08</filing-date>
    <history-text>TEST ENTRY</history-text><due-date>09/30</due-date></prosecution-history-entry>
    </proceeding></root>"""
    bundle = list(iter_ttab_bundles(BytesIO(xml)))[0]
    assert bundle.proceeding.filing_date is None
    assert bundle.proceeding.filing_date_raw == "2024-07"
    assert bundle.proceeding.status_date is None
    assert bundle.proceeding.status_date_raw == "UNKNOWN"
    docket = bundle.docket_entries[0]
    assert docket.filing_date is None
    assert docket.filing_date_raw == "2024-08"
    assert docket.due_date is None
    assert docket.due_date_raw == "09/30"


def test_nested_docket_generic_fields_do_not_become_proceeding_metadata() -> None:
    xml = b"""<root><proceeding><proceeding-number>91234568</proceeding-number>
    <prosecution-history-entry><number>2</number><date>08/01/2024</date>
    <type>ORDER</type><status>DOCKET STATUS</status><history-text>TEST</history-text>
    </prosecution-history-entry></proceeding></root>"""
    bundle = list(iter_ttab_bundles(BytesIO(xml)))[0]
    assert bundle.proceeding.proceeding_number == "91234568"
    assert bundle.proceeding.proceeding_type == ""
    assert bundle.proceeding.status_text == ""
    assert bundle.proceeding.filing_date_raw == ""
    assert bundle.proceeding.status_date_raw == ""
    assert bundle.docket_entries[0].entry_number == "2"
    assert bundle.docket_entries[0].filing_date == date(2024, 8, 1)
