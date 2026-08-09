from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET

from app.us.parser import iter_case_bundles, parse_case_element


REAL_FIXTURE = Path("tests/fixtures/us_real_tdxf_layout.xml")


def test_real_history_layout_reads_direct_case_fields_and_events() -> None:
    history = list(iter_case_bundles(REAL_FIXTURE))[0]

    assert history.case.serial_number == "70011210"
    assert history.case.registration_number == "0011210"
    assert history.case.transaction_date == date(2023, 12, 7)
    assert history.case.filing_date == date(1884, 4, 7)
    assert history.case.registration_date == date(1884, 5, 27)
    assert history.case.use_1a_filed is True
    assert history.case.use_1a_current is True
    assert history.case.section_8_accepted is True
    assert history.case.section_15_acknowledged is True

    assert len(history.events) == 2
    assert history.events[0].event_code == "REN4"
    assert history.events[0].description_text.startswith("REGISTERED AND RENEWED")
    assert history.events[0].event_date == date(1974, 5, 27)

    assert history.owners[0].nationality_country == "US"
    assert history.owners[0].nationality_state == "DE"
    assert history.owners[0].country == "US"
    assert history.classifications[0].international_codes == ("022",)
    assert history.classifications[0].us_codes == ("007",)


def test_real_daily_madrid_layout_reads_publication_basis_and_international_record() -> None:
    madrid = list(iter_case_bundles(REAL_FIXTURE))[1]

    assert madrid.case.serial_number == "79001917"
    assert madrid.case.registration_number == "2998412"
    assert madrid.case.transaction_date == date(2026, 1, 8)
    assert madrid.case.publication_date == date(2005, 6, 28)
    assert madrid.case.madrid_66a_filed is True
    assert madrid.case.madrid_66a_current is True
    assert madrid.case.madrid_66a is True
    assert madrid.case.law_office_code == "L30"
    assert madrid.case.examiner_name == "DWYER, SEAN"

    assert madrid.case.international_registration_number == "0822403"
    assert madrid.case.international_registration_date == date(2004, 3, 24)
    assert madrid.case.international_publication_date == date(2004, 5, 27)
    assert madrid.case.international_renewal_date == date(2034, 3, 24)
    assert madrid.case.international_registration_status_code == "001"
    assert madrid.case.international_priority_claimed is True
    assert madrid.case.international_priority_claimed_date == date(2003, 10, 28)
    assert madrid.case.international_first_refusal is True

    assert madrid.owners[0].nationality_country == "CH"
    assert madrid.owners[0].country == "CH"
    assert [event.event_code for event in madrid.events] == ["REPR", "NWAP"]


def test_filed_basis_does_not_override_explicit_current_false() -> None:
    element = ET.fromstring(
        """
        <case-file>
          <serial-number>99112233</serial-number>
          <case-file-header>
            <filed-as-use-application-in>T</filed-as-use-application-in>
            <use-application-currently-in>F</use-application-currently-in>
            <intent-to-use-in>T</intent-to-use-in>
            <intent-to-use-current-in>F</intent-to-use-current-in>
            <filing-basis-filed-as-44d-in>T</filing-basis-filed-as-44d-in>
            <filing-basis-current-44d-in>F</filing-basis-current-44d-in>
          </case-file-header>
        </case-file>
        """
    )
    record = parse_case_element(element).case
    assert record.use_1a_filed is True
    assert record.use_1a_current is False
    assert record.use_1a is False
    assert record.intent_to_use_1b_filed is True
    assert record.intent_to_use_1b_current is False
    assert record.foreign_application_44d_filed is True
    assert record.foreign_application_44d_current is False


def test_missing_current_basis_falls_back_only_for_legacy_transforms() -> None:
    element = ET.fromstring(
        """
        <case-file>
          <serial-number>99112234</serial-number>
          <case-file-header>
            <use-in-commerce-1a>Y</use-in-commerce-1a>
            <intent-to-use-1b>N</intent-to-use-1b>
          </case-file-header>
        </case-file>
        """
    )
    record = parse_case_element(element).case
    assert record.use_1a_filed is True
    assert record.use_1a_current is True
    assert record.intent_to_use_1b_current is False
