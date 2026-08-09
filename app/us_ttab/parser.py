from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, Iterable
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
    child = _child(node, *names)
    return _text(child)


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
    return _child(node, "proceeding-attributes") or node


def _proceeding_number(node: ET.Element) -> str:
    attrs = _proceeding_attributes(node)
    return (_direct_text(attrs, "proceeding-number", "proceeding-no", "proceeding-num", "case-number") or _first_text(
        attrs, "proceeding-number", "proceeding-no", "proceeding-num", "case-number"
    )).replace(" ", "")


def _candidate_proceedings(root: ET.Element) -> list[ET.Element]:
    candidates = [node for node in root.iter() if _local(node.tag) in _PROCEEDING_TAGS and _proceeding_number(node)]
    if candidates:
        return candidates
    return [
        node for node in root.iter()
        if _direct_text(_proceeding_attributes(node), "proceeding-number", "case-number")
    ]


def _address_from_attributes(node: ET.Element | None) -> str:
    if node is None:
        return ""
    parts = [
        _attr(node, "address-1"), _attr(node, "address-2"), _attr(node, "address-3"),
        _attr(node, "city"), _attr(node, "state"), _attr(node, "postcode", "postal-code"),
    ]
    country = _child(node, "country")
    parts.append(_attr(country, "country-name") or _text(country))
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


def _real_party_records(node: ET.Element, proceeding_number: str) -> tuple[list[TTABPartyRecord], list[TTABPropertyRecord]]:
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
    node = (_descendants(party, {"correspondence", "correspondent", "correspondence-information"}) or [party])[0]
    name = _first_text(node, "correspondent-name", "person-or-organization-name", "name")
    parts = [_direct_text(node, key) for key in ("address-1", "address-2", "city", "state", "postal-code", "country")]
    return name, "\n".join(x for x in parts if x), _first_text(node, "email", "email-address"), _first_text(node, "phone", "phone-number")


def _legacy_party_records(node: ET.Element, proceeding_number: str) -> tuple[list[TTABPartyRecord], list[TTABPropertyRecord]]:
    parties: list[TTABPartyRecord] = []
    properties: list[TTABPropertyRecord] = []
    counters: dict[str, int] = {}
    for party_node in _descendants(node, _PARTY_SIDE_TAGS):
        local = _local(party_node.tag)
        side = local.upper() if local != "party" else _first_text(party_node, "party-type", "party-side", "role").upper() or "OTHER"
        name = _direct_text(party_node, "party-name", "name") or _first_text(party_node, "party-name", "name")
        if not name:
            continue
        counters[side] = counters.get(side, 0) + 1
        ordinal = counters[side]
        corr_name, corr_address, corr_email, corr_phone = _legacy_correspondent(party_node)
        parties.append(TTABPartyRecord(proceeding_number, side, ordinal, name, correspondent_name=corr_name, correspondent_address=corr_address, correspondent_email_text=corr_email, correspondent_phone=corr_phone))
        prop_nodes = _descendants(party_node, _PROPERTY_TAGS)
        for prop_ordinal, prop in enumerate(prop_nodes, 1):
            status = _first_text(prop, "application-status", "property-status", "status")
            properties.append(TTABPropertyRecord(
                proceeding_number, side, ordinal, prop_ordinal,
                serial_number=_first_text(prop, "serial-number", "serial-no").replace(" ", ""),
                registration_number=_first_text(prop, "registration-number", "registration-no").replace(" ", ""),
                mark_text=_first_text(prop, "mark", "mark-text", "property-name"),
                application_status=status,
                application_status_code=status,
            ))
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
        records.append(TTABDocketRecord(
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
        ))
    return records


def _legacy_docket_records(node: ET.Element, proceeding_number: str) -> list[TTABDocketRecord]:
    records: list[TTABDocketRecord] = []
    for ordinal, item in enumerate(_descendants(node, _DOCKET_TAGS), 1):
        history_text = _first_text(item, "history-text", "history", "description", "text")
        entry_number = _first_text(item, "entry-number", "history-number", "document-number", "number")
        filing_raw = _first_text(item, "filing-date", "history-date", "date")
        due_raw = _first_text(item, "due-date", "deadline-date", "answer-due-date")
        if any((history_text, entry_number, filing_raw, due_raw)):
            records.append(TTABDocketRecord(
                proceeding_number, ordinal, entry_number=entry_number, identifier=entry_number,
                filing_date=_parse_date_raw(filing_raw), filing_date_raw=filing_raw,
                history_text=history_text, due_date=_parse_date_raw(due_raw), due_date_raw=due_raw,
                document_url=_first_text(item, "document-url", "file-url", "url"),
            ))
    return records


def parse_proceeding(node: ET.Element) -> TTABProceedingBundle:
    number = _proceeding_number(node)
    if not number or not (number.isdigit() and 6 <= len(number) <= 8):
        raise ValueError(f"Invalid TTAB proceeding number: {number!r}")
    attrs = _proceeding_attributes(node)
    type_node = _child(attrs, "proceeding-type", "case-type") or _first(attrs, "proceeding-type", "case-type")
    status_node = _child(attrs, "proceeding-status", "status-text") or _first(attrs, "proceeding-status", "status-text")
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
        interlocutory_attorney=_staff_name(_child(attrs, "interlocutory-attorney", "interlocutory-attorney-name")),
        paralegal_name=_staff_name(_child(attrs, "paralegal-name", "paralegal")),
    )
    parties, properties = _real_party_records(node, number)
    if not parties:
        parties, properties = _legacy_party_records(node, number)
    docket = _real_docket_records(node, number) or _legacy_docket_records(node, number)
    return TTABProceedingBundle(proceeding, tuple(parties), tuple(properties), tuple(docket))


def iter_ttab_bundles(source: Source):
    root = ET.parse(source).getroot()
    seen: set[str] = set()
    for node in _candidate_proceedings(root):
        bundle = parse_proceeding(node)
        if bundle.proceeding.proceeding_number not in seen:
            seen.add(bundle.proceeding.proceeding_number)
            yield bundle
