from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterator, TextIO
import xml.etree.ElementTree as ET

from app.us_assignment.model import (
    AssignmentBundle,
    AssignmentParty,
    AssignmentProperty,
    AssignmentRecord,
)


Source = str | Path | BinaryIO | TextIO


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower().replace("_", "-")


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _first_text(node: ET.Element, *names: str) -> str:
    wanted = {name.lower().replace("_", "-") for name in names}
    for child in node.iter():
        if child is node:
            continue
        if _local(child.tag) in wanted:
            value = _clean(child.text)
            if value:
                return value
    return ""


def _first_all_text(node: ET.Element, *names: str) -> str:
    """Return flattened text for mixed-content official fields without interpreting it."""
    wanted = {name.lower().replace("_", "-") for name in names}
    for child in node.iter():
        if child is node:
            continue
        if _local(child.tag) in wanted:
            value = _clean(" ".join(child.itertext()))
            if value:
                return value
    return ""


def _direct_children(node: ET.Element, names: set[str]) -> list[ET.Element]:
    wanted = {name.lower().replace("_", "-") for name in names}
    return [child for child in list(node) if _local(child.tag) in wanted]


def _descendants(node: ET.Element, names: set[str]) -> list[ET.Element]:
    wanted = {name.lower().replace("_", "-") for name in names}
    return [child for child in node.iter() if child is not node and _local(child.tag) in wanted]


