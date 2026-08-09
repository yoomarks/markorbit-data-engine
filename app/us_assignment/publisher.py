from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any
import uuid

from app.us.publisher import stable_hash
from app.us_assignment.model import AssignmentBundle, AssignmentParty, AssignmentProperty


TABLE_COLUMNS = {
    "markorbit_facts.us_assignment_record_history": [
        "observation_key", "reel_frame_id", "reel_no", "frame_no",
        "recorded_date", "recorded_date_raw", "last_update_date", "last_update_date_raw",
        "page_count", "conveyance_text", "purge_indicator", "correspondent_name",
        "correspondent_address_1", "correspondent_address_2", "correspondent_address_3",
        "correspondent_address_4", "record_hash", "source_kind", "source_effective_date",
        "source_file", "source_package_id", "source_rank",
    ],
    "markorbit_facts.us_assignment_assignor_history": [
        "observation_key", "party_key", "reel_frame_id", "ordinal", "party_name",
        "address_1", "address_2", "city", "state", "postcode", "country", "nationality",
        "legal_entity_text", "formerly_statement", "composed_of_statement", "dba_statement",
        "execution_date", "execution_date_raw", "acknowledgement_date",
        "acknowledgement_date_raw", "record_hash", "source_kind", "source_effective_date",
        "source_file", "source_package_id", "source_rank",
    ],
    "markorbit_facts.us_assignment_assignee_history": [
        "observation_key", "party_key", "reel_frame_id", "ordinal", "party_name",
        "address_1", "address_2", "city", "state", "postcode", "country", "nationality",
        "legal_entity_text", "formerly_statement", "composed_of_statement", "dba_statement",
        "execution_date", "execution_date_raw", "acknowledgement_date",
        "acknowledgement_date_raw", "record_hash", "source_kind", "source_effective_date",
        "source_file", "source_package_id", "source_rank",
    ],
    "markorbit_facts.us_assignment_property_history": [
        "observation_key", "property_key", "reel_frame_id", "ordinal", "serial_number",
        "registration_number", "international_registration_number", "record_hash",
        "source_kind", "source_effective_date", "source_file", "source_package_id", "source_rank",
    ],
}


def _observation_key(kind: str, reel_frame_id: str, package_id: uuid.UUID, ordinal: int = 0) -> str:
    return stable_hash(
        {
            "kind": kind,
            "reel_frame_id": reel_frame_id,
            "source_package_id": str(package_id),
            "ordinal": ordinal,
        }
    )


def _party_row(
    party: AssignmentParty,
    *,
    role: str,
    package_id: uuid.UUID,
    source_kind: str,
    source_effective_date: date,
    source_file: str,
    source_rank: int,
) -> list[Any]:
    payload = asdict(party)
    party_key = stable_hash(
        {
            "reel_frame_id": party.reel_frame_id,
            "role": role,
            "ordinal": party.ordinal,
            "name": " ".join(party.name.split()).casefold(),
        }
    )
    return [
        _observation_key(role, party.reel_frame_id, package_id, party.ordinal),
        party_key,
        party.reel_frame_id,
        party.ordinal,
        party.name,
        party.address_1,
        party.address_2,
        party.city,
        party.state,
        party.postcode,
        party.country,
        party.nationality,
        party.legal_entity_text,
        party.formerly_statement,
        party.composed_of_statement,
        party.dba_statement,
        party.execution_date,
        party.execution_date_raw,
        party.acknowledgement_date,
        party.acknowledgement_date_raw,
        stable_hash(payload),
        source_kind,
        source_effective_date,
        source_file,
        package_id,
        source_rank,
    ]


def _property_row(
    item: AssignmentProperty,
    *,
    package_id: uuid.UUID,
    source_kind: str,
    source_effective_date: date,
    source_file: str,
    source_rank: int,
) -> list[Any]:
    payload = asdict(item)
    property_key = stable_hash(
        {
            "reel_frame_id": item.reel_frame_id,
            "ordinal": item.ordinal,
            "serial_number": item.serial_number,
            "registration_number": item.registration_number,
            "international_registration_number": item.international_registration_number,
        }
    )
    return [
        _observation_key("PROPERTY", item.reel_frame_id, package_id, item.ordinal),
        property_key,
        item.reel_frame_id,
        item.ordinal,
        item.serial_number,
        item.registration_number,
        item.international_registration_number,
        stable_hash(payload),
        source_kind,
        source_effective_date,
        source_file,
        package_id,
        source_rank,
    ]


class AssignmentBatchPublisher:
    def __init__(
        self,
        client: Any,
        *,
        package_id: uuid.UUID,
        source_kind: str,
        source_effective_date: date,
        source_rank: int,
        batch_size: int = 1000,
    ) -> None:
        self.client = client
        self.package_id = package_id
        self.source_kind = source_kind
        self.source_effective_date = source_effective_date
        self.source_rank = source_rank
        self.batch_size = batch_size
        self.buffers = {table: [] for table in TABLE_COLUMNS}
        self.counts = {table: 0 for table in TABLE_COLUMNS}

    def add(self, bundle: AssignmentBundle, source_file: str) -> None:
        record = bundle.assignment
        self.buffers["markorbit_facts.us_assignment_record_history"].append(
            [
                _observation_key("ASSIGNMENT", record.reel_frame_id, self.package_id),
                record.reel_frame_id,
                record.reel_no,
                record.frame_no,
                record.recorded_date,
                record.recorded_date_raw,
                record.last_update_date,
                record.last_update_date_raw,
                record.page_count,
                record.conveyance_text,
                record.purge_indicator,
                record.correspondent_name,
                record.correspondent_address_1,
                record.correspondent_address_2,
                record.correspondent_address_3,
                record.correspondent_address_4,
                stable_hash(asdict(record)),
                self.source_kind,
                self.source_effective_date,
                source_file,
                self.package_id,
                self.source_rank,
            ]
        )
        for party in bundle.assignors:
            self.buffers["markorbit_facts.us_assignment_assignor_history"].append(
                _party_row(
                    party,
                    role="ASSIGNOR",
                    package_id=self.package_id,
                    source_kind=self.source_kind,
                    source_effective_date=self.source_effective_date,
                    source_file=source_file,
                    source_rank=self.source_rank,
                )
            )
        for party in bundle.assignees:
            self.buffers["markorbit_facts.us_assignment_assignee_history"].append(
                _party_row(
                    party,
                    role="ASSIGNEE",
                    package_id=self.package_id,
                    source_kind=self.source_kind,
                    source_effective_date=self.source_effective_date,
                    source_file=source_file,
                    source_rank=self.source_rank,
                )
            )
        for item in bundle.properties:
            self.buffers["markorbit_facts.us_assignment_property_history"].append(
                _property_row(
                    item,
                    package_id=self.package_id,
                    source_kind=self.source_kind,
                    source_effective_date=self.source_effective_date,
                    source_file=source_file,
                    source_rank=self.source_rank,
                )
            )
        if sum(len(rows) for rows in self.buffers.values()) >= self.batch_size:
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
