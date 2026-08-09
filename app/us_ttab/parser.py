from __future__ import annotations

from datetime import date
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
_PROCEEDING_TAGS = {
    "proceeding",
    "proceeding-record",
    "proceeding-file",
    "proceeding-information",
    "case-record",
    "case",
}
_PARTY_SIDE_TAGS = {"plaintiff", "defendant", "applicant", "party"}
_PROPERTY_TAGS = {"property", "application-registration", "trademark-property"}
_DOCKET_TAGS = {
    "prosecution-history-entry",
    "history-entry",
    "docket-entry",
    "prosecution-entry",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower().replace("_", "-")


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return _clean(" ".join(node.itertext()))


def _direct_text(node: ET.Element, *names: str) -> str:
    wanted = {name.lower().replace("_", "-") for name in names}
    for child in list(node):
        if _local(child.tag) in wanted:
            value = _text(child)
            if value:
                return value
    return ""


def _first_text(node: ET.Element, *names: str) -> str:
    direct = _direct_text(node, *names)
    if direct:
        return direct
    wanted = {name.lower().replace("_", "-") for name in names}
    for child in node.iter():
        if child is node:
            continue
        if _local(child.tag) in wanted:
            value = _text(child)
            if value:
                return value
    return ""


def _descendants(node: ET.Element, names: Iterable[str]) -> list[ET.Element]:
    wanted = {name.lower().replace("_", "-") for name in names}
    return [child for child in node.iter() if child is not node and _local(child.tag) in wanted]


def _parse_date_raw(raw: str) -> date | None:
    value = raw.strip()
    if not value:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _proceeding_number(node: ET.Element) -> str:
    direct = _direct_text(
        node,
        "proceeding-number",
        "proceeding-no",
        "proceeding-num",
        "case-number",
    )
    if direct:
        return direct.replace(" ", "")
    return _first_text(
        node,
        "proceeding-number",
        "proceeding-no",
        "proceeding-num",
        "case-number",
    ).replace(" ", "")


def _candidate_proceedings(root: ET.Element) -> list[ET.Element]:
    candidates: list[ET.Element] = []
    if _local(root.tag) in _PROCEEDING_TAGS and _proceeding_number(root):
        candidates.append(root)
    for node in root.iter():
        if node is root:
            continue
        if _local(node.tag) in _PROCEEDING_TAGS and _proceeding_number(node):
            candidates.append(node)
    if candidates:
        return candidates

    # Fallback for TTABVUE XML wrappers whose proceeding element has an unexpected
    # local name. Only accept elements with a direct proceeding/case number child so
    # nested party/property/docket numbers cannot be mistaken for a proceeding.
    for node in root.iter():
        number = _direct_text(
            node,
            "proceeding-number",
            "proceeding-no",
            "proceeding-num",
            "case-number",
        ).replace(" ", "")
        if number:
            candidates.append(node)
    return candidates


def _correspondent(party: ET.Element) -> tuple[str, str, str, str]:
    nodes = _descendants(
        party,
        {"correspondence", "correspondent", "correspondence-information"},
    )
    node = nodes[0] if nodes else party
    name = _first_text(
        node,
        "correspondent-name",
        "person-or-organization-name",
        "name",
    )
    address_parts: list[str] = []
    for key in (
        "address-1",
        "address-2",
        "address-3",
        "address-4",
        "city",
        "state",
        "postal-code",
        "postcode",
        "country",
    ):
        value = _direct_text(node, key)
        if value:
            address_parts.append(value)
    email = _first_text(node, "email", "email-address", "email-text")
    phone = _first_text(node, "phone", "phone-number", "telephone")
    return name, "\n".join(address_parts), email, phone


def _property_nodes(party: ET.Element) -> list[ET.Element]:
    nodes = _descendants(party, _PROPERTY_TAGS)
    usable = [
        node
        for node in nodes
        if _first_text(
            node,
            "serial-number",
            "serial-no",
            "registration-number",
            "registration-no",
            "mark",
            "mark-text",
            "property-name",
        )
    ]
    if usable:
        return usable
    if _first_text(party, "serial-number", "registration-number", "mark-text"):
        return [party]
    return []


def _party_records(
    node: ET.Element,
    proceeding_number: str,
) -> tuple[list[TTABPartyRecord], list[TTABPropertyRecord]]:
    parties: list[TTABPartyRecord] = []
    properties: list[TTABPropertyRecord] = []
    counters: dict[str, int] = {}

    for party_node in _descendants(node, _PARTY_SIDE_TAGS):
        local = _local(party_node.tag)
        side = local.upper() if local != "party" else _first_text(
            party_node,
            "party-type",
            "party-side",
            "role",
        ).upper()
        side = side or "OTHER"
        name = _direct_text(party_node, "party-name", "name") or _first_text(
            party_node,
            "party-name",
            "name",
        )
        if not name:
            continue
        counters[side] = counters.get(side, 0) + 1
        ordinal = counters[side]
        corr_name, corr_address, corr_email, corr_phone = _correspondent(party_node)
        parties.append(
            TTABPartyRecord(
                proceeding_number=proceeding_number,
                side=side,
                ordinal=ordinal,
                party_name=name,
                correspondent_name=corr_name,
                correspondent_address=corr_address,
                correspondent_email_text=corr_email,
                correspondent_phone=corr_phone,
            )
        )
        for property_ordinal, prop in enumerate(_property_nodes(party_node), 1):
            properties.append(
                TTABPropertyRecord(
                    proceeding_number=proceeding_number,
                    party_side=side,
                    party_ordinal=ordinal,
                    ordinal=property_ordinal,
                    serial_number=_first_text(prop, "serial-number", "serial-no").replace(" ", ""),
                    registration_number=_first_text(
                        prop,
                        "registration-number",
                        "registration-no",
                    ).replace(" ", ""),
                    mark_text=_first_text(prop, "mark", "mark-text", "property-name"),
                    application_status=_first_text(
                        prop,
                        "application-status",
                        "property-status",
                        "status",
                    ),
                )
            )
    return parties, properties


def _docket_records(node: ET.Element, proceeding_number: str) -> list[TTABDocketRecord]:
    records: list[TTABDocketRecord] = []
    for ordinal, item in enumerate(_descendants(node, _DOCKET_TAGS), 1):
        history_text = _first_text(
            item,
            "history-text",
            "history",
            "description",
            "history-description",
            "text",
        )
        entry_number = _first_text(
            item,
            "entry-number",
            "history-number",
            "document-number",
            "number",
        )
        filing_raw = _first_text(item, "filing-date", "history-date", "date")
        due_raw = _first_text(item, "due-date", "deadline-date", "answer-due-date")
        if not any((history_text, entry_number, filing_raw, due_raw)):
            continue
        document_url = _first_text(item, "document-url", "file-url", "url")
        if not document_url:
            for child in item.iter():
                href = child.attrib.get("href") or child.attrib.get("url")
                if href:
                    document_url = _clean(href)
                    break
        records.append(
            TTABDocketRecord(
                proceeding_number=proceeding_number,
                ordinal=ordinal,
                entry_number=entry_number,
                filing_date=_parse_date_raw(filing_raw),
                filing_date_raw=filing_raw,
                history_text=history_text,
                due_date=_parse_date_raw(due_raw),
                due_date_raw=due_raw,
                document_url=document_url,
            )
        )
    return records


def parse_proceeding(node: ET.Element) -> TTABProceedingBundle:
    number = _proceeding_number(node)
    if not number:
        raise ValueError("TTAB proceeding snapshot is missing proceeding number")
    if not (number.isdigit() and 6 <= len(number) <= 8):
        raise ValueError(f"Invalid TTAB proceeding number: {number!r}")

    filing_raw = _direct_text(node, "proceeding-filing-date", "filing-date") or _first_text(
        node,
        "proceeding-filing-date",
    )
    status_raw = _direct_text(node, "proceeding-status-date", "status-date") or _first_text(
        node,
        "proceeding-status-date",
    )
    proceeding_type = _direct_text(node, "proceeding-type", "case-type") or _first_text(
        node,
        "proceeding-type",
        "case-type",
    )
    status_text = _direct_text(node, "proceeding-status", "status-text") or _first_text(
        node,
        "proceeding-status",
        "status-text",
    )
    proceeding = TTABProceedingRecord(
        proceeding_number=number,
        proceeding_type=proceeding_type,
        filing_date=_parse_date_raw(filing_raw),
        filing_date_raw=filing_raw,
        status_text=status_text,
        status_date=_parse_date_raw(status_raw),
        status_date_raw=status_raw,
        general_contact_number=_first_text(node, "general-contact-number", "contact-number"),
        interlocutory_attorney=_first_text(
            node,
            "interlocutory-attorney",
            "interlocutory-attorney-name",
        ),
        paralegal_name=_first_text(node, "paralegal-name", "paralegal"),
    )
    parties, properties = _party_records(node, number)
    docket = _docket_records(node, number)
    return TTABProceedingBundle(
        proceeding=proceeding,
        parties=tuple(parties),
        properties=tuple(properties),
        docket_entries=tuple(docket),
    )


def iter_ttab_bundles(source: Source):
    root = ET.parse(source).getroot()
    seen: set[str] = set()
    for node in _candidate_proceedings(root):
        bundle = parse_proceeding(node)
        number = bundle.proceeding.proceeding_number
        if number in seen:
            continue
        seen.add(number)
        yield bundle
