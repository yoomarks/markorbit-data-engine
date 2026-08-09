from __future__ import annotations

from dataclasses import asdict
from datetime import date
import hashlib
import json
import re
from typing import Any
import uuid

from app.us.model import USCaseBundle


CASE_COLUMNS = [
    "case_id",
    "jurisdiction",
    "serial_number",
    "registration_number",
    "transaction_date",
    "filing_date",
    "publication_date",
    "registration_date",
    "abandonment_date",
    "cancellation_date",
    "renewal_date",
    "status_code",
    "status_date",
    "mark_identification",
    "mark_drawing_code",
    "current_location",
    "location_date",
    "examiner_name",
    "law_office_code",
    "standard_character_claimed",
    "use_1a",
    "intent_to_use_1b",
    "foreign_application_44d",
    "foreign_registration_44e",
    "madrid_66a",
    "no_basis",
    "use_1a_filed",
    "use_1a_current",
    "intent_to_use_1b_filed",
    "intent_to_use_1b_current",
    "foreign_application_44d_filed",
    "foreign_application_44d_current",
    "foreign_registration_44e_filed",
    "foreign_registration_44e_current",
    "madrid_66a_filed",
    "madrid_66a_current",
    "no_basis_current",
    "renewal_filed",
    "section_8_filed",
    "section_8_accepted",
    "section_8_partial_accepted",
    "section_15_filed",
    "section_15_acknowledged",
    "opposition_pending",
    "cancellation_pending",
    "international_registration_number",
    "international_registration_date",
    "international_publication_date",
    "international_renewal_date",
    "international_auto_protection_date",
    "international_death_date",
    "international_registration_status_code",
    "international_registration_status_date",
    "international_priority_claimed",
    "international_priority_claimed_date",
    "international_first_refusal",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_row_hash",
    "last_source_package_id",
    "record_hash",
    "source_rank",
    "is_deleted",
]

OWNER_COLUMNS = [
    "owner_key",
    "serial_number",
    "entry_number",
    "party_type",
    "legal_entity_type_code",
    "entity_statement",
    "party_name",
    "party_name_norm",
    "nationality_country",
    "nationality_state",
    "nationality_other",
    "address_1",
    "address_2",
    "city",
    "state",
    "country",
    "postcode",
    "dba_aka_text",
    "composed_of_statement",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_row_hash",
    "last_source_package_id",
    "record_hash",
    "source_rank",
    "is_deleted",
]

CLASS_COLUMNS = [
    "classification_key",
    "serial_number",
    "primary_code",
    "international_codes",
    "us_codes",
    "status_code",
    "status_date",
    "first_use_anywhere",
    "first_use_anywhere_raw",
    "first_use_commerce",
    "first_use_commerce_raw",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_row_hash",
    "last_source_package_id",
    "record_hash",
    "source_rank",
    "is_deleted",
]

EVENT_COLUMNS = [
    "event_key",
    "serial_number",
    "event_code",
    "event_date",
    "event_sequence",
    "event_type_code",
    "description_text",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_row_hash",
    "source_package_id",
    "source_rank",
]

STATEMENT_COLUMNS = [
    "statement_key",
    "serial_number",
    "type_code",
    "statement_text",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_row_hash",
    "last_source_package_id",
    "record_hash",
    "source_rank",
    "is_deleted",
]

TABLE_COLUMNS = {
    "markorbit_facts.us_case_current": CASE_COLUMNS,
    "markorbit_facts.us_owner_current": OWNER_COLUMNS,
    "markorbit_facts.us_classification_current": CLASS_COLUMNS,
    "markorbit_facts.us_event_history": EVENT_COLUMNS,
    "markorbit_facts.us_statement_current": STATEMENT_COLUMNS,
}


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Unsupported hash value: {type(value).__name__}")


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _key(*parts: object) -> str:
    return stable_hash([str(part) for part in parts])


def _name_norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def case_id(serial_number: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"markorbit:us:case:{serial_number}")


