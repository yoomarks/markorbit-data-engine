from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any
import uuid

from app.us.publisher import stable_hash
from app.us_ttab.model import TTABProceedingBundle


TABLE_COLUMNS: dict[str, list[str]] = {
    "markorbit_facts.us_ttab_proceeding_history": [
        "observation_key",
        "proceeding_number",
        "proceeding_type",
        "filing_date",
        "filing_date_raw",
        "status_text",
        "status_date",
        "status_date_raw",
        "general_contact_number",
        "interlocutory_attorney",
        "paralegal_name",
        "record_hash",
        "source_kind",
        "source_snapshot_at",
        "source_file",
        "source_package_id",
        "source_rank",
    ],
    "markorbit_facts.us_ttab_party_history": [
        "observation_key",
        "party_key",
        "proceeding_number",
        "side",
        "ordinal",
        "party_name",
        "correspondent_name",
        "correspondent_address",
        "correspondent_email_text",
        "correspondent_phone",
        "record_hash",
        "source_kind",
        "source_snapshot_at",
        "source_file",
        "source_package_id",
        "source_rank",
    ],
    "markorbit_facts.us_ttab_property_history": [
        "observation_key",
        "property_key",
        "proceeding_number",
        "party_side",
        "party_ordinal",
        "ordinal",
        "serial_number",
        "registration_number",
        "mark_text",
        "application_status",
        "record_hash",
        "source_kind",
        "source_snapshot_at",
        "source_file",
        "source_package_id",
        "source_rank",
    ],
    "markorbit_facts.us_ttab_docket_history": [
        "observation_key",
        "docket_key",
        "proceeding_number",
        "ordinal",
        "entry_number",
        "filing_date",
        "filing_date_raw",
        "history_text",
        "due_date",
        "due_date_raw",
        "document_url",
        "record_hash",
        "source_kind",
        "source_snapshot_at",
        "source_file",
        "source_package_id",
        "source_rank",
    ],
}


def _key(*parts: object) -> str:
    return stable_hash(["" if part is None else str(part) for part in parts])


def bundle_rows(
    bundle: TTABProceedingBundle,
    *,
    package_id: uuid.UUID,
    source_kind: str,
    source_snapshot_at: datetime,
    source_file: str,
    source_rank: int,
) -> dict[str, list[list[Any]]]:
    rows = {table: [] for table in TABLE_COLUMNS}
    proceeding = bundle.proceeding
    proceeding_hash = stable_hash(asdict(proceeding))
    rows["markorbit_facts.us_ttab_proceeding_history"].append(
        [
            _key(package_id, proceeding.proceeding_number),
            proceeding.proceeding_number,
            proceeding.proceeding_type,
            proceeding.filing_date,
            proceeding.filing_date_raw,
            proceeding.status_text,
            proceeding.status_date,
            proceeding.status_date_raw,
            proceeding.general_contact_number,
            proceeding.interlocutory_attorney,
            proceeding.paralegal_name,
            proceeding_hash,
            source_kind,
            source_snapshot_at,
            source_file,
            package_id,
            source_rank,
        ]
    )

    for party in bundle.parties:
        party_key = _key(party.side, party.ordinal, party.party_name)
        record_hash = stable_hash(asdict(party))
        rows["markorbit_facts.us_ttab_party_history"].append(
            [
                _key(package_id, proceeding.proceeding_number, party_key),
                party_key,
                party.proceeding_number,
                party.side,
                party.ordinal,
                party.party_name,
                party.correspondent_name,
                party.correspondent_address,
                party.correspondent_email_text,
                party.correspondent_phone,
                record_hash,
                source_kind,
                source_snapshot_at,
                source_file,
                package_id,
                source_rank,
            ]
        )

    for item in bundle.properties:
        property_key = _key(
            item.party_side,
            item.party_ordinal,
            item.ordinal,
            item.serial_number,
            item.registration_number,
            item.mark_text,
        )
        record_hash = stable_hash(asdict(item))
        rows["markorbit_facts.us_ttab_property_history"].append(
            [
                _key(package_id, proceeding.proceeding_number, property_key),
                property_key,
                item.proceeding_number,
                item.party_side,
                item.party_ordinal,
                item.ordinal,
                item.serial_number,
                item.registration_number,
                item.mark_text,
                item.application_status,
                record_hash,
                source_kind,
                source_snapshot_at,
                source_file,
                package_id,
                source_rank,
            ]
        )

    for item in bundle.docket_entries:
        stable_identity = item.entry_number or _key(
            item.filing_date_raw,
            item.history_text,
            item.ordinal,
        )
        docket_key = _key(stable_identity)
        record_hash = stable_hash(asdict(item))
        rows["markorbit_facts.us_ttab_docket_history"].append(
            [
                _key(package_id, proceeding.proceeding_number, docket_key),
                docket_key,
                item.proceeding_number,
                item.ordinal,
                item.entry_number,
                item.filing_date,
                item.filing_date_raw,
                item.history_text,
                item.due_date,
                item.due_date_raw,
                item.document_url,
                record_hash,
                source_kind,
                source_snapshot_at,
                source_file,
                package_id,
                source_rank,
            ]
        )
    return rows


class TTABBatchPublisher:
    def __init__(
        self,
        client: Any,
        *,
        package_id: uuid.UUID,
        source_kind: str,
        source_snapshot_at: datetime,
        source_rank: int,
        batch_size: int = 500,
    ) -> None:
        self.client = client
        self.package_id = package_id
        self.source_kind = source_kind
        self.source_snapshot_at = source_snapshot_at
        self.source_rank = source_rank
        self.batch_size = batch_size
        self.buffers: dict[str, list[list[Any]]] = {table: [] for table in TABLE_COLUMNS}
        self.counts: dict[str, int] = {table: 0 for table in TABLE_COLUMNS}
        self.proceeding_count = 0

    def add(self, bundle: TTABProceedingBundle, source_file: str) -> None:
        rows = bundle_rows(
            bundle,
            package_id=self.package_id,
            source_kind=self.source_kind,
            source_snapshot_at=self.source_snapshot_at,
            source_file=source_file,
            source_rank=self.source_rank,
        )
        for table, values in rows.items():
            self.buffers[table].extend(values)
        self.proceeding_count += 1
        if self.proceeding_count % self.batch_size == 0:
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
