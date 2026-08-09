from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import uuid

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us.ingest import _cleanup_package_outputs
from app.us.migrations import ensure_us_m1_schema
from app.us.model import USCaseBundle, USCaseRecord
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher
from app.us_ttab.audit_real_data import build_audit
from app.us_ttab.ingest import cleanup_ttab_package_outputs, ingest_ttab_package
from app.us_ttab.migrations import ensure_ttab_schema
from app.us_ttab.read_model import proceeding_snapshot, proceedings_for_serial
from app.us_ttab.readiness import build_readiness
from app.us_ttab.repository import register_ttab_source
from app.us_ttab.timeline import build_ttab_timeline


PROCEEDING = "92081234"
SERIAL = "88997766"
FILES = ("ci_ttab_1.xml", "ci_ttab_2.xml")


def _xml(
    *,
    status_code: str,
    status: str,
    status_date: str,
    due_date: str,
    include_final_entry: bool,
) -> str:
    final = (
        """
    <prosecution-history-entry><entry-number>3</entry-number><filing-date>02/11/2026</filing-date>
    <history-text>SUBMITTED FOR FINAL DECISION</history-text></prosecution-history-entry>
    """
        if include_final_entry
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ttabvue-results><proceeding>
<proceeding-type name="Cancellation">CAN</proceeding-type><proceeding-number>{PROCEEDING}</proceeding-number>
<filing-date>07/25/2024</filing-date><proceeding-status name="{status}">{status_code}</proceeding-status>
<status-date>{status_date}</status-date><general-contact-number>571-272-8500</general-contact-number>
<interlocutory-attorney>TEST ATTORNEY</interlocutory-attorney><paralegal-name>TEST PARALEGAL</paralegal-name>
<defendant><name>Gamma Brand Corp.</name><correspondent><name>Defense Counsel</name>
<address-1>1 Counsel Plaza</address-1><email>defense@example.test</email></correspondent>
<property><serial-number>{SERIAL}</serial-number><registration-number>7654321</registration-number>
<mark-text>ORBIT TEST</mark-text><application-status name="Cancellation Pending">604</application-status></property></defendant>
<plaintiff><name>Beta Holdings Inc.</name><correspondent><name>Plaintiff Counsel</name></correspondent></plaintiff>
{final}
<prosecution-history-entry><entry-number>2</entry-number><filing-date>07/30/2024</filing-date>
<history-text>NOTICE AND TRIAL DATES SENT; ANSWER DUE:</history-text><due-date>{due_date}</due-date></prosecution-history-entry>
<prosecution-history-entry><entry-number>1</entry-number><filing-date>07/25/2024</filing-date>
<history-text>FILED AND FEE</history-text></prosecution-history-entry>
</proceeding></ttabvue-results>"""


def _delete_registry(package_ids: list[str]) -> None:
    if not package_ids:
        return
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for package_id in package_ids:
                cur.execute("DELETE FROM control.source_package WHERE package_id = %s", (package_id,))
        conn.commit()


def _remove_files(raw_root: Path) -> None:
    for directory in (
        raw_root / "incoming" / "us_ttab",
        raw_root / "archive" / "us_ttab",
    ):
        if not directory.exists():
            continue
        for name in FILES:
            path = directory / name
            if path.exists():
                path.unlink()
            for candidate in directory.glob(f"{Path(name).stem}_*{Path(name).suffix}"):
                candidate.unlink()


def _scalar(sql: str) -> int:
    rows = clickhouse_client().query(sql).result_rows
    return int(rows[0][0]) if rows else 0


def main() -> None:
    ensure_us_m1_schema()
    ensure_ttab_schema()
    raw_root = get_settings().raw_data_root
    incoming = raw_root / "incoming" / "us_ttab"
    incoming.mkdir(parents=True, exist_ok=True)
    ttab_package_ids: list[str] = []
    us_package_id = uuid.uuid4()

    try:
        _remove_files(raw_root)
        us_publisher = SnapshotAwareUSBatchPublisher(
            clickhouse_client(),
            package_id=us_package_id,
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 2, 11),
            source_rank=4_500_000_000_000_000_001,
            batch_size=10,
        )
        us_publisher.add(
            USCaseBundle(
                case=USCaseRecord(
                    serial_number=SERIAL,
                    registration_number="7654321",
                    status_code="700",
                    status_date=date(2026, 2, 11),
                    mark_identification="ORBIT TEST",
                    cancellation_pending=True,
                )
            ),
            "ttab-cross-link-case.xml",
        )
        us_publisher.close()

        first = incoming / FILES[0]
        first.write_text(
            _xml(
                status_code="9",
                status="Pending",
                status_date="07/30/2024",
                due_date="09/08/2024",
                include_final_entry=False,
            ),
            encoding="utf-8",
        )
        first_id, _ = register_ttab_source(
            first,
            snapshot_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        ttab_package_ids.append(first_id)
        first_totals = ingest_ttab_package(first_id, first, raw_root)

        second = incoming / FILES[1]
        second.write_text(
            _xml(
                status_code="10",
                status="Ready for Final Decision",
                status_date="02/11/2026",
                due_date="09/09/2024",
                include_final_entry=True,
            ),
            encoding="utf-8",
        )
        second_id, _ = register_ttab_source(
            second,
            snapshot_at=datetime(2026, 2, 11, 18, 0, tzinfo=timezone.utc),
        )
        ttab_package_ids.append(second_id)
        second_totals = ingest_ttab_package(second_id, second, raw_root)

        counts = {
            table: _scalar(
                f"SELECT count() FROM markorbit_facts.{table} "
                f"WHERE proceeding_number = '{PROCEEDING}'"
            )
            for table in (
                "us_ttab_proceeding_history",
                "us_ttab_party_history",
                "us_ttab_property_history",
                "us_ttab_docket_history",
            )
        }
        expected = {
            "us_ttab_proceeding_history": 2,
            "us_ttab_party_history": 4,
            "us_ttab_property_history": 2,
            "us_ttab_docket_history": 5,
        }
        if counts != expected:
            raise RuntimeError(f"TTAB append-only counts mismatch: {counts}")

        snapshot = proceeding_snapshot(PROCEEDING)
        if snapshot is None:
            raise RuntimeError("Latest TTAB snapshot is missing")
        latest = snapshot["proceeding"]
        if (
            latest["proceeding_type_code"] != "CAN"
            or latest["proceeding_type"] != "Cancellation"
            or latest["status_code"] != "10"
            or latest["status_text"] != "Ready for Final Decision"
        ):
            raise RuntimeError(f"Latest TTAB M1.1 code/display snapshot mismatch: {latest}")
        if len(snapshot["due_date_observations"]) != 1:
            raise RuntimeError(f"TTAB due-date observation mismatch: {snapshot}")
        linked = proceedings_for_serial(SERIAL)
        if len(linked) != 1 or linked[0]["proceeding_number"] != PROCEEDING:
            raise RuntimeError(f"TTAB serial cross-link mismatch: {linked}")

        timeline = build_ttab_timeline(PROCEEDING)
        if timeline["observation_count"] != 2 or len(timeline["changes"]) != 1:
            raise RuntimeError(f"TTAB timeline mismatch: {timeline}")
        change_types = {
            item["change_type"] for item in timeline["changes"][0]["changes"]
        }
        required = {
            "STATUS_CODE_CHANGED",
            "STATUS_TEXT_CHANGED",
            "STATUS_DATE_CHANGED",
            "DOCKET_ENTRY_ADDED",
            "DOCKET_DUE_DATE_OBSERVATION_CHANGED",
        }
        if not required.issubset(change_types):
            raise RuntimeError(f"TTAB timeline missing changes: {change_types}")

        audit = build_audit(raw_root=raw_root, verify_sources=True)
        if audit["status"] != "PASS":
            raise RuntimeError(f"TTAB source-backed acceptance failed: {audit}")
        readiness = build_readiness(raw_root=raw_root, verify_sources=True)
        if readiness["state"] != "ACCEPTED" or not readiness["ready"]:
            raise RuntimeError(f"TTAB readiness mismatch: {readiness}")
        if audit["projection"]["property_serial_joined_to_us_case_count"] != 1:
            raise RuntimeError(f"TTAB-US case coverage mismatch: {audit['projection']}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_TTAB_M1.1_RUNTIME_FIXTURE",
                    "first_totals": first_totals,
                    "second_totals": second_totals,
                    "append_only_counts": counts,
                    "proceeding_type_code": latest["proceeding_type_code"],
                    "proceeding_type": latest["proceeding_type"],
                    "latest_status_code": latest["status_code"],
                    "latest_status": latest["status_text"],
                    "timeline_change_types": sorted(change_types),
                    "source_backed_acceptance": audit["status"],
                    "readiness": readiness["state"],
                    "serial_cross_link_count": len(linked),
                    "deadline_validity_inference": False,
                    "legal_outcome_conclusion": False,
                    "substantive_rights_conclusion": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        for package_id in reversed(ttab_package_ids):
            cleanup_ttab_package_outputs(uuid.UUID(package_id))
        _delete_registry(ttab_package_ids)
        _cleanup_package_outputs(us_package_id)
        _remove_files(raw_root)
        residual = _scalar(
            "SELECT count() FROM markorbit_facts.us_ttab_proceeding_history "
            f"WHERE proceeding_number = '{PROCEEDING}'"
        )
        if residual:
            raise RuntimeError(f"TTAB fixture cleanup failed: residual={residual}")


if __name__ == "__main__":
    main()
