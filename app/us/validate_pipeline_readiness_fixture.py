from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import uuid
import zipfile

from app.config import get_settings
from app.db import postgres_conn
from app.domain import DiscoveredPackage
from app.scanner import sha256_file
from app.us.ingest import _cleanup_package_outputs
from app.us.migrations import US_SCHEMA_VERSION, ensure_us_m1_schema
from app.us.model import (
    USCaseBundle,
    USCaseRecord,
    USClassificationRecord,
    USCorrespondentRecord,
    USDesignSearchRecord,
    USEventRecord,
    USForeignApplicationRecord,
    USMadridFilingRecord,
    USMadridHistoryEventRecord,
    USOwnerRecord,
    USPriorRegistrationRecord,
    USStatementRecord,
)
from app.us.pipeline_readiness import build_readiness
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher
from app.us.repository import register_us_package
from app.us.source_preflight import build_preflight
from app.db import clickhouse_client


SERIAL = "88990008"
HISTORY_NAME = "apc18840407-20260228-01.zip"
DAILY_NAME = "apc260302.zip"


def _source_xml(marker: str) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<trademark-case-files><case-file>"
        f"<serial-number>{marker}</serial-number>"
        "</case-file></trademark-case-files>"
    )


def _write_zip(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path.with_suffix(".xml").name, _source_xml(marker))


def _discovered(path: Path) -> DiscoveredPackage:
    stat = path.stat()
    return DiscoveredPackage(
        jurisdiction="US",
        path=path,
        file_name=path.name,
        file_size=stat.st_size,
        sha256=sha256_file(path),
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )


def _bundle(*, status_code: str, status_date: date, owner_name: str) -> USCaseBundle:
    return USCaseBundle(
        case=USCaseRecord(
            serial_number=SERIAL,
            registration_number="7655001",
            transaction_date=status_date,
            filing_date=date(2020, 1, 17),
            status_code=status_code,
            status_date=status_date,
            mark_identification="MARKORBIT READINESS FIXTURE",
            mark_drawing_code="4000",
        ),
        owners=(
            USOwnerRecord(
                serial_number=SERIAL,
                entry_number=1,
                party_type="10",
                legal_entity_type_code="16",
                party_name=owner_name,
                nationality_country="US",
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
                event_code=f"RD{status_code}",
                event_date=status_date,
                event_sequence=1,
                event_type_code="A",
                description_text=f"READINESS EVENT {status_code}",
            ),
        ),
        statements=(
            USStatementRecord(
                serial_number=SERIAL,
                type_code="GS0091",
                text="Readiness fixture software.",
            ),
        ),
        correspondent=USCorrespondentRecord(
            serial_number=SERIAL,
            address_1="Readiness Counsel LLP",
            attorney_name="Readiness Attorney",
            attorney_docket_number="READY-1",
        ),
        design_searches=(
            USDesignSearchRecord(serial_number=SERIAL, code="010725"),
        ),
        prior_registrations=(
            USPriorRegistrationRecord(
                serial_number=SERIAL,
                relationship_type="0",
                number="520351",
            ),
        ),
        foreign_applications=(
            USForeignApplicationRecord(
                serial_number=SERIAL,
                entry_number=1,
                application_number="GB-READY-1",
                country="GB",
                filing_date=date(2020, 1, 2),
                foreign_priority_claimed=True,
            ),
        ),
        madrid_filings=(
            USMadridFilingRecord(
                serial_number=SERIAL,
                entry_number=1,
                reference_number="A-READY-1",
                original_filing_date_uspto=date(2020, 1, 17),
                international_registration_number="1271417",
                international_registration_date=date(2020, 5, 8),
                international_status_code="400",
                international_status_date=status_date,
            ),
        ),
        madrid_events=(
            USMadridHistoryEventRecord(
                serial_number=SERIAL,
                filing_entry_number=1,
                filing_reference_number="A-READY-1",
                event_entry_number=1 if status_code == "630" else 2,
                code="NEWAP" if status_code == "630" else "READY",
                event_date=status_date,
                description_text=f"READINESS MADRID EVENT {status_code}",
            ),
        ),
    )


