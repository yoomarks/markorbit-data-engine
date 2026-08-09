from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class TTABProceedingRecord:
    proceeding_number: str
    proceeding_type: str = ""
    filing_date: date | None = None
    filing_date_raw: str = ""
    status_text: str = ""
    status_date: date | None = None
    status_date_raw: str = ""
    general_contact_number: str = ""
    interlocutory_attorney: str = ""
    paralegal_name: str = ""


@dataclass(frozen=True)
class TTABPartyRecord:
    proceeding_number: str
    side: str
    ordinal: int
    party_name: str
    correspondent_name: str = ""
    correspondent_address: str = ""
    correspondent_email_text: str = ""
    correspondent_phone: str = ""


@dataclass(frozen=True)
class TTABPropertyRecord:
    proceeding_number: str
    party_side: str
    party_ordinal: int
    ordinal: int
    serial_number: str = ""
    registration_number: str = ""
    mark_text: str = ""
    application_status: str = ""


@dataclass(frozen=True)
class TTABDocketRecord:
    proceeding_number: str
    ordinal: int
    entry_number: str = ""
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
