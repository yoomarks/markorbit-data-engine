from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class AssignmentRecord:
    reel_no: str
    frame_no: str
    reel_frame_id: str
    recorded_date: date | None = None
    recorded_date_raw: str = ""
    last_update_date: date | None = None
    last_update_date_raw: str = ""
    page_count: int | None = None
    conveyance_text: str = ""
    purge_indicator: str = ""
    correspondent_name: str = ""
    correspondent_address_1: str = ""
    correspondent_address_2: str = ""
    correspondent_address_3: str = ""
    correspondent_address_4: str = ""


@dataclass(frozen=True)
class AssignmentParty:
    reel_frame_id: str
    ordinal: int
    name: str
    address_1: str = ""
    address_2: str = ""
    city: str = ""
    state: str = ""
    postcode: str = ""
    country: str = ""
    nationality: str = ""
    legal_entity_text: str = ""
    formerly_statement: str = ""
    composed_of_statement: str = ""
    dba_statement: str = ""
    execution_date: date | None = None
    execution_date_raw: str = ""
    acknowledgement_date: date | None = None
    acknowledgement_date_raw: str = ""


@dataclass(frozen=True)
class AssignmentProperty:
    reel_frame_id: str
    ordinal: int
    serial_number: str = ""
    registration_number: str = ""
    international_registration_number: str = ""


@dataclass(frozen=True)
class AssignmentBundle:
    assignment: AssignmentRecord
    assignors: tuple[AssignmentParty, ...] = field(default_factory=tuple)
    assignees: tuple[AssignmentParty, ...] = field(default_factory=tuple)
    properties: tuple[AssignmentProperty, ...] = field(default_factory=tuple)
