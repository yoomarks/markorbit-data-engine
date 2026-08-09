from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator
import xml.etree.ElementTree as ET

from app.us_ttab.model import (
    TTABDocketRecord,
    TTABPartyRecord,
    TTABProceedingBundle,
    TTABProceedingRecord,
    TTABPropertyRecord,
)


Source = str | Path | BinaryIO
_PROCEEDING_TAGS = {"proceeding", "proceeding-record", "proceeding-file", "case-record", "case"}
_PARTY_SIDE_TAGS = {"plaintiff", "defendant", "applicant", "party"}
_PROPERTY_TAGS = {"property", "application-registration", "trademark-property"}
_DOCKET_TAGS = {"prosecution-history-entry", "history-entry", "docket-entry", "prosecution-entry"}


def _norm(value: str) -> str:
    return value.lower().replace("_", "-")


def _local(tag: str) -> str:
    return _norm(tag.rsplit("}", 1)[-1])


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _text(node: ET.Element | None) -> str:
    return _clean(" ".join(node.itertext())) if node is not None else ""


def _attr(node: ET.Element | None, *names: str) -> str:
    if node is None:
        return ""
    wanted = {_norm(name) for name in names}
    for key, value in node.attrib.items():
        if _norm(str(key)) in wanted:
            cleaned = _clean(str(value))
            if cleaned:
                return cleaned
    return ""


def _child(node: ET.Element | None, *names: str) -> ET.Element | None:
    if node is None:
        return None
    wanted = {_norm(name) for name in names}
    for child in list(node):
        if _local(child.tag) in wanted:
            return child
    return None


def _direct_text(node: ET.Element, *names: str) -> str:
    return _text(_child(node, *names))


def _first(node: ET.Element, *names: str) -> ET.Element | None:
    direct = _child(node, *names)
    if direct is not None:
        return direct
    wanted = {_norm(name) for name in names}
    for child in node.iter():
        if child is not node and _local(child.tag) in wanted:
            return child
    return None


def _first_text(node: ET.Element, *names: str) -> str:
    return _text(_first(node, *names))


def _descendants(node: ET.Element, names: Iterable[str]) -> list[ET.Element]:
    wanted = {_norm(name) for name in names}
    return [child for child in node.iter() if child is not node and _local(child.tag) in wanted]


def _parse_date_raw(raw: str) -> date | None:
    value = raw.strip()
    if not value:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _code_display(node: ET.Element | None) -> tuple[str, str]:
    if node is None:
        return "", ""
    code = _text(node)
    display = _attr(node, "name") or code
    return code, display


def _staff_name(node: ET.Element | None) -> str:
    if node is None:
        return ""
    first = _direct_text(node, "first-name")
    last = _direct_text(node, "last-name")
    full = _clean(f"{first} {last}")
    return full or _text(node)


def _proceeding_attributes(node: ET.Element) -> ET.Element:
    attrs = _child(node, "proceeding-attributes")
    return attrs if attrs is not None else node


def _proceeding_number(node: ET.Element) -> str:
    attrs = _proceeding_attributes(node)
    return (
        _direct_text(attrs, "proceeding-number", "proceeding-no", "proceeding-num", "case-number")
        or _first_text(attrs, "proceeding-number", "proceeding-no", "proceeding-num", "case-number")
    ).replace(" ", "")


def _address_from_attributes(node: ET.Element | None) -> str:
    if node is None:
        return ""
    parts = [
        _attr(node, "address-1"),
        _attr(node, "address-2"),
        _attr(node, "address-3"),
        _attr(node, "city"),
        _attr(node, "state"),
        _attr(node, "postcode", "postal-code"),
    ]
    country = _child(node, "country")
    parts.append(_attr(country, "country-name") or _text(country))
    return "\n".join(value for value in parts if value)


def _address_from_bulk(node: ET.Element | None) -> str:
    if node is None:
        return ""
    parts = [
        _direct_text(node, "address-1"),
        _direct_text(node, "address-2"),
        _direct_text(node, "address-3"),
        _direct_text(node, "address-4"),
        _direct_text(node, "city"),
        _direct_text(node, "state"),
        _direct_text(node, "country"),
        _direct_text(node, "postcode", "postal-code"),
    ]
    return "\n".join(value for value in parts if value)


