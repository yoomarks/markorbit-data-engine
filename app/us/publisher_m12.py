from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date
from typing import Any
import uuid

from app.us.change_history import (
    CASE_OBSERVATION_COLUMNS,
    CASE_OBSERVATION_TABLE,
    build_case_observation_row,
)
from app.us.model import USCaseBundle
from app.us.publisher import (
    TABLE_COLUMNS,
    USBatchPublisher,
    _madrid_filing_identity,
    stable_hash,
)


SNAPSHOT_CHILD_TABLES = {
    "markorbit_facts.us_owner_current": "owner_key",
    "markorbit_facts.us_classification_current": "classification_key",
    "markorbit_facts.us_statement_current": "statement_key",
    "markorbit_facts.us_correspondent_current": "correspondent_key",
    "markorbit_facts.us_design_search_current": "design_search_key",
    "markorbit_facts.us_prior_registration_current": "prior_registration_key",
    "markorbit_facts.us_foreign_application_current": "foreign_application_key",
    "markorbit_facts.us_madrid_filing_current": "madrid_filing_key",
}
CURRENT_SNAPSHOT_TABLES = {"markorbit_facts.us_case_current", *SNAPSHOT_CHILD_TABLES}


def _text(value: object) -> str:
    """Normalize ClickHouse string-like values at the read boundary."""
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return str(value)


def _normalize_queried_value(value: object) -> object:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _text(value)
    return value


def _compact_madrid_filing_snapshot(bundle: USCaseBundle) -> USCaseBundle:
    """Keep one deterministic current Madrid filing row per filing identity.

    Real USPTO application XML may repeat one Madrid reference across several
    entry numbers and historical international-status dates. Those source rows
    remain available to package observations/history, but the replaceable
    current table needs exactly one winner for the reference.
    """
    if len(bundle.madrid_filings) < 2:
        return bundle

    grouped: dict[tuple[object, ...], list[Any]] = {}
    for filing in bundle.madrid_filings:
        grouped.setdefault(_madrid_filing_identity(filing), []).append(filing)

    if all(len(records) == 1 for records in grouped.values()):
        return bundle

    winners: list[Any] = []
    for identity in sorted(grouped, key=lambda item: tuple(str(value) for value in item)):
        candidates = grouped[identity]
        latest_status_date = max(
            (record.international_status_date or date.min for record in candidates),
            default=date.min,
        )
        candidates = [
            record
            for record in candidates
            if (record.international_status_date or date.min) == latest_status_date
        ]

        latest_entry_number = max((record.entry_number for record in candidates), default=0)
        candidates = [
            record for record in candidates if record.entry_number == latest_entry_number
        ]

        by_hash = {stable_hash(asdict(record)): record for record in candidates}
        if len(by_hash) != 1:
            raise ValueError(
                "Ambiguous Madrid filing current snapshot for "
                f"identity={identity!r}, status_date={latest_status_date}, "
                f"entry_number={latest_entry_number}"
            )
        winners.append(next(iter(by_hash.values())))

    return replace(bundle, madrid_filings=tuple(winners))