def _parse_date_raw(value: str) -> date | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) != 8 or digits.endswith("00"):
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _int_or_none(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _reel_frame(reel: str, frame: str) -> str:
    reel = reel.strip()
    frame = frame.strip()
    if not reel or not frame:
        raise ValueError("USPTO assignment entry is missing reel-no or frame-no")
    return f"{reel}/{frame}"


def _correspondent(assignment: ET.Element) -> tuple[str, str, str, str, str]:
    nodes = _descendants(assignment, {"correspondent"})
    if not nodes:
        return "", "", "", "", ""
    node = nodes[0]
    name = _first_text(node, "person-or-organization-name", "name")
    return (
        name,
        _first_text(node, "address-1", "address1"),
        _first_text(node, "address-2", "address2"),
        _first_text(node, "address-3", "address3"),
        _first_text(node, "address-4", "address4"),
    )


def _party(node: ET.Element, reel_frame_id: str, ordinal: int) -> AssignmentParty:
    execution_raw = _first_text(node, "execution-date", "date-executed")
    acknowledgement_raw = _first_text(
        node,
        "date-acknowledged",
        "acknowledgement-date",
        "acknowledgment-date",
    )
    return AssignmentParty(
        reel_frame_id=reel_frame_id,
        ordinal=ordinal,
        name=_first_text(node, "name", "person-or-organization-name"),
        address_1=_first_text(node, "address-1", "address1"),
        address_2=_first_text(node, "address-2", "address2"),
        city=_first_text(node, "city"),
        state=_first_text(node, "state", "state-code"),
        postcode=_first_text(node, "postcode", "postal-code", "zip-code"),
        country=_first_text(node, "country", "country-name", "country-code"),
        nationality=_first_text(node, "nationality", "citizenship"),
        legal_entity_text=_first_text(
            node,
            "legal-entity-text",
            "legal-entity",
            "entity-type",
        ),
        formerly_statement=_first_text(node, "formerly-statement", "formerly"),
        composed_of_statement=_first_all_text(
            node,
            "composed-of-statement",
            "composed-of",
        ),
        dba_statement=_first_text(
            node,
            "dba-aka-ta-statement",
            "dba-statement",
            "dba",
        ),
        execution_date=_parse_date_raw(execution_raw),
        execution_date_raw=execution_raw,
        acknowledgement_date=_parse_date_raw(acknowledgement_raw),
        acknowledgement_date_raw=acknowledgement_raw,
    )


def _party_nodes(entry: ET.Element, singular: str, plural: str) -> list[ET.Element]:
    containers = _descendants(entry, {plural})
    nodes: list[ET.Element] = []
    for container in containers:
        nodes.extend(_direct_children(container, {singular}))
    if nodes:
        return nodes
    return _descendants(entry, {singular})


def _property_nodes(entry: ET.Element) -> list[ET.Element]:
    candidates = _descendants(
        entry,
        {
            "document-id",
            "property",
            "property-number",
            "trademark-property",
        },
    )
    usable = [
        node
        for node in candidates
        if _first_text(
            node,
            "serial-number",
            "serial-no",
            "registration-number",
            "registration-no",
            "international-registration-number",
            "intl-reg-no",
        )
    ]
    if usable:
        return usable

    # Some historical XMLs expose serial/registration fields directly below a
    # properties container. Treat each direct child containing one of the known
    # identifiers as a property record, without inventing identifiers.
    containers = _descendants(entry, {"properties", "document-ids", "trademarks"})
    result: list[ET.Element] = []
    for container in containers:
        for child in list(container):
            if _first_text(
                child,
                "serial-number",
                "serial-no",
                "registration-number",
                "registration-no",
                "international-registration-number",
                "intl-reg-no",
            ):
                result.append(child)
    return result


def parse_assignment_entry(entry: ET.Element) -> AssignmentBundle:
    assignments = _descendants(entry, {"assignment"})
    assignment = assignments[0] if assignments else entry
    reel = _first_text(assignment, "reel-no", "reel-number")
    frame = _first_text(assignment, "frame-no", "frame-number")
    reel_frame_id = _reel_frame(reel, frame)
    recorded_raw = _first_text(assignment, "date-recorded", "recorded-date")
    last_update_raw = _first_text(assignment, "last-update-date", "last-update-dt")
    correspondent = _correspondent(assignment)

    record = AssignmentRecord(
        reel_no=reel,
        frame_no=frame,
        reel_frame_id=reel_frame_id,
        recorded_date=_parse_date_raw(recorded_raw),
        recorded_date_raw=recorded_raw,
        last_update_date=_parse_date_raw(last_update_raw),
        last_update_date_raw=last_update_raw,
        page_count=_int_or_none(_first_text(assignment, "page-count")),
        conveyance_text=_first_text(assignment, "conveyance-text", "convey-text"),
        purge_indicator=_first_text(assignment, "purge-indicator", "purge-in"),
        correspondent_name=correspondent[0],
        correspondent_address_1=correspondent[1],
        correspondent_address_2=correspondent[2],
        correspondent_address_3=correspondent[3],
        correspondent_address_4=correspondent[4],
    )

    assignors = tuple(
        _party(node, reel_frame_id, index)
        for index, node in enumerate(_party_nodes(entry, "assignor", "assignors"), 1)
    )
    assignees = tuple(
        _party(node, reel_frame_id, index)
        for index, node in enumerate(_party_nodes(entry, "assignee", "assignees"), 1)
    )
    properties = tuple(
        AssignmentProperty(
            reel_frame_id=reel_frame_id,
            ordinal=index,
            serial_number=_first_text(node, "serial-number", "serial-no"),
            registration_number=_first_text(
                node,
                "registration-number",
                "registration-no",
            ),
            international_registration_number=_first_text(
                node,
                "international-registration-number",
                "intl-reg-no",
            ),
        )
        for index, node in enumerate(_property_nodes(entry), 1)
    )
    return AssignmentBundle(
        assignment=record,
        assignors=assignors,
        assignees=assignees,
        properties=properties,
    )


def iter_assignment_bundles(source: Source) -> Iterator[AssignmentBundle]:
    context = ET.iterparse(source, events=("end",))
    for _event, element in context:
        if _local(element.tag) not in {"assignment-entry", "trademark-assignment"}:
            continue
        yield parse_assignment_entry(element)
        element.clear()