def _mark_success(package_id: str, source_sha: str) -> None:
    profile = {
        "source_sha256": source_sha.lower(),
        "totals": {
            "schema_version": US_SCHEMA_VERSION,
            "row_counts": {},
            "snapshot_tombstone_counts": {},
        },
    }
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE control.source_package
                SET status = 'SUCCESS', profile = %s::jsonb, processed_at = now(),
                    archived_path = NULL, error_message = NULL
                WHERE package_id = %s AND jurisdiction = 'US'
                """,
                (json.dumps(profile), package_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Readiness fixture could not mark package SUCCESS: {package_id}")
        conn.commit()


def _source_rank(package_id: str) -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_rank FROM control.source_package WHERE package_id = %s",
                (package_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"Readiness fixture package missing: {package_id}")
            return int(row["source_rank"])


def _publish(
    package_id: str,
    *,
    package_kind: str,
    source_effective_date: date,
    source_file: str,
    bundle: USCaseBundle,
) -> None:
    publisher = SnapshotAwareUSBatchPublisher(
        clickhouse_client(),
        package_id=uuid.UUID(package_id),
        package_kind=package_kind,
        source_effective_date=source_effective_date,
        source_rank=_source_rank(package_id),
        batch_size=100,
    )
    publisher.add(bundle, source_file)
    publisher.close()


def _delete_registry(package_ids: list[str]) -> None:
    if not package_ids:
        return
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM control.source_package WHERE package_id = ANY(%s::uuid[])",
                (package_ids,),
            )
        conn.commit()


def _remove_sources(raw_root: Path) -> None:
    for directory in (raw_root / "incoming" / "us", raw_root / "archive" / "us"):
        if not directory.exists():
            continue
        for name in (HISTORY_NAME, DAILY_NAME):
            path = directory / name
            if path.exists():
                path.unlink()


def main() -> None:
    ensure_us_m1_schema()
    raw_root = get_settings().raw_data_root
    incoming = raw_root / "incoming" / "us"
    incoming.mkdir(parents=True, exist_ok=True)
    package_ids: list[str] = []

    try:
        _remove_sources(raw_root)
        _write_zip(incoming / HISTORY_NAME, "history")
        _write_zip(incoming / DAILY_NAME, "daily")

        initial = build_readiness(
            raw_root,
            expected_history_parts=1,
            verify_source_files=False,
        )
        if initial["state"] != "REPLAY_READY":
            raise RuntimeError(f"Readiness fixture initial state mismatch: {initial}")

        preflight = build_preflight(raw_root, expected_history_parts=1)
        sources = {row["file_name"]: row for row in preflight["replay_plan"]}
        history_discovered = _discovered(incoming / HISTORY_NAME)
        daily_discovered = _discovered(incoming / DAILY_NAME)
        history_id, _ = register_us_package(history_discovered)
        daily_id, _ = register_us_package(daily_discovered)
        package_ids.extend([history_id, daily_id])

        _publish(
            history_id,
            package_kind="HISTORICAL_APPLICATIONS",
            source_effective_date=date(2026, 2, 28),
            source_file=HISTORY_NAME.replace(".zip", ".xml"),
            bundle=_bundle(
                status_code="630",
                status_date=date(2026, 2, 28),
                owner_name="Readiness Historical LLC",
            ),
        )
        _mark_success(history_id, sources[HISTORY_NAME]["sha256"])

        _publish(
            daily_id,
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 3, 2),
            source_file=DAILY_NAME.replace(".zip", ".xml"),
            bundle=_bundle(
                status_code="700",
                status_date=date(2026, 3, 2),
                owner_name="Readiness Daily LLC",
            ),
        )
        _mark_success(daily_id, sources[DAILY_NAME]["sha256"])

        database_only = build_readiness(
            raw_root,
            expected_history_parts=1,
            verify_source_files=False,
        )
        if database_only["state"] != "SOURCE_VERIFICATION_REQUIRED":
            raise RuntimeError(
                f"Readiness fixture database-only state mismatch: {database_only}"
            )
        if database_only["reports"]["acceptance"]["status"] != "PASS_WITH_WARNINGS":
            raise RuntimeError("Readiness fixture database-only acceptance did not warn")

        accepted = build_readiness(
            raw_root,
            expected_history_parts=1,
            verify_source_files=True,
        )
        if accepted["state"] != "ACCEPTED" or accepted["ready"] is not True:
            raise RuntimeError(f"Readiness fixture accepted state mismatch: {accepted}")
        if accepted["reports"]["acceptance"]["status"] != "PASS":
            raise RuntimeError("Readiness fixture source-backed acceptance did not PASS")
        if accepted["next_action"]["code"] != "NONE":
            raise RuntimeError("Readiness fixture ACCEPTED state still requested an action")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_PIPELINE_READINESS_FIXTURE",
                    "initial_state": initial["state"],
                    "database_only_state": database_only["state"],
                    "source_backed_state": accepted["state"],
                    "final_acceptance": accepted["reports"]["acceptance"]["status"],
                    "terminal_next_action": accepted["next_action"]["code"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        for package_id in package_ids:
            _cleanup_package_outputs(uuid.UUID(package_id))
        _delete_registry(package_ids)
        _remove_sources(raw_root)


if __name__ == "__main__":
    main()
