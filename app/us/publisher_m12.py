from __future__ import annotations

from datetime import date
from typing import Any
import uuid

from app.us.model import USCaseBundle
from app.us.publisher import TABLE_COLUMNS, USBatchPublisher, stable_hash


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


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


class SnapshotAwareUSBatchPublisher(USBatchPublisher):
    """Publish complete USPTO case snapshots without leaving stale child rows current.

    TDXF case files are complete observations for the current child families declared in
    ``SNAPSHOT_CHILD_TABLES``. A later source-ranked observation tombstones an older active child
    identity when that identity disappears from the new case snapshot.

    General case events and Madrid history events are intentionally excluded. Both event families
    are cumulative official evidence and remain source-ranked histories rather than replace-all
    collections.
    """

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
        self.tombstone_counts: dict[str, int] = {
            table: 0 for table in SNAPSHOT_CHILD_TABLES
        }

    def add(self, bundle: USCaseBundle, source_file: str) -> None:
        self._touched_serial_sources[bundle.case.serial_number] = source_file
        super().add(bundle, source_file)

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
                tombstone = list(existing)
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

    def flush(self) -> None:
        self._append_snapshot_tombstones()
        super().flush()
        self._touched_serial_sources.clear()