class SnapshotAwareUSBatchPublisher(USBatchPublisher):
    """Publish current snapshots plus durable per-package case observations."""

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
        super().__init__(
            client,
            package_id=package_id,
            package_kind=package_kind,
            source_effective_date=source_effective_date,
            source_rank=source_rank,
            batch_size=batch_size,
        )
        self._touched_serial_sources: dict[str, str] = {}
        self._touched_serial_transactions: dict[str, date | None] = {}
        self.tombstone_counts: dict[str, int] = {
            table: 0 for table in SNAPSHOT_CHILD_TABLES
        }
        self.observation_buffer: list[list[Any]] = []
        self.observation_count = 0

    def add(self, bundle: USCaseBundle, source_file: str) -> None:
        serial = bundle.case.serial_number
        self._touched_serial_sources[serial] = source_file
        self._touched_serial_transactions[serial] = bundle.case.transaction_date
        self.observation_buffer.append(
            build_case_observation_row(
                bundle,
                package_id=self.package_id,
                package_kind=self.package_kind,
                source_effective_date=self.source_effective_date,
                source_file=source_file,
                source_rank=self.source_rank,
            )
        )
        super().add(_compact_madrid_filing_snapshot(bundle), source_file)

    def _stale_current_serials(self) -> set[str]:
        if not self._touched_serial_transactions:
            return set()
        serial_sql = ", ".join(
            f"'{serial}'" for serial in sorted(self._touched_serial_transactions)
        )
        rows = self.client.query(
            f"""
            SELECT serial_number, transaction_date
            FROM markorbit_facts.us_case_current FINAL
            WHERE is_deleted = 0
              AND serial_number IN ({serial_sql})
            """
        ).result_rows
        existing_dates = {
            _text(serial): transaction_date
            for serial, transaction_date in rows
            if transaction_date is not None
        }
        return {
            serial
            for serial, incoming_date in self._touched_serial_transactions.items()
            if incoming_date is not None
            and serial in existing_dates
            and incoming_date < existing_dates[serial]
        }

    def _drop_stale_current_rows(self, stale_serials: set[str]) -> None:
        if not stale_serials:
            return
        for table in CURRENT_SNAPSHOT_TABLES:
            columns = TABLE_COLUMNS[table]
            serial_index = columns.index("serial_number")
            self.buffers[table][:] = [
                row
                for row in self.buffers[table]
                if _text(row[serial_index]) not in stale_serials
            ]
        for serial in stale_serials:
            self._touched_serial_sources.pop(serial, None)
            self._touched_serial_transactions.pop(serial, None)

    def _append_snapshot_tombstones(self) -> None:
        if not self._touched_serial_sources:
            return

        serials = sorted(self._touched_serial_sources)
        serial_sql = ", ".join(f"'{serial}'" for serial in serials)

        for table, key_column in SNAPSHOT_CHILD_TABLES.items():
            columns = TABLE_COLUMNS[table]
            serial_index = columns.index("serial_number")
            key_index = columns.index(key_column)
            desired_keys: dict[str, set[str]] = {serial: set() for serial in serials}
            for row in self.buffers[table]:
                serial = _text(row[serial_index])
                if serial in desired_keys:
                    desired_keys[serial].add(_text(row[key_index]))

            column_sql = ", ".join(columns)
            existing_rows = self.client.query(
                f"""
                SELECT {column_sql}
                FROM {table} FINAL
                WHERE is_deleted = 0
                  AND source_rank < {self.source_rank}
                  AND serial_number IN ({serial_sql})
                """
            ).result_rows

            for existing in existing_rows:
                serial = _text(existing[serial_index])
                key = _text(existing[key_index])
                if serial not in desired_keys or key in desired_keys[serial]:
                    continue

                source_file = self._touched_serial_sources[serial]
                tombstone = [_normalize_queried_value(value) for value in existing]
                tombstone_hash = stable_hash(
                    {
                        "kind": "US_CHILD_SNAPSHOT_OMISSION_V1",
                        "table": table,
                        "serial_number": serial,
                        "record_key": key,
                        "source_effective_date": self.source_effective_date,
                        "source_file": source_file,
                        "source_rank": self.source_rank,
                    }
                )
                tombstone[columns.index("source_package_kind")] = self.package_kind
                tombstone[columns.index("source_effective_date")] = self.source_effective_date
                tombstone[columns.index("source_file")] = source_file
                tombstone[columns.index("source_row_hash")] = tombstone_hash
                tombstone[columns.index("last_source_package_id")] = self.package_id
                tombstone[columns.index("record_hash")] = tombstone_hash
                tombstone[columns.index("source_rank")] = self.source_rank
                tombstone[columns.index("is_deleted")] = 1
                self.buffers[table].append(tombstone)
                self.tombstone_counts[table] += 1

    def _flush_observations(self) -> None:
        if not self.observation_buffer:
            return
        self.client.insert(
            CASE_OBSERVATION_TABLE,
            self.observation_buffer,
            column_names=CASE_OBSERVATION_COLUMNS,
        )
        self.observation_count += len(self.observation_buffer)
        self.observation_buffer.clear()

    def flush(self) -> None:
        self._drop_stale_current_rows(self._stale_current_serials())
        self._append_snapshot_tombstones()
        super().flush()
        self._flush_observations()
        self._touched_serial_sources.clear()
        self._touched_serial_transactions.clear()

    def close(self) -> dict[str, int]:
        counts = super().close()
        counts[CASE_OBSERVATION_TABLE] = self.observation_count
        return counts