def bundle_rows(
    bundle: USCaseBundle,
    *,
    package_id: uuid.UUID,
    package_kind: str,
    source_effective_date: date | None,
    source_file: str,
    source_rank: int,
) -> dict[str, list[list[Any]]]:
    case = bundle.case
    case_hash = stable_hash(asdict(case))
    rows: dict[str, list[list[Any]]] = {table: [] for table in TABLE_COLUMNS}
    common = [package_kind, source_effective_date, source_file]

    rows["markorbit_facts.us_case_current"].append(
        [
            case_id(case.serial_number),
            "US",
            case.serial_number,
            case.registration_number,
            case.transaction_date,
            case.filing_date,
            case.publication_date,
            case.registration_date,
            case.abandonment_date,
            case.cancellation_date,
            case.renewal_date,
            case.status_code,
            case.status_date,
            case.mark_identification,
            case.mark_drawing_code,
            case.current_location,
            case.location_date,
            case.examiner_name,
            case.law_office_code,
            int(case.standard_character_claimed),
            int(case.use_1a),
            int(case.intent_to_use_1b),
            int(case.foreign_application_44d),
            int(case.foreign_registration_44e),
            int(case.madrid_66a),
            int(case.no_basis),
            int(case.use_1a_filed),
            int(case.use_1a_current),
            int(case.intent_to_use_1b_filed),
            int(case.intent_to_use_1b_current),
            int(case.foreign_application_44d_filed),
            int(case.foreign_application_44d_current),
            int(case.foreign_registration_44e_filed),
            int(case.foreign_registration_44e_current),
            int(case.madrid_66a_filed),
            int(case.madrid_66a_current),
            int(case.no_basis_current),
            int(case.renewal_filed),
            int(case.section_8_filed),
            int(case.section_8_accepted),
            int(case.section_8_partial_accepted),
            int(case.section_15_filed),
            int(case.section_15_acknowledged),
            int(case.opposition_pending),
            int(case.cancellation_pending),
            case.international_registration_number,
            case.international_registration_date,
            case.international_publication_date,
            case.international_renewal_date,
            case.international_auto_protection_date,
            case.international_death_date,
            case.international_registration_status_code,
            case.international_registration_status_date,
            int(case.international_priority_claimed),
            case.international_priority_claimed_date,
            int(case.international_first_refusal),
            *common,
            case_hash,
            package_id,
            case_hash,
            source_rank,
            0,
        ]
    )

    for owner in bundle.owners:
        record = asdict(owner)
        record_hash = stable_hash(record)
        owner_key = _key(
            owner.serial_number,
            owner.entry_number,
            owner.party_type,
            _name_norm(owner.party_name),
        )
        rows["markorbit_facts.us_owner_current"].append(
            [
                owner_key,
                owner.serial_number,
                owner.entry_number,
                owner.party_type,
                owner.legal_entity_type_code,
                owner.entity_statement,
                owner.party_name,
                _name_norm(owner.party_name),
                owner.nationality_country,
                owner.nationality_state,
                owner.nationality_other,
                owner.address_1,
                owner.address_2,
                owner.city,
                owner.state,
                owner.country,
                owner.postcode,
                owner.dba_aka_text,
                owner.composed_of_statement,
                *common,
                record_hash,
                package_id,
                record_hash,
                source_rank,
                0,
            ]
        )

    for classification in bundle.classifications:
        record = asdict(classification)
        record_hash = stable_hash(record)
        classification_key = _key(
            classification.serial_number,
            classification.primary_code,
            ",".join(classification.international_codes),
            ",".join(classification.us_codes),
        )
        rows["markorbit_facts.us_classification_current"].append(
            [
                classification_key,
                classification.serial_number,
                classification.primary_code,
                list(classification.international_codes),
                list(classification.us_codes),
                classification.status_code,
                classification.status_date,
                classification.first_use_anywhere,
                classification.first_use_anywhere_raw,
                classification.first_use_commerce,
                classification.first_use_commerce_raw,
                *common,
                record_hash,
                package_id,
                record_hash,
                source_rank,
                0,
            ]
        )

    for event in bundle.events:
        record_hash = stable_hash(asdict(event))
        event_key = _key(
            event.serial_number,
            event.event_code,
            event.event_date or "",
            event.event_sequence,
            event.event_type_code,
        )
        rows["markorbit_facts.us_event_history"].append(
            [
                event_key,
                event.serial_number,
                event.event_code,
                event.event_date,
                event.event_sequence,
                event.event_type_code,
                event.description_text,
                *common,
                record_hash,
                package_id,
                source_rank,
            ]
        )

    for statement in bundle.statements:
        record_hash = stable_hash(asdict(statement))
        statement_key = _key(
            statement.serial_number,
            statement.type_code,
            statement.text,
        )
        rows["markorbit_facts.us_statement_current"].append(
            [
                statement_key,
                statement.serial_number,
                statement.type_code,
                statement.text,
                *common,
                record_hash,
                package_id,
                record_hash,
                source_rank,
                0,
            ]
        )
    return rows


class USBatchPublisher:
    def __init__(
        self,
        client: Any,
        *,
        package_id: uuid.UUID,
        package_kind: str,
        source_effective_date: date | None,
        source_rank: int,
        batch_size: int = 1000,
    ) -> None:
        self.client = client
        self.package_id = package_id
        self.package_kind = package_kind
        self.source_effective_date = source_effective_date
        self.source_rank = source_rank
        self.batch_size = batch_size
        self.buffers: dict[str, list[list[Any]]] = {table: [] for table in TABLE_COLUMNS}
        self.counts: dict[str, int] = {table: 0 for table in TABLE_COLUMNS}
        self.case_count = 0

    def add(self, bundle: USCaseBundle, source_file: str) -> None:
        rows = bundle_rows(
            bundle,
            package_id=self.package_id,
            package_kind=self.package_kind,
            source_effective_date=self.source_effective_date,
            source_file=source_file,
            source_rank=self.source_rank,
        )
        for table, values in rows.items():
            self.buffers[table].extend(values)
        self.case_count += 1
        if self.case_count % self.batch_size == 0:
            self.flush()

    def flush(self) -> None:
        for table, rows in self.buffers.items():
            if not rows:
                continue
            self.client.insert(table, rows, column_names=TABLE_COLUMNS[table])
            self.counts[table] += len(rows)
            rows.clear()

    def close(self) -> dict[str, int]:
        self.flush()
        return dict(self.counts)
