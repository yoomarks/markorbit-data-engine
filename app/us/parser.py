from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterator
import xml.etree.ElementTree as ET

from app.us.model import (
    USCaseBundle,
    USCaseRecord,
    USClassificationRecord,
    USEventRecord,
    USOwnerRecord,
    USStatementRecord,
)


class USParseError(ValueError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, *names: str) -> list[ET.Element]:
    wanted = set(names)
    return [child for child in element.iter() if _local_name(child.tag) in wanted]


def _first_element(element: ET.Element, *names: str) -> ET.Element | None:
    wanted = set(names)
    for child in element.iter():
        if _local_name(child.tag) in wanted:
            return child
    return None


def _direct_element(element: ET.Element, *names: str) -> ET.Element | None:
    wanted = set(names)
    for child in list(element):
        if _local_name(child.tag) in wanted:
            return child
    return None


def _text(element: ET.Element, *names: str) -> str:
    child = _first_element(element, *names)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _direct_text(element: ET.Element, *names: str) -> str:
    child = _direct_element(element, *names)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _texts(element: ET.Element, *names: str) -> tuple[str, ...]:
    wanted = set(names)
    values: list[str] = []
    for child in element.iter():
        if _local_name(child.tag) not in wanted or child.text is None:
            continue
        value = child.text.strip()
        if value:
            values.append(value)
    return tuple(values)


def _integer(value: str) -> int:
    try:
        return int(value.strip()) if value.strip() else 0
    except ValueError:
        return 0


def _flag(value: str) -> bool:
    # Real TDXF uses T/F for most boolean indicators. Some derivative fixtures
    # and older transforms use Y/N or 1/0, so accept both encodings.
    return value.strip().upper() in {"T", "Y", "YES", "TRUE", "1"}


def parse_uspto_date(value: str) -> date | None:
    raw = value.strip()
    if not raw:
        return None
    compact = raw.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        return None
    year = int(compact[:4])
    month = int(compact[4:6])
    day = int(compact[6:8])
    if month == 0 or day == 0:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _header(case_element: ET.Element) -> ET.Element:
    header = _direct_element(case_element, "case-file-header")
    return header if header is not None else case_element