def _real_property_records(
    party: ET.Element, proceeding_number: str, side: str, party_ordinal: int
) -> list[TTABPropertyRecord]:
    container = _child(party, "ttab-properties")
    if container is None:
        return []
    records: list[TTABPropertyRecord] = []
    for ordinal, prop in enumerate([x for x in list(container) if _local(x.tag) == "ttab-property"], 1):
        status_node = _first(prop, "application-status")
        status_code, status_name = _code_display(status_node)
        records.append(
            TTABPropertyRecord(
                proceeding_number=proceeding_number,
                party_side=side,
                party_ordinal=party_ordinal,
                ordinal=ordinal,
                serial_number=_first_text(prop, "serial-number").replace(" ", ""),
                registration_number=_first_text(prop, "registration-number").replace(" ", ""),
                mark_explanation=_first_text(prop, "mark-explanation"),
                property_filing=_first_text(prop, "property-filing"),
                property_filing_code=_first_text(prop, "property-filing-cd"),
                common_law_indicator=_first_text(prop, "common-law-ind"),
                application_status=status_name,
                application_status_code=status_code,
                trademark_gid=_first_text(prop, "trademark-gid"),
            )
        )
    return records


def _real_party_records(
    node: ET.Element, proceeding_number: str
) -> tuple[list[TTABPartyRecord], list[TTABPropertyRecord]]:
    root = _child(node, "proceeding-parties")
    if root is None:
        return [], []
    parties: list[TTABPartyRecord] = []
    properties: list[TTABPropertyRecord] = []
    for container in list(root):
        local = _local(container.tag)
        if local not in {"plaintiffs", "defendants", "applicants"}:
            continue
        side = {"plaintiffs": "PLAINTIFF", "defendants": "DEFENDANT", "applicants": "APPLICANT"}[local]
        party_nodes = [item for item in list(container) if _local(item.tag) == "party"]
        for ordinal, party in enumerate(party_nodes, 1):
            db = _child(party, "party-info-from-db")
            correspondence = _child(party, "correspondence-info-from-db")
            party_name = _attr(party, "name") or _attr(db, "name", "orgname")
            corr_address_node = _child(correspondence, "contact-address")
            parties.append(
                TTABPartyRecord(
                    proceeding_number=proceeding_number,
                    side=side,
                    ordinal=ordinal,
                    party_name=party_name,
                    party_id=_attr(party, "party-id"),
                    role=_attr(party, "role"),
                    company=_attr(party, "company"),
                    organization=_attr(party, "organization") or _attr(db, "orgname"),
                    granted_to_date_raw=_attr(party, "granted-to-date"),
                    correspondent_name=_attr(correspondence, "name"),
                    correspondent_organization=_attr(correspondence, "orgname"),
                    correspondent_address=_address_from_attributes(corr_address_node),
                    correspondent_email_text=_attr(correspondence, "email"),
                    correspondent_phone=_attr(correspondence, "phone"),
                )
            )
            properties.extend(_real_property_records(party, proceeding_number, side, ordinal))
    return parties, properties


def _legacy_correspondent(party: ET.Element) -> tuple[str, str, str, str]:
    candidates = _descendants(party, {"correspondence", "correspondent", "correspondence-information"})
    node = candidates[0] if candidates else party
    name = _first_text(node, "correspondent-name", "person-or-organization-name", "name")
    parts = [
        _direct_text(node, key)
        for key in ("address-1", "address-2", "city", "state", "postal-code", "country")
    ]
    return (
        name,
        "\n".join(x for x in parts if x),
        _first_text(node, "email", "email-address"),
        _first_text(node, "phone", "phone-number"),
    )


