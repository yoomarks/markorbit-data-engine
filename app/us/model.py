from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class USCaseRecord:
    serial_number: str
    registration_number: str = ""
    transaction_date: date | None = None
    filing_date: date | None = None
    publication_date: date | None = None
    registration_date: date | None = None
    abandonment_date: date | None = None
    cancellation_date: date | None = None
    renewal_date: date | None = None
    status_code: str = ""
    status_date: date | None = None
    mark_identification: str = ""
    mark_drawing_code: str = ""
    current_location: str = ""
    location_date: date | None = None
    examiner_name: str = ""
    law_office_code: str = ""
    standard_character_claimed: bool = False

    # Backward-compatible current-basis aliases used by the first US M1 API.
    use_1a: bool = False
    intent_to_use_1b: bool = False
    foreign_application_44d: bool = False
    foreign_registration_44e: bool = False
    madrid_66a: bool = False
    no_basis: bool = False

    # Real TDXF keeps filed-as and current-basis flags separately.
    use_1a_filed: bool = False
    use_1a_current: bool = False
    intent_to_use_1b_filed: bool = False
    intent_to_use_1b_current: bool = False
    foreign_application_44d_filed: bool = False
    foreign_application_44d_current: bool = False
    foreign_registration_44e_filed: bool = False
    foreign_registration_44e_current: bool = False
    madrid_66a_filed: bool = False
    madrid_66a_current: bool = False
    no_basis_current: bool = False

    renewal_filed: bool = False
    section_8_filed: bool = False
    section_8_accepted: bool = False
    section_8_partial_accepted: bool = False
    section_15_filed: bool = False
    section_15_acknowledged: bool = False
    opposition_pending: bool = False
    cancellation_pending: bool = False

    international_registration_number: str = ""
    international_registration_date: date | None = None
    international_publication_date: date | None = None
    international_renewal_date: date | None = None
    international_auto_protection_date: date | None = None
    international_death_date: date | None = None
    international_registration_status_code: str = ""
    international_registration_status_date: date | None = None
    international_priority_claimed: bool = False
    international_priority_claimed_date: date | None = None
    international_first_refusal: bool = False


@dataclass(frozen=True)
class USOwnerRecord:
    serial_number: str
    entry_number: int = 0
    party_type: str = ""
    legal_entity_type_code: str = ""
    entity_statement: str = ""
    party_name: str = ""
    nationality_country: str = ""
    nationality_state: str = ""
    nationality_other: str = ""
    address_1: str = ""
    address_2: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    postcode: str = ""
    dba_aka_text: str = ""
    composed_of_statement: str = ""


@dataclass(frozen=True)
class USClassificationRecord:
    serial_number: str
    primary_code: str = ""
    international_codes: tuple[str, ...] = ()
    us_codes: tuple[str, ...] = ()
    status_code: str = ""
    status_date: date | None = None
    first_use_anywhere: date | None = None
    first_use_anywhere_raw: str = ""
    first_use_commerce: date | None = None
    first_use_commerce_raw: str = ""


@dataclass(frozen=True)
class USEventRecord:
    serial_number: str
    event_code: str = ""
    event_date: date | None = None
    event_sequence: int = 0
    event_type_code: str = ""
    description_text: str = ""


@dataclass(frozen=True)
class USStatementRecord:
    serial_number: str
    type_code: str = ""
    text: str = ""


@dataclass(frozen=True)
class USCaseBundle:
    case: USCaseRecord
    owners: tuple[USOwnerRecord, ...] = field(default_factory=tuple)
    classifications: tuple[USClassificationRecord, ...] = field(default_factory=tuple)
    events: tuple[USEventRecord, ...] = field(default_factory=tuple)
    statements: tuple[USStatementRecord, ...] = field(default_factory=tuple)