def _parse_case(case_element: ET.Element, source_name: str) -> USCaseRecord:
    header = _header(case_element)
    serial_number = _direct_text(case_element, "serial-number", "serial-no") or _text(
        case_element, "serial-number", "serial-no"
    )
    if not (len(serial_number) == 8 and serial_number.isdigit()):
        raise USParseError(
            f"Invalid USPTO serial number {serial_number!r} in {source_name or '<stream>'}"
        )

    # In the official TDXF DTD registration-number and transaction-date are
    # siblings of case-file-header, not header fields.
    registration_number = _direct_text(
        case_element, "registration-number", "registration-no"
    ) or _text(header, "registration-number", "registration-no")

    use_filed = _flag(
        _text(header, "filed-as-use-application-in", "use-application-filed-in", "use-in-commerce-1a")
    )
    use_current = _flag(_text(header, "use-application-currently-in")) or use_filed
    itu_filed = _flag(_text(header, "intent-to-use-in", "intent-to-use-application-filed-in", "intent-to-use-1b"))
    itu_current = _flag(_text(header, "intent-to-use-current-in"))
    d44_filed = _flag(_text(header, "filing-basis-filed-as-44d-in", "foreign-application-filed-in", "foreign-application-44d"))
    d44_current = _flag(_text(header, "filing-basis-current-44d-in"))
    e44_filed = _flag(_text(header, "filing-basis-filed-as-44e-in", "foreign-registration-application-filed-in", "foreign-registration-44e"))
    e44_current = _flag(_text(header, "filing-basis-current-44e-in"))
    a66_filed = _flag(_text(header, "filing-basis-filed-as-66a-in", "international-registration-filed-in", "madrid-66a"))
    a66_current = _flag(_text(header, "filing-basis-current-66a-in")) or a66_filed
    no_basis_current = _flag(_text(header, "without-basis-currently-in", "filing-current-no-basis-in", "no-basis", "no-basis-filed-in"))

    international = _direct_element(case_element, "international-registration")
    intl_source = international if international is not None else header

    return USCaseRecord(
        serial_number=serial_number,
        registration_number=registration_number,
        transaction_date=parse_uspto_date(_direct_text(case_element, "transaction-date")),
        filing_date=parse_uspto_date(_text(header, "filing-date", "filing-dt")),
        publication_date=parse_uspto_date(
            _text(header, "published-for-opposition-date", "publication-date", "publication-dt")
        ),
        registration_date=parse_uspto_date(_text(header, "registration-date", "registration-dt")),
        abandonment_date=parse_uspto_date(_text(header, "abandonment-date", "abandonment-dt")),
        cancellation_date=parse_uspto_date(_text(header, "cancellation-date", "cancellation-dt")),
        renewal_date=parse_uspto_date(_text(header, "renewal-date", "renewal-dt")),
        status_code=_text(header, "status-code", "status-cd"),
        status_date=parse_uspto_date(_text(header, "status-date", "status-dt")),
        mark_identification=_text(header, "mark-identification", "mark-id-character", "mark-identification-character"),
        mark_drawing_code=_text(header, "mark-drawing-code", "mark-draw-code"),
        current_location=_text(header, "current-location", "location-code"),
        location_date=parse_uspto_date(_text(header, "location-date", "location-dt")),
        examiner_name=_text(header, "employee-name", "examiner-name"),
        law_office_code=_text(header, "law-office-assigned-location-code", "law-office-code"),
        standard_character_claimed=_flag(_text(header, "standard-characters-claimed-in", "standard-character-claim")),
        use_1a=use_current,
        intent_to_use_1b=itu_current,
        foreign_application_44d=d44_current,
        foreign_registration_44e=e44_current,
        madrid_66a=a66_current,
        no_basis=no_basis_current,
        use_1a_filed=use_filed,
        use_1a_current=use_current,
        intent_to_use_1b_filed=itu_filed,
        intent_to_use_1b_current=itu_current,
        foreign_application_44d_filed=d44_filed,
        foreign_application_44d_current=d44_current,
        foreign_registration_44e_filed=e44_filed,
        foreign_registration_44e_current=e44_current,
        madrid_66a_filed=a66_filed,
        madrid_66a_current=a66_current,
        no_basis_current=no_basis_current,
        renewal_filed=_flag(_text(header, "renewal-filed-in")),
        section_8_filed=_flag(_text(header, "section-8-filed-in")),
        section_8_accepted=_flag(_text(header, "section-8-accepted-in")),
        section_8_partial_accepted=_flag(_text(header, "section-8-partial-accept-in")),
        section_15_filed=_flag(_text(header, "section-15-filed-in")),
        section_15_acknowledged=_flag(_text(header, "section-15-acknowledged-in")),
        opposition_pending=_flag(_text(header, "opposition-pending-in")),
        cancellation_pending=_flag(_text(header, "cancellation-pending-in")),
        international_registration_number=_text(intl_source, "international-registration-number", "international-registration-no"),
        international_registration_date=parse_uspto_date(_text(intl_source, "international-registration-date")),
        international_publication_date=parse_uspto_date(_text(intl_source, "international-publication-date")),
        international_renewal_date=parse_uspto_date(_text(intl_source, "international-renewal-date")),
        international_auto_protection_date=parse_uspto_date(_text(intl_source, "auto-protection-date")),
        international_death_date=parse_uspto_date(_text(intl_source, "international-death-date")),
        international_registration_status_code=_text(intl_source, "international-status-code", "international-registration-status-code"),
        international_registration_status_date=parse_uspto_date(_text(intl_source, "international-status-date", "international-registration-status-date")),
        international_priority_claimed=_flag(_text(intl_source, "priority-claimed-in")),
        international_priority_claimed_date=parse_uspto_date(_text(intl_source, "priority-claimed-date")),
        international_first_refusal=_flag(_text(intl_source, "first-refusal-in")),
    )