def _legacy_party_records(
    node: ET.Element, proceeding_number: str
) -> tuple[list[TTABPartyRecord], list[TTABPropertyRecord]]:
    parties: list[TTABPartyRecord] = []
    properties: list[TTABPropertyRecord] = []
    counters: dict[str, int] = {}
    for party_node in _descendants(node, _PARTY_SIDE_TAGS):
        local = _local(party_node.tag)
        side = (
            local.upper()
            if local != "party"
            else _first_text(party_node, "party-type", "party-side", "role").upper() or "OTHER"
        )
        name = _direct_text(party_node, "party-name", "name") or _first_text(
            party_node, "party-name", "name"
        )
        if not name:
            continue
        counters[side] = counters.get(side, 0) + 1
        ordinal = counters[side]
        corr_name, corr_address, corr_email, corr_phone = _legacy_correspondent(party_node)
        parties.append(
            TTABPartyRecord(
                proceeding_number,
                side,
                ordinal,
                name,
                correspondent_name=corr_name,
                correspondent_address=corr_address,
                correspondent_email_text=corr_email,
                correspondent_phone=corr_phone,
            )
        )
        prop_nodes = _descendants(party_node, _PROPERTY_TAGS)
        for prop_ordinal, prop in enumerate(prop_nodes, 1):
            status = _first_text(prop, "application-status", "property-status", "status")
            properties.append(
                TTABPropertyRecord(
                    proceeding_number,
                    side,
                    ordinal,
                    prop_ordinal,
                    serial_number=_first_text(prop, "serial-number", "serial-no").replace(" ", ""),
                    registration_number=_first_text(
                        prop, "registration-number", "registration-no"
                    ).replace(" ", ""),
                    mark_text=_first_text(prop, "mark", "mark-text", "property-name"),
                    application_status=status,
                    application_status_code=status,
                )
            )
    return parties, properties


def _real_docket_records(node: ET.Element, proceeding_number: str) -> list[TTABDocketRecord]:
    history = _child(node, "prosecution-history")
    if history is None:
        return []
    records: list[TTABDocketRecord] = []
    events = [item for item in list(history) if _local(item.tag) == "prosecution-history-event"]
    for ordinal, item in enumerate(events, 1):
        identifier = _attr(item, "identifier")
        object_id = _attr(item, "object-id")
        entry_code = _attr(item, "entry-code")
        filing_raw = _attr(item, "entry-date", "event-date")
        due_raw = _attr(item, "due-date")
        records.append(
            TTABDocketRecord(
                proceeding_number=proceeding_number,
                ordinal=ordinal,
                entry_number=identifier,
                identifier=identifier,
                object_id=object_id,
                entry_code=entry_code,
                confidential=_attr(item, "confidential"),
                filing_date=_parse_date_raw(filing_raw),
                filing_date_raw=filing_raw,
                history_text=_attr(item, "event-text"),
                due_date=_parse_date_raw(due_raw),
                due_date_raw=due_raw,
            )
        )
    return records


def _legacy_docket_records(node: ET.Element, proceeding_number: str) -> list[TTABDocketRecord]:
    records: list[TTABDocketRecord] = []
    for ordinal, item in enumerate(_descendants(node, _DOCKET_TAGS), 1):
        history_text = _first_text(item, "history-text", "history", "description", "text")
        entry_number = _first_text(
            item, "entry-number", "history-number", "document-number", "number"
        )
        filing_raw = _first_text(item, "filing-date", "history-date", "date")
        due_raw = _first_text(item, "due-date", "deadline-date", "answer-due-date")
        if any((history_text, entry_number, filing_raw, due_raw)):
            records.append(
                TTABDocketRecord(
                    proceeding_number,
                    ordinal,
                    entry_number=entry_number,
                    identifier=entry_number,
                    filing_date=_parse_date_raw(filing_raw),
                    filing_date_raw=filing_raw,
                    history_text=history_text,
                    due_date=_parse_date_raw(due_raw),
                    due_date_raw=due_raw,
                    document_url=_first_text(item, "document-url", "file-url", "url"),
                )
            )
    return records


