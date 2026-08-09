from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
import time
import uuid

from app.db import clickhouse_client
from app.us.change_history import build_case_timeline, scan_change_feed_page
from app.us.ingest import _cleanup_package_outputs
from app.us.migrations import US_SCHEMA_VERSION, ensure_us_m1_schema
from app.us.model import USCaseBundle, USCaseRecord, USOwnerRecord
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher


SERIAL = "88990014"
OLD_RANK = 17_900_000_000_000_000_041
NEW_RANK = 17_900_000_000_000_000_042


def _old_bundle() -> USCaseBundle:
    return USCaseBundle(
        case=USCaseRecord(
            serial_number=SERIAL,
            registration_number="7000014",
            filing_date=date(2025, 1, 2),
            status_code="630",
            status_date=date(2025, 12, 31),
            current_location="TMO LAW OFFICE 114",
            mark_identification="MARKORBIT HISTORY FIXTURE",
            intent_to_use_1b_current=True,
        ),
        owners=(
            USOwnerRecord(
                serial_number=SERIAL,
                entry_number=1,
                party_type="10",
                legal_entity_type_code="16",
                party_name="Alpha History Owner LLC",
                country="US",
                address_1="1 Alpha Street",
            ),
        ),
    )


def _new_bundle(old: USCaseBundle) -> USCaseBundle:
    return replace(
        old,
        case=replace(
            old.case,
            status_code="700",
            status_date=date(2026, 1, 8),
            current_location="PUBLICATION AND ISSUE SECTION",
        ),
        owners=(
            USOwnerRecord(
                serial_number=SERIAL,
                entry_number=1,
                party_type="10",
                legal_entity_type_code="16",
                party_name="Beta History Owner Inc.",
                country="US",
                address_1="2 Beta Avenue",
            ),
        ),
    )


def _scalar(sql: str) -> int:
    rows = clickhouse_client().query(sql).result_rows
    return int(rows[0][0]) if rows else 0


def main() -> None:
    started = time.perf_counter()
    ensure_us_m1_schema()
    if US_SCHEMA_VERSION != "US_M1.4":
        raise RuntimeError(f"Unexpected US schema version: {US_SCHEMA_VERSION}")

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
        old_publisher.add(old, "history-fixture-old.xml")
        old_counts = old_publisher.close()

        new_publisher = SnapshotAwareUSBatchPublisher(
            clickhouse_client(),
            package_id=new_package_id,
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 8),
            source_rank=NEW_RANK,
            batch_size=100,
        )
        new_publisher.add(new, "history-fixture-new.xml")
        new_counts = new_publisher.close()

        timeline = build_case_timeline(SERIAL)
        if timeline["observation_count"] != 2:
            raise RuntimeError(f"Expected two durable observations: {timeline}")
        if len(timeline["changes"]) != 1:
            raise RuntimeError(f"Expected one derived change: {timeline}")
        change = timeline["changes"][0]
        required_change_types = {
            "STATUS_CODE_CHANGED",
            "CURRENT_LOCATION_CHANGED",
            "OWNER_IDENTITY_SET_CHANGED",
        }
        if not required_change_types.issubset(set(change["change_types"])):
            raise RuntimeError(f"Missing expected change types: {change}")
        if change["field_changes"]["owners"] != {
            "before": ["Alpha History Owner LLC"],
            "after": ["Beta History Owner Inc."],
        }:
            raise RuntimeError(f"Owner transition mismatch: {change}")

        feed = scan_change_feed_page(
            after_source_rank=OLD_RANK,
            after_serial=SERIAL,
            scan_limit=10,
        )
        if feed["scanned_observation_count"] != 1 or feed["change_count"] != 1:
            raise RuntimeError(f"Change feed cursor contract failed: {feed}")
        feed_change = feed["changes"][0]
        if "OWNER_IDENTITY_SET_CHANGED" not in feed_change["change_types"]:
            raise RuntimeError(f"Change feed owner transition missing: {feed_change}")
        if feed["legal_ownership_conclusion"] is not False:
            raise RuntimeError("Change feed must never claim legal ownership conclusion")

        observed_rows = _scalar(
            "SELECT count() FROM markorbit_facts.us_case_observation_history "
            f"WHERE serial_number = '{SERIAL}'"
        )
        if observed_rows != 2:
            raise RuntimeError(f"Durable history row count mismatch: {observed_rows}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_M1.4_DURABLE_CHANGE_HISTORY_FIXTURE",
                    "schema_version": US_SCHEMA_VERSION,
                    "old_package_id": str(old_package_id),
                    "new_package_id": str(new_package_id),
                    "old_observation_rows": old_counts.get(
                        "markorbit_facts.us_case_observation_history"
                    ),
                    "new_observation_rows": new_counts.get(
                        "markorbit_facts.us_case_observation_history"
                    ),
                    "observation_count": timeline["observation_count"],
                    "change_types": change["change_types"],
                    "feed_change_count": feed["change_count"],
                    "legal_status_inference": False,
                    "legal_ownership_conclusion": False,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_package_outputs(new_package_id)
        _cleanup_package_outputs(old_package_id)
        residual = _scalar(
            "SELECT count() FROM markorbit_facts.us_case_observation_history "
            f"WHERE serial_number = '{SERIAL}'"
        )
        if residual:
            raise RuntimeError(
                f"US M1.4 change-history fixture cleanup failed: residual_rows={residual}"
            )


if __name__ == "__main__":
    main()
