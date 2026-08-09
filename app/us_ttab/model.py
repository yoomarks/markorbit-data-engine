from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class TTABProceedingRecord:
    proceeding_number: str
    proceeding_type: str = ""
    proceeding_type_code: str = ""
    filing_date: date | None = None
    filing_date_raw: str = ""
    status_text: str = ""
    status_code: str = ""
    status_date: date | None = None
    status_date_raw: str = ""
    general_contact_number: str = ""
    interlocutory_attorney: str = ""
    paralegal_name: str = ""
    employee_number: str = ""
    location_code: str = ""
    day_in_location: date | None = None
    day_in_location_raw: str = ""
    charge_to_location_code: str = ""
    charge_to_employee_name: str = ""


@dataclass(frozen=True)
class TTABPartyRecord:
    proceeding_number: str
    side: str
    ordinal: int
    party_name: str
    party_id: str = ""
    role: str = ""
    company: str = ""
    organization: str = ""
    granted_to_date_raw: str = ""
    correspondent_name: str = ""
    correspondent_organization: str = ""
    correspondent_address: str = ""
    correspondent_email_text: str = ""
    correspondent_phone: str = ""
    correspondent_address_id: str = ""
    correspondent_address_type_code: str = ""


@dataclass(frozen=True)
class TTABPropertyRecord:
    proceeding_number: str
    party_side: str
    party_ordinal: int
    ordinal: int
    serial_number: str = ""
    registration_number: str = ""
    mark_text: str = ""
    mark_explanation: str = ""
    property_filing: str = ""
    property_filing_code: str = ""
    common_law_indicator: str = ""
    application_status: str = ""
    application_status_code: str = ""
    trademark_gid: str = ""
    source_property_id: str = ""
    tma_proceeding_number: str = ""
    tma_proceeding_type_code: str = ""


@dataclass(frozen=True)
class TTABDocketRecord:
    proceeding_number: str
    ordinal: int
    entry_number: str = ""
    identifier: str = ""
    object_id: str = ""
    entry_code: str = ""
    entry_type_code: str = ""
    confidential: str = ""
    filing_date: date | None = None
    filing_date_raw: str = ""
    history_text: str = ""
    due_date: date | None = None
    due_date_raw: str = ""
    document_url: str = ""


@dataclass(frozen=True)
class TTABProceedingBundle:
    proceeding: TTABProceedingRecord
    parties: tuple[TTABPartyRecord, ...] = field(default_factory=tuple)
    properties: tuple[TTABPropertyRecord, ...] = field(default_factory=tuple)
    docket_entries: tuple[TTABDocketRecord, ...] = field(default_factory=tuple)