def parse_proceeding(node: ET.Element) -> TTABProceedingBundle:
    """Parse TTABVUE rawxml/legacy proceeding shapes."""
    number = _proceeding_number(node)
    if not number or not (number.isdigit() and 6 <= len(number) <= 8):
        raise ValueError(f"Invalid TTAB proceeding number: {number!r}")
    attrs = _proceeding_attributes(node)
    type_node = _child(attrs, "proceeding-type", "case-type")
    if type_node is None:
        type_node = _first(attrs, "proceeding-type", "case-type")
    status_node = _child(attrs, "proceeding-status", "status-text")
    if status_node is None:
        status_node = _first(attrs, "proceeding-status", "status-text")
    type_code, type_name = _code_display(type_node)
    status_code, status_name = _code_display(status_node)
    filing_raw = _direct_text(attrs, "filing-date", "proceeding-filing-date")
    status_raw = _direct_text(attrs, "proceeding-status-date", "status-date")
    proceeding = TTABProceedingRecord(
        proceeding_number=number,
        proceeding_type=type_name,
        proceeding_type_code=type_code,
        filing_date=_parse_date_raw(filing_raw),
        filing_date_raw=filing_raw,
        status_text=status_name,
        status_code=status_code,
        status_date=_parse_date_raw(status_raw),
        status_date_raw=status_raw,
        general_contact_number=_direct_text(attrs, "general-contact-number", "contact-number"),
        interlocutory_attorney=_staff_name(
            _child(attrs, "interlocutory-attorney", "interlocutory-attorney-name")
        ),
        paralegal_name=_staff_name(_child(attrs, "paralegal-name", "paralegal")),
    )
    parties, properties = _real_party_records(node, number)
    if not parties:
        parties, properties = _legacy_party_records(node, number)
    docket = _real_docket_records(node, number) or _legacy_docket_records(node, number)
    return TTABProceedingBundle(proceeding, tuple(parties), tuple(properties), tuple(docket))


def _bulk_property_records(
    party: ET.Element, proceeding_number: str, side: str, party_ordinal: int
) -> list[TTABPropertyRecord]:
    container = _child(party, "property-information")
    if container is None:
        return []
    records: list[TTABPropertyRecord] = []
    props = [node for node in list(container) if _local(node.tag) == "property"]
    for ordinal, prop in enumerate(props, 1):
        tma = _child(prop, "tma-proceeding")
        records.append(
            TTABPropertyRecord(
                proceeding_number=proceeding_number,
                party_side=side,
                party_ordinal=party_ordinal,
                ordinal=ordinal,
                serial_number=_direct_text(prop, "serial-number").replace(" ", ""),
                registration_number=_direct_text(prop, "registration-number").replace(" ", ""),
                mark_text=_direct_text(prop, "mark-text"),
                source_property_id=_direct_text(prop, "identifier"),
                tma_proceeding_number=(
                    _direct_text(tma, "proceeding-number") if tma is not None else ""
                ),
                tma_proceeding_type_code=(
                    _direct_text(tma, "proceeding-type-code") if tma is not None else ""
                ),
            )
        )
    return records


def _bulk_party_records(
    node: ET.Element, proceeding_number: str
) -> tuple[list[TTABPartyRecord], list[TTABPropertyRecord]]:
    root = _child(node, "party-information")
    if root is None:
        return [], []
    parties: list[TTABPartyRecord] = []
    properties: list[TTABPropertyRecord] = []
    for ordinal, party in enumerate([x for x in list(root) if _local(x.tag) == "party"], 1):
        role_code = _direct_text(party, "role-code")
        # Bulk role codes are retained verbatim. Do not infer plaintiff/defendant semantics
        # without an evidence-bound official code reference.
        side = f"ROLE_{role_code}" if role_code else "OTHER"
        address_info = _child(party, "address-information")
        address = _child(address_info, "proceeding-address") if address_info is not None else None
        parties.append(
            TTABPartyRecord(
                proceeding_number=proceeding_number,
                side=side,
                ordinal=ordinal,
                party_name=_direct_text(party, "name"),
                party_id=_direct_text(party, "identifier"),
                role=role_code,
                organization=_direct_text(party, "orgname"),
                correspondent_name=(
                    _direct_text(address, "name") if address is not None else ""
                ),
                correspondent_organization=(
                    _direct_text(address, "orgname") if address is not None else ""
                ),
                correspondent_address=_address_from_bulk(address),
                correspondent_address_id=(
                    _direct_text(address, "identifier") if address is not None else ""
                ),
                correspondent_address_type_code=(
                    _direct_text(address, "type-code") if address is not None else ""
                ),
            )
        )
        properties.extend(_bulk_property_records(party, proceeding_number, side, ordinal))
    return parties, properties


