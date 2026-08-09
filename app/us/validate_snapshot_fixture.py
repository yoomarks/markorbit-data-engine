from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
import time
import uuid

from app.db import clickhouse_client
from app.us.ingest import _cleanup_package_outputs
from app.us.migrations import ensure_us_m1_schema
from app.us.model import (
    USCaseBundle,
    USCaseRecord,
    USClassificationRecord,
    USEventRecord,
    USOwnerRecord,
    USStatementRecord,
)
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher


SERIAL = "88990003"
OLD_RANK = 17_900_000_000_000_000_001
NEW_RANK = 17_900_000_000_000_000_002


def _old_bundle() -> USCaseBundle:
    return USCaseBundle(
        case=USCaseRecord(
            serial_number=SERIAL,
            filing_date=date(2025, 1, 2),
            status_code="630",
            status_date=date(2025, 12, 31),
            mark_identification="MARKORBIT SNAPSHOT FIXTURE",
        ),
        owners=(
            USOwnerRecord(
                serial_number=SERIAL,
                entry_number=1,
                party_type="10",
                party_name="Old Snapshot Owner LLC",
                country="US",
            ),
        ),
        classifications=(
            USClassificationRecord(
                serial_number=SERIAL,
                primary_code="009",
                international_codes=("009",),
                status_code="6",
            ),
        ),
        events=(
            USEventRecord(
                serial_number=SERIAL,
                event_code="NWAP",
                event_date=date(2025, 1, 2),
                event_sequence=1,
                event_type_code="A",
                description_text="OLD SNAPSHOT EVENT",
            ),
        ),
        statements=(
            USStatementRecord(
                serial_number=SERIAL,
                type_code="GS0091",
                text="Old snapshot goods statement.",
            ),
        ),
    )


def _new_bundle(old: USCaseBundle) -> USCaseBundle:
    return replace(
        old,
        case=replace(old.case, status_code="700", status_date=date(2026, 1, 8)),
        owners=(),
        classifications=(),
        statements=(),
        events=(
            USEventRecord(
                serial_number=SERIAL,
                event_code="R.PR",
                event_date=date(2026, 1, 8),
                event_sequence=2,
                event_type_code="A",
                description_text="NEW SNAPSHOT EVENT",
            ),
        ),
    )


def _scalar(sql: str) -> int:
    rows = clickhouse_client().query(sql).result_rows
    return int(rows[0][0]) if rows else 0


def main() -> None:
    started = time.perf_counter()
    ensure_us_m1_schema()
    old_package_id = uuid.uuid4()
    new_package_id = uuid.uuid4()
    old = _old_bundle()
    new = _new_bundle(old)

    try:
        old_publisher = SnapshotAwareUSBatchPublisher(
            clickhouse_client(),
            package_id=old_package_id,
            package_kind="HISTORICAL_APPLICATIONS",
            source_effective_date=date(2025, 12, 31),
            source_rank=OLD_RANK,
            batch_size=100,
        )
        old_publisher.add(old, "apc18840407-20251231-05.xml")
        old_publisher.close()

        new_publisher = SnapshotAwareUSBatchPublisher(
            clickhouse_client(),
            package_id=new_package_id,
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 8),
            source_rank=NEW_RANK,
            batch_size=100,
        )
        new_publisher.add(new, "apc260108.xml")
        new_publisher.close()

        checks = {
            "case_current": _scalar(
                "SELECT count() FROM markorbit_facts.us_case_current FINAL "
                f"WHERE serial_number = '{SERIAL}' AND status_code = '700' AND is_deleted = 0"
            ),
            "owner_current": _scalar(
                "SELECT count() FROM markorbit_facts.us_owner_current FINAL "
                f"WHERE serial_number = '{SERIAL}' AND is_deleted = 0"
            ),
            "classification_current": _scalar(
                "SELECT count() FROM markorbit_facts.us_classification_current FINAL "
                f"WHERE serial_number = '{SERIAL}' AND is_deleted = 0"
            ),
            "statement_current": _scalar(
                "SELECT count() FROM markorbit_facts.us_statement_current FINAL "
                f"WHERE serial_number = '{SERIAL}' AND is_deleted = 0"
            ),
            "event_history": _scalar(
                "SELECT count() FROM markorbit_facts.us_event_history FINAL "
                f"WHERE serial_number = '{SERIAL}'"
            ),
        }
        expected = {
            "case_current": 1,
            "owner_current": 0,
            "classification_current": 0,
            "statement_current": 0,
            "event_history": 2,
        }
        expected_tombstones = {
            "markorbit_facts.us_owner_current": 1,
            "markorbit_facts.us_classification_current": 1,
            "markorbit_facts.us_statement_current": 1,
            "markorbit_facts.us_correspondent_current": 0,
            "markorbit_facts.us_design_search_current": 0,
            "markorbit_facts.us_prior_registration_current": 0,
            "markorbit_facts.us_foreign_application_current": 0,
            "markorbit_facts.us_madrid_filing_current": 0,
        }
        if checks != expected:
            raise RuntimeError(
                f"US M1.2 snapshot current-state contract failed: {checks} != {expected}"
            )
        if new_publisher.tombstone_counts != expected_tombstones:
            raise RuntimeError(
                "US M1.2 snapshot tombstone contract failed: "
                f"{new_publisher.tombstone_counts} != {expected_tombstones}"
            )

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_M1.2_CHILD_SNAPSHOT_FIXTURE",
                    "old_package_id": str(old_package_id),
                    "new_package_id": str(new_package_id),
                    "checks": checks,
                    "tombstones": new_publisher.tombstone_counts,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_package_outputs(new_package_id)
        _cleanup_package_outputs(old_package_id)
        residual = sum(
            _scalar(f"SELECT count() FROM {table} FINAL WHERE serial_number = '{SERIAL}'")
            for table in (
                "markorbit_facts.us_case_current",
                "markorbit_facts.us_owner_current",
                "markorbit_facts.us_classification_current",
                "markorbit_facts.us_event_history",
                "markorbit_facts.us_statement_current",
            )
        )
        if residual:
            raise RuntimeError(
                f"US M1.2 snapshot fixture cleanup failed: residual_rows={residual}"
            )


if __name__ == "__main__":
    main()
