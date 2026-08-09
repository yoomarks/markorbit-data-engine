from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import uuid

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us.ingest import _cleanup_package_outputs
from app.us.model import USCaseBundle, USCaseRecord, USOwnerRecord
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher
from app.us_assignment.api import _assignments_for_serial, _bundle_for_record, _latest_record
from app.us_assignment.audit_real_data import build_audit
from app.us_assignment.ingest import cleanup_assignment_package_outputs, ingest_assignment_package
from app.us_assignment.migrations import ensure_assignment_schema
from app.us_assignment.readiness import build_readiness
from app.us_assignment.reconciliation import scan_reconciliation_page
from app.us_assignment.repository import register_assignment_source


SERIAL = "88991234"
REEL = "1234"
FRAME = "0056"
FILES = ("ci_us_assignment_1.xml", "ci_us_assignment_2.xml")
CASE_SOURCE_RANK = 17_900_000_000_000_000_077


def _xml(*, assignee: str, update_date: str, correspondent: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<trademark-assignments><assignment-information><assignment-entry>
<assignment><reel-no>{REEL}</reel-no><frame-no>{FRAME}</frame-no>
<date-recorded>20260801</date-recorded><last-update-date>{update_date}</last-update-date>
<page-count>7</page-count><conveyance-text>ASSIGNS THE ENTIRE INTEREST</conveyance-text>
<purge-indicator>N</purge-indicator><correspondent>
<person-or-organization-name>{correspondent}</person-or-organization-name>
<address-1>1 Counsel Plaza</address-1></correspondent></assignment>
<assignors><assignor><name>Alpha Brand LLC</name><country>US</country>
<execution-date>20260729</execution-date></assignor></assignors>
<assignees><assignee><name>{assignee}</name><country>US</country></assignee></assignees>
<properties><property><serial-number>{SERIAL}</serial-number>
<registration-number>7654321</registration-number></property></properties>
</assignment-entry></assignment-information></trademark-assignments>"""


def _delete_registry(package_ids: list[str]) -> None:
    if not package_ids:
        return
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for package_id in package_ids:
                cur.execute(
                    "DELETE FROM control.source_package WHERE package_id = %s",
                    (package_id,),
                )
        conn.commit()


def _remove_files(raw_root: Path) -> None:
    for directory in (
        raw_root / "incoming" / "us_assignment",
        raw_root / "archive" / "us_assignment",
    ):
        if not directory.exists():
            continue
        for name in FILES:
            path = directory / name
            if path.exists():
                path.unlink()
            for candidate in directory.glob(f"{Path(name).stem}_*{Path(name).suffix}"):
                candidate.unlink()


def _publish_case_owner(package_id: uuid.UUID) -> None:
    publisher = SnapshotAwareUSBatchPublisher(
        clickhouse_client(),
        package_id=package_id,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 8, 4),
        source_rank=CASE_SOURCE_RANK,
        batch_size=100,
    )
    publisher.add(
        USCaseBundle(
            case=USCaseRecord(
                serial_number=SERIAL,
                registration_number="7654321",
                filing_date=date(2025, 1, 2),
                status_code="700",
                status_date=date(2026, 8, 4),
                mark_identification="ASSIGNMENT CROSS LAYER FIXTURE",
            ),
            owners=(
                USOwnerRecord(
                    serial_number=SERIAL,
                    entry_number=1,
                    party_type="10",
                    legal_entity_type_code="03",
                    party_name="Gamma Brand Corp.",
                    country="US",
                ),
            ),
        ),
        "assignment-cross-layer-case.xml",
    )
    publisher.close()


def main() -> None:
    ensure_assignment_schema()
    raw_root = get_settings().raw_data_root
    incoming = raw_root / "incoming" / "us_assignment"
    incoming.mkdir(parents=True, exist_ok=True)
    package_ids: list[str] = []
    case_package_id = uuid.uuid4()

    try:
        _remove_files(raw_root)
        first = incoming / FILES[0]
        first.write_text(
            _xml(
                assignee="Beta Brand Inc.",
                update_date="20260802",
                correspondent="Counsel One LLP",
            ),
            encoding="utf-8",
        )
        first_id, _ = register_assignment_source(
            first,
            effective_date=date(2026, 8, 2),
            source_kind="ASSIGNMENT_SNAPSHOT_XML",
        )
        package_ids.append(first_id)
        first_totals = ingest_assignment_package(first_id, first, raw_root)

        second = incoming / FILES[1]
        second.write_text(
            _xml(
                assignee="Gamma Brand Corp.",
                update_date="20260803",
                correspondent="Counsel Two LLP",
            ),
            encoding="utf-8",
        )
        second_id, _ = register_assignment_source(
            second,
            effective_date=date(2026, 8, 3),
            source_kind="DAILY_ASSIGNMENT_XML",
        )
        package_ids.append(second_id)
        second_totals = ingest_assignment_package(second_id, second, raw_root)

        _publish_case_owner(case_package_id)

        counts = {}
        for table in (
            "us_assignment_record_history",
            "us_assignment_assignor_history",
            "us_assignment_assignee_history",
            "us_assignment_property_history",
        ):
            counts[table] = int(
                clickhouse_client().query(
                    f"SELECT count() FROM markorbit_facts.{table} "
                    f"WHERE source_package_id IN (toUUID('{first_id}'), toUUID('{second_id}'))"
                ).result_rows[0][0]
            )
        expected = {
            "us_assignment_record_history": 2,
            "us_assignment_assignor_history": 2,
            "us_assignment_assignee_history": 2,
            "us_assignment_property_history": 2,
        }
        if counts != expected:
            raise RuntimeError(f"Assignment append-only counts mismatch: {counts}")

        latest = _latest_record(REEL, FRAME)
        if latest is None or str(latest["source_package_id"]) != second_id:
            raise RuntimeError(f"Latest reel/frame observation mismatch: {latest}")
        bundle = _bundle_for_record(latest)
        assignees = [str(row["party_name"]) for row in bundle["assignees"]]
        if assignees != ["Gamma Brand Corp."]:
            raise RuntimeError(f"Latest assignee projection mismatch: {assignees}")
        serial_records = _assignments_for_serial(SERIAL, 100)
        if len(serial_records) != 1 or str(serial_records[0]["source_package_id"]) != second_id:
            raise RuntimeError(f"Serial current-assignment projection mismatch: {serial_records}")

        acceptance = build_audit(raw_root=raw_root, verify_sources=True)
        if acceptance["status"] != "PASS":
            raise RuntimeError(f"Source-backed Assignment acceptance failed: {acceptance}")
        if acceptance["projection"]["property_serial_joined_to_case_count"] != 1:
            raise RuntimeError(f"Assignment-to-case join coverage mismatch: {acceptance}")
        readiness = build_readiness(raw_root=raw_root, verify_sources=True)
        if readiness["state"] != "ACCEPTED" or readiness["ready"] is not True:
            raise RuntimeError(f"Assignment readiness not accepted: {readiness}")

        reconciliation = scan_reconciliation_page(after_serial="88991233", limit=10)
        matching = [
            item
            for item in reconciliation["items"]
            if item["serial_number"] == SERIAL
        ]
        if len(matching) != 1 or matching[0]["classification"] != "NAME_SET_MATCH":
            raise RuntimeError(f"Assignment/case owner reconciliation mismatch: {reconciliation}")
        if matching[0]["legal_ownership_conclusion"] is not False:
            raise RuntimeError("Reconciliation must not claim legal ownership")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_ASSIGNMENT_M1.0_RUNTIME_FIXTURE",
                    "first_totals": first_totals,
                    "second_totals": second_totals,
                    "append_only_counts": counts,
                    "latest_assignee": assignees[0],
                    "serial_current_record_count": len(serial_records),
                    "source_backed_acceptance": acceptance["status"],
                    "readiness": readiness["state"],
                    "reconciliation": matching[0]["classification"],
                    "legal_ownership_conclusion": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        _cleanup_package_outputs(case_package_id)
        for package_id in reversed(package_ids):
            cleanup_assignment_package_outputs(uuid.UUID(package_id))
        _delete_registry(package_ids)
        _remove_files(raw_root)
        residual = int(
            clickhouse_client().query(
                "SELECT count() FROM markorbit_facts.us_assignment_record_history "
                f"WHERE reel_frame_id = '{REEL}/{FRAME}'"
            ).result_rows[0][0]
        )
        if residual:
            raise RuntimeError(f"Assignment fixture cleanup failed: residual={residual}")


if __name__ == "__main__":
    main()