def _bulk_docket_records(node: ET.Element, proceeding_number: str) -> list[TTABDocketRecord]:
    history = _child(node, "prosecution-history")
    if history is None:
        return []
    records: list[TTABDocketRecord] = []
    entries = [item for item in list(history) if _local(item.tag) == "prosecution-entry"]
    for ordinal, item in enumerate(entries, 1):
        identifier = _direct_text(item, "identifier")
        filing_raw = _direct_text(item, "date")
        due_raw = _direct_text(item, "due-date")
        records.append(
            TTABDocketRecord(
                proceeding_number=proceeding_number,
                ordinal=ordinal,
                entry_number=identifier,
                identifier=identifier,
                entry_code=_direct_text(item, "code"),
                entry_type_code=_direct_text(item, "type-code"),
                filing_date=_parse_date_raw(filing_raw),
                filing_date_raw=filing_raw,
                history_text=_direct_text(item, "history-text"),
                due_date=_parse_date_raw(due_raw),
                due_date_raw=due_raw,
            )
        )
    return records


def parse_bulk_proceeding_entry(node: ET.Element) -> TTABProceedingBundle:
    """Parse the official TTAB bulk-data `proceeding-entry` contract.

    Code fields remain raw codes. This layer does not assign legal meaning to them.
    """
    number = _direct_text(node, "number").replace(" ", "")
    if not number or not (number.isdigit() and 6 <= len(number) <= 8):
        raise ValueError(f"Invalid TTAB bulk proceeding number: {number!r}")
    filing_raw = _direct_text(node, "filing-date")
    status_raw = _direct_text(node, "status-update-date")
    day_raw = _direct_text(node, "day-in-location")
    proceeding = TTABProceedingRecord(
        proceeding_number=number,
        proceeding_type="",
        proceeding_type_code=_direct_text(node, "type-code"),
        filing_date=_parse_date_raw(filing_raw),
        filing_date_raw=filing_raw,
        status_text="",
        status_code=_direct_text(node, "status-code"),
        status_date=_parse_date_raw(status_raw),
        status_date_raw=status_raw,
        interlocutory_attorney=_direct_text(node, "interlocutory-attorney-name"),
        employee_number=_direct_text(node, "employee-number"),
        location_code=_direct_text(node, "location-code"),
        day_in_location=_parse_date_raw(day_raw),
        day_in_location_raw=day_raw,
        charge_to_location_code=_direct_text(node, "charge-to-location-code"),
        charge_to_employee_name=_direct_text(node, "charge-to-employee-name"),
    )
    parties, properties = _bulk_party_records(node, number)
    docket = _bulk_docket_records(node, number)
    return TTABProceedingBundle(proceeding, tuple(parties), tuple(properties), tuple(docket))


def iter_ttab_bundles(source: Source) -> Iterator[TTABProceedingBundle]:
    """Stream TTABVUE rawxml, legacy XML, and official bulk XML.

    The official historical bulk file is hundreds of MB compressed / roughly a GB
    uncompressed, so parsing must remain record-streaming rather than `ET.parse()`.
    """
    seen: set[str] = set()
    context = ET.iterparse(source, events=("end",))
    for _event, element in context:
        local = _local(element.tag)
        bundle: TTABProceedingBundle | None = None
        if local == "proceeding-entry":
            bundle = parse_bulk_proceeding_entry(element)
        elif local in _PROCEEDING_TAGS and _proceeding_number(element):
            bundle = parse_proceeding(element)
        if bundle is None:
            continue
        number = bundle.proceeding.proceeding_number
        if number not in seen:
            seen.add(number)
            yield bundle
        element.clear()
