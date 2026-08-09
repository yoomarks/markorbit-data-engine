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


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element.iter() if _local_name(child.tag) == name]


def _first_element(element: ET.Element, *names: str) -> ET.Element | None:
    wanted = set(names)
    for child in element.iter():
        if _local_name(child.tag) in wanted:
            return child
    return None


def _text(element: ET.Element, *names: str) -> str:
    child = _first_element(element, *names)
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
    return value.strip().upper() in {"Y", "YES", "TRUE", "1"}


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
    header = _first_element(case_element, "case-file-header")
    return header if header is not None else case_element


def _parse_case(case_element: ET.Element, source_name: str) -> USCaseRecord:
    header = _header(case_element)
    serial_number = _text(case_element, "serial-number", "serial-no")
    if not (len(serial_number) == 8 and serial_number.isdigit()):
        raise USParseError(
            f"Invalid USPTO serial number {serial_number!r} in {source_name or '<stream>'}"
        )

    return USCaseRecord(
        serial_number=serial_number,
        registration_number=_text(header, "registration-number", "registration-no"),
        filing_date=parse_uspto_date(_text(header, "filing-date", "filing-dt")),
        publication_date=parse_uspto_date(
            _text(header, "publication-date", "publication-dt")
        ),
        registration_date=parse_uspto_date(
            _text(header, "registration-date", "registration-dt")
        ),
        abandonment_date=parse_uspto_date(
            _text(header, "abandonment-date", "abandonment-dt")
        ),
        cancellation_date=parse_uspto_date(
            _text(header, "cancellation-date", "cancellation-dt")
        ),
        renewal_date=parse_uspto_date(_text(header, "renewal-date", "renewal-dt")),
        status_code=_text(header, "status-code", "status-cd"),
        status_date=parse_uspto_date(_text(header, "status-date", "status-dt")),
        mark_identification=_text(
            header,
            "mark-identification",
            "mark-id-character",
            "mark-identification-character",
        ),
        mark_drawing_code=_text(header, "mark-drawing-code", "mark-draw-code"),
        current_location=_text(header, "current-location", "location-code"),
        location_date=parse_uspto_date(_text(header, "location-date", "location-dt")),
        examiner_name=_text(header, "examiner-name", "employee-name"),
        law_office_code=_text(header, "law-office-code"),
        standard_character_claimed=_flag(
            _text(header, "standard-character-claim", "standard-character-claimed-in")
        ),
        use_1a=_flag(_text(header, "use-in-commerce-1a", "use-application-filed-in")),
        intent_to_use_1b=_flag(
            _text(header, "intent-to-use-1b", "intent-to-use-application-filed-in")
        ),
        foreign_application_44d=_flag(
            _text(header, "foreign-application-44d", "foreign-application-filed-in")
        ),
        foreign_registration_44e=_flag(
            _text(
                header,
                "foreign-registration-44e",
                "foreign-registration-application-filed-in",
            )
        ),
        madrid_66a=_flag(
            _text(header, "madrid-66a", "international-registration-filed-in")
        ),
        no_basis=_flag(_text(header, "no-basis", "no-basis-filed-in")),
        international_registration_number=_text(
            header,
            "international-registration-number",
            "international-registration-no",
        ),
        international_registration_status_code=_text(
            header,
            "international-registration-status-code",
        ),
        international_registration_status_date=parse_uspto_date(
            _text(header, "international-registration-status-date")
        ),
    )


def _parse_owners(case_element: ET.Element, serial_number: str) -> tuple[USOwnerRecord, ...]:
    owners: list[USOwnerRecord] = []
    for element in _children(case_element, "case-file-owner"):
        owners.append(
            USOwnerRecord(
                serial_number=serial_number,
                entry_number=_integer(_text(element, "entry-number", "entry-seq-no")),
                party_type=_text(element, "party-type", "party-type-code"),
                legal_entity_type_code=_text(
                    element,
                    "legal-entity-type-code",
                    "legal-entity-code",
                ),
                party_name=_text(element, "party-name", "name"),
                nationality_country=_text(
                    element,
                    "nationality-country",
                    "nationality-country-code",
                ),
                nationality_state=_text(
                    element,
                    "nationality-state",
                    "nationality-state-code",
                ),
                nationality_other=_text(element, "nationality-other"),
                address_1=_text(element, "address-1", "address1"),
                address_2=_text(element, "address-2", "address2"),
                city=_text(element, "city"),
                state=_text(element, "state", "state-code"),
                country=_text(element, "country", "country-code"),
                postcode=_text(element, "postcode", "postal-code", "zip-code"),
            )
        )
    return tuple(owners)


def _parse_classifications(
    case_element: ET.Element,
    serial_number: str,
) -> tuple[USClassificationRecord, ...]:
    records: list[USClassificationRecord] = []
    for element in _children(case_element, "classification"):
        first_use_raw = _text(
            element,
            "first-use-anywhere-date",
            "first-use-anywhere-dt",
            "first-use-date",
        )
        first_commerce_raw = _text(
            element,
            "first-use-in-commerce-date",
            "first-use-in-commerce-dt",
            "first-use-commerce-date",
        )
        records.append(
            USClassificationRecord(
                serial_number=serial_number,
                primary_code=_text(element, "primary-code"),
                international_codes=_texts(
                    element,
                    "international-code",
                    "international-class",
                ),
                us_codes=_texts(element, "us-code", "us-class"),
                status_code=_text(element, "status-code", "status-cd"),
                status_date=parse_uspto_date(_text(element, "status-date", "status-dt")),
                first_use_anywhere=parse_uspto_date(first_use_raw),
                first_use_anywhere_raw=first_use_raw,
                first_use_commerce=parse_uspto_date(first_commerce_raw),
                first_use_commerce_raw=first_commerce_raw,
            )
        )
    return tuple(records)


def _parse_events(case_element: ET.Element, serial_number: str) -> tuple[USEventRecord, ...]:
    events: list[USEventRecord] = []
    for element in _children(case_element, "case-file-event"):
        events.append(
            USEventRecord(
                serial_number=serial_number,
                event_code=_text(element, "code", "event-code", "event-cd"),
                event_date=parse_uspto_date(
                    _text(element, "date", "event-date", "event-dt")
                ),
                event_sequence=_integer(
                    _text(element, "number", "event-sequence", "event-seq")
                ),
                event_type_code=_text(element, "type", "event-type-code", "event-type-cd"),
            )
        )
    return tuple(events)


def _parse_statements(
    case_element: ET.Element,
    serial_number: str,
) -> tuple[USStatementRecord, ...]:
    statements: list[USStatementRecord] = []
    for element in _children(case_element, "case-file-statement"):
        statements.append(
            USStatementRecord(
                serial_number=serial_number,
                type_code=_text(element, "type-code", "statement-type-code"),
                text=_text(element, "text", "statement-text"),
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


def iter_case_bundles(
    source: str | Path | BinaryIO,
    *,
    source_name: str = "",
) -> Iterator[USCaseBundle]:
    if isinstance(source, (str, Path)):
        display_name = source_name or str(source)
    else:
        display_name = source_name
    for _event, element in ET.iterparse(source, events=("end",)):
        if _local_name(element.tag) not in {"case-file", "trademark-case-file"}:
            continue
        yield parse_case_element(element, display_name)
        element.clear()
