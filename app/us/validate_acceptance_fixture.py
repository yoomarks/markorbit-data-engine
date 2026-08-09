from __future__ import annotations

from datetime import date
import json
import uuid

from app.db import clickhouse_client, postgres_conn
from app.us.audit_real_data_v2 import build_audit
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
from app.us.package_meta import DAILY_RANK_MAJOR
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher


SERIAL = "88990005"
HISTORY_PACKAGE_ID = uuid.UUID("77777777-7777-7777-7777-777777777771")
DAILY_PACKAGE_ID = uuid.UUID("77777777-7777-7777-7777-777777777772")
HISTORY_RANK = 1_020_251_231_001_771
DAILY_RANK = DAILY_RANK_MAJOR + 20_260_108 * 1_000_000 + 772


def _bundle(*, status_code: str, status_date: date) -> USCaseBundle:
    return USCaseBundle(
        case=USCaseRecord(
            serial_number=SERIAL,
            registration_number="7654321",
            transaction_date=status_date,
            filing_date=date(2020, 2, 6),
            status_code=status_code,
            status_date=status_date,
            mark_identification="MARKORBIT ACCEPTANCE FIXTURE",
        ),
        owners=(
            USOwnerRecord(
                serial_number=SERIAL,
                entry_number=1,
                party_type="10",
                legal_entity_type_code="16",
                party_name="Acceptance Fixture LLC",
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
                event_code="NWAP",
                event_date=date(2020, 2, 6),
                event_sequence=1,
                event_type_code="A",
                description_text="NEW APPLICATION ENTERED",
            ),
        ),
        statements=(
            USStatementRecord(
                serial_number=SERIAL,
                type_code="GS0091",
                text="Acceptance fixture goods.",
            ),
        ),
        correspondent=USCorrespondentRecord(
            serial_number=SERIAL,
            address_1="Acceptance Counsel LLP",
            attorney_name="Acceptance Attorney",
            attorney_docket_number="ACCEPT-1",
        ),
        design_searches=(
            USDesignSearchRecord(serial_number=SERIAL, code="010725"),
        ),
        prior_registrations=(
            USPriorRegistrationRecord(
                serial_number=SERIAL,
                relationship_type="0",
                number="520350",
            ),
        ),
        foreign_applications=(
            USForeignApplicationRecord(
                serial_number=SERIAL,
                entry_number=1,
                application_number="GB-ACCEPT-1",
                country="GB",
                filing_date=date(2020, 1, 1),
                foreign_priority_claimed=True,
            ),
        ),
        madrid_filings=(
            USMadridFilingRecord(
                serial_number=SERIAL,
                entry_number=1,
                reference_number="A-ACCEPT-1",
                original_filing_date_uspto=date(2020, 2, 6),
                international_registration_number="1271416",
                international_registration_date=date(2020, 5, 7),
                international_status_code="400",
                international_status_date=status_date,
            ),
        ),
        madrid_events=(
            USMadridHistoryEventRecord(
                serial_number=SERIAL,
                filing_entry_number=1,
                filing_reference_number="A-ACCEPT-1",
                event_entry_number=1,
                code="NEWAP",
                event_date=date(2020, 2, 6),
                description_text="NEW APPLICATION FOR IR RECEIVED",
            ),
        ),
    )


def _profile(package_kind: str) -> str:
    return json.dumps(
        {
            "totals": {
                "schema_version": US_SCHEMA_VERSION,
                "package_kind": package_kind,
                "row_counts": {
                    "markorbit_facts.us_case_current": 1,
                    "markorbit_facts.us_owner_current": 1,
                    "markorbit_facts.us_classification_current": 1,
                    "markorbit_facts.us_event_history": 1,
                    "markorbit_facts.us_statement_current": 1,
                    "markorbit_facts.us_correspondent_current": 1,
                    "markorbit_facts.us_design_search_current": 1,
                    "markorbit_facts.us_prior_registration_current": 1,
                    "markorbit_facts.us_foreign_application_current": 1,
                    "markorbit_facts.us_madrid_filing_current": 1,
                    "markorbit_facts.us_madrid_event_history": 1,
                },
                "snapshot_tombstone_counts": {},
            }
        }
    )


def _register_packages() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM control.source_package WHERE package_id IN (%s, %s)",
                (HISTORY_PACKAGE_ID, DAILY_PACKAGE_ID),
            )
            cur.execute(
                """
                INSERT INTO control.source_package (
                    package_id, jurisdiction, file_name, file_path, file_size, sha256,
                    package_kind, partition_dimension, partition_value,
                    source_period_start, source_period_end, source_sequence, source_rank,
                    status, profile, schema_version, archived_path, processed_at
                )
                VALUES
                (%s, 'US', 'apc18840407-20251231-01.zip', '/fixture/history.zip', 1, %s,
                 'HISTORICAL_APPLICATIONS', 'COVERAGE_RANGE_PART',
                 '1884-04-07/2025-12-31#001', '1884-04-07', '2025-12-31',
                 20251231001, %s, 'SUCCESS', %s::jsonb, %s, '/fixture/history.zip', now()),
                (%s, 'US', 'apc260108.zip', '/fixture/daily.zip', 1, %s,
                 'DAILY_APPLICATIONS', 'UPDATE_DATE', '2026-01-08',
                 '2026-01-08', '2026-01-08', 20260108, %s,
                 'SUCCESS', %s::jsonb, %s, '/fixture/daily.zip', now())
                """,
                (
                    HISTORY_PACKAGE_ID,
                    "7" * 64,
                    HISTORY_RANK,
                    _profile("HISTORICAL_APPLICATIONS"),
                    US_SCHEMA_VERSION,
                    DAILY_PACKAGE_ID,
                    "8" * 64,
                    DAILY_RANK,
                    _profile("DAILY_APPLICATIONS"),
                    US_SCHEMA_VERSION,
                ),
            )
        conn.commit()