def _parse_owners(case_element: ET.Element, serial_number: str) -> tuple[USOwnerRecord, ...]:
    owners: list[USOwnerRecord] = []
    for element in _children(case_element, "case-file-owner"):
        nationality = _direct_element(element, "nationality")
        nat_country = _direct_text(nationality, "country", "country-code") if nationality is not None else ""
        nat_state = _direct_text(nationality, "state", "state-code") if nationality is not None else ""
        nat_other = _direct_text(nationality, "other") if nationality is not None else ""
        owners.append(
            USOwnerRecord(
                serial_number=serial_number,
                entry_number=_integer(_direct_text(element, "entry-number", "entry-seq-no")),
                party_type=_direct_text(element, "party-type", "party-type-code"),
                legal_entity_type_code=_direct_text(element, "legal-entity-type-code", "legal-entity-code"),
                entity_statement=_direct_text(element, "entity-statement"),
                party_name=_direct_text(element, "party-name", "name"),
                nationality_country=nat_country or _direct_text(element, "nationality-country", "nationality-country-code"),
                nationality_state=nat_state or _direct_text(element, "nationality-state", "nationality-state-code"),
                nationality_other=nat_other or _direct_text(element, "nationality-other"),
                address_1=_direct_text(element, "address-1", "address1"),
                address_2=_direct_text(element, "address-2", "address2"),
                city=_direct_text(element, "city"),
                state=_direct_text(element, "state", "state-code"),
                country=_direct_text(element, "country", "country-code"),
                postcode=_direct_text(element, "postcode", "postal-code", "zip-code"),
                dba_aka_text=_direct_text(element, "dba-aka-text"),
                composed_of_statement=_direct_text(element, "composed-of-statement"),
            )
        )
    return tuple(owners)


def _parse_classifications(case_element: ET.Element, serial_number: str) -> tuple[USClassificationRecord, ...]:
    records: list[USClassificationRecord] = []
    for element in _children(case_element, "classification"):
        first_use_raw = _direct_text(element, "first-use-anywhere-date", "first-use-anywhere-dt", "first-use-date")
        first_commerce_raw = _direct_text(element, "first-use-in-commerce-date", "first-use-in-commerce-dt", "first-use-commerce-date")
        records.append(
            USClassificationRecord(
                serial_number=serial_number,
                primary_code=_direct_text(element, "primary-code"),
                international_codes=_texts(element, "international-code", "international-class"),
                us_codes=_texts(element, "us-code", "us-class"),
                status_code=_direct_text(element, "status-code", "status-cd"),
                status_date=parse_uspto_date(_direct_text(element, "status-date", "status-dt")),
                first_use_anywhere=parse_uspto_date(first_use_raw),
                first_use_anywhere_raw=first_use_raw,
                first_use_commerce=parse_uspto_date(first_commerce_raw),
                first_use_commerce_raw=first_commerce_raw,
            )
        )
    return tuple(records)


def _parse_events(case_element: ET.Element, serial_number: str) -> tuple[USEventRecord, ...]:
    events: list[USEventRecord] = []
    for element in _children(case_element, "case-file-event-statement", "case-file-event"):
        events.append(
            USEventRecord(
                serial_number=serial_number,
                event_code=_direct_text(element, "code", "event-code", "event-cd"),
                event_date=parse_uspto_date(_direct_text(element, "date", "event-date", "event-dt")),
                event_sequence=_integer(_direct_text(element, "number", "event-sequence", "event-seq")),
                event_type_code=_direct_text(element, "type", "event-type-code", "event-type-cd"),
                description_text=_direct_text(element, "description-text", "description", "text"),
            )
        )
    return tuple(events)


def _parse_statements(case_element: ET.Element, serial_number: str) -> tuple[USStatementRecord, ...]:
    statements: list[USStatementRecord] = []
    for element in _children(case_element, "case-file-statement"):
        statements.append(
            USStatementRecord(
                serial_number=serial_number,
                type_code=_direct_text(element, "type-code", "statement-type-code"),
                text=_direct_text(element, "text", "statement-text"),
            )
        )
    return tuple(statements)


def parse_case_element(case_element: ET.Element, source_name: str = "") -> USCaseBundle:
    case = _parse_case(case_element, source_name)
    return USCaseBundle(
        case=case,
        owners=_parse_owners(case_element, case.serial_number),
        classifications=_parse_classifications(case_element, case.serial_number),
        events=_parse_events(case_element, case.serial_number),
        statements=_parse_statements(case_element, case.serial_number),
    )


def iter_case_bundles(source: str | Path | BinaryIO, *, source_name: str = "") -> Iterator[USCaseBundle]:
    display_name = source_name or (str(source) if isinstance(source, (str, Path)) else "")
    for _event, element in ET.iterparse(source, events=("end",)):
        if _local_name(element.tag) not in {"case-file", "trademark-case-file"}:
            continue
        yield parse_case_element(element, display_name)
        element.clear()