def _delete_packages() -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM control.source_package WHERE package_id IN (%s, %s)",
                (HISTORY_PACKAGE_ID, DAILY_PACKAGE_ID),
            )
        conn.commit()


def _publish(
    *,
    package_id: uuid.UUID,
    package_kind: str,
    source_effective_date: date,
    source_rank: int,
    source_file: str,
    bundle: USCaseBundle,
) -> None:
    publisher = SnapshotAwareUSBatchPublisher(
        clickhouse_client(),
        package_id=package_id,
        package_kind=package_kind,
        source_effective_date=source_effective_date,
        source_rank=source_rank,
        batch_size=100,
    )
    publisher.add(bundle, source_file)
    publisher.close()


def main() -> None:
    ensure_us_m1_schema()
    try:
        _cleanup_package_outputs(DAILY_PACKAGE_ID)
        _cleanup_package_outputs(HISTORY_PACKAGE_ID)
        _register_packages()
        _publish(
            package_id=HISTORY_PACKAGE_ID,
            package_kind="HISTORICAL_APPLICATIONS",
            source_effective_date=date(2025, 12, 31),
            source_rank=HISTORY_RANK,
            source_file="history.xml",
            bundle=_bundle(status_code="630", status_date=date(2025, 12, 31)),
        )
        _publish(
            package_id=DAILY_PACKAGE_ID,
            package_kind="DAILY_APPLICATIONS",
            source_effective_date=date(2026, 1, 8),
            source_rank=DAILY_RANK,
            source_file="apc260108.xml",
            bundle=_bundle(status_code="700", status_date=date(2026, 1, 8)),
        )

        report = build_audit(
            verify_source_files=False,
            expected_history_parts=1,
        )
        if report["status"] != "PASS_WITH_WARNINGS":
            raise RuntimeError(
                f"US acceptance fixture expected PASS_WITH_WARNINGS, got {report['status']}: "
                f"hard={report['hard_fail_reasons']} ready={report['not_ready_reasons']}"
            )
        if report["packages"]["history_success_count"] != 1:
            raise RuntimeError("US acceptance fixture history package count mismatch")
        if report["packages"]["daily_success_count"] != 1:
            raise RuntimeError("US acceptance fixture daily package count mismatch")
        if report["coverage"]["rank_boundary_ok"] is not True:
            raise RuntimeError("US acceptance fixture source-rank boundary failed")
        if report["coverage"]["current_case_source_kind_counts"].get("DAILY_APPLICATIONS") != 1:
            raise RuntimeError("US acceptance fixture daily current-case lineage failed")
        completeness = report["historical_part_completeness"]
        if completeness["complete"] is not True:
            raise RuntimeError("US acceptance fixture historical part completeness failed")
        if completeness["observed_part_count"] if "observed_part_count" in completeness else False:
            raise RuntimeError("historical part summary unexpectedly flattened")
        if completeness["baseline_coverage"]["observed_parts"] != [1]:
            raise RuntimeError("US acceptance fixture historical part identity mismatch")
        if report["integrity"]["duplicates_after_final"]:
            raise RuntimeError("US acceptance fixture duplicate audit failed")
        if report["integrity"]["orphan_serials_by_table"]:
            raise RuntimeError("US acceptance fixture orphan audit failed")
        if report["integrity"]["source_lineage_rank_mismatches"]:
            raise RuntimeError("US acceptance fixture lineage-rank audit failed")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_M1.3_REAL_DATA_ACCEPTANCE_FIXTURE_V2",
                    "audit_status": report["status"],
                    "table_count": len(report["tables"]),
                    "coverage": report["coverage"],
                    "historical_part_completeness": completeness,
                    "warnings": report["warning_reasons"],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        _cleanup_package_outputs(DAILY_PACKAGE_ID)
        _cleanup_package_outputs(HISTORY_PACKAGE_ID)
        _delete_packages()
        residual = clickhouse_client().query(
            f"""
            SELECT count()
            FROM markorbit_facts.us_case_current FINAL
            WHERE serial_number = '{SERIAL}'
            """
        ).result_rows[0][0]
        if int(residual):
            raise RuntimeError(
                f"US acceptance fixture cleanup failed: residual_case_rows={residual}"
            )


if __name__ == "__main__":
    main()
