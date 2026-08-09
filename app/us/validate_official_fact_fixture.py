from __future__ import annotations

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
    USCorrespondentRecord,
    USDesignSearchRecord,
    USForeignApplicationRecord,
    USMadridFilingRecord,
    USMadridHistoryEventRecord,
    USPriorRegistrationRecord,
)
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher


SERIAL = "88990004"
SOURCE_RANK = 18_100_000_000_000_000_000


def _bundle() -> USCaseBundle:
    return USCaseBundle(
        case=USCaseRecord(
            serial_number=SERIAL,
            transaction_date=date(2026, 1, 8),
            filing_date=date(2020, 2, 6),
            status_code="630",
            status_date=date(2026, 1, 8),
            mark_identification="MARKORBIT OFFICIAL FACT FIXTURE",
        ),
        correspondent=USCorrespondentRecord(
            serial_number=SERIAL,
            address_1="Peter S. Sloane Leason Ellis LLP",
            address_2="One North Lexington Ave., Suite 1200",
            address_3="White Plains, NY 10601",
            address_4="United States",
            attorney_name="Jane Q. Attorney",
            attorney_docket_number="MO-88990004",
            domestic_representative_name="Domestic Representative LLC",
        ),
        design_searches=(USDesignSearchRecord(serial_number=SERIAL, code="010725"),),
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
                application_number="UK0000346470",
                country="GB",
                filing_date=date(2020, 2, 6),
                foreign_priority_claimed=True,
            ),
        ),
        madrid_filings=(
            USMadridFilingRecord(
                serial_number=SERIAL,
                entry_number=53,
                reference_number="A0048809",
                original_filing_date_uspto=date(2015, 3, 4),
                international_registration_number="1271416",
                international_registration_date=date(2015, 5, 7),
                international_status_code="408",
                international_status_date=date(2026, 1, 7),
                international_renewal_date=date(2035, 5, 7),
            ),
        ),
        madrid_events=(
            USMadridHistoryEventRecord(
                serial_number=SERIAL,
                filing_entry_number=53,
                filing_reference_number="A0048809",
                event_entry_number=1,
                code="NEWAP",
                event_date=date(2015, 3, 4),
                description_text="NEW APPLICATION FOR IR RECEIVED",
            ),
        ),
    )


def _rows(sql: str) -> list[tuple[object, ...]]:
    return clickhouse_client().query(sql).result_rows


def _count(table: str) -> int:
    rows = _rows(
        f"SELECT count() FROM markorbit_facts.{table} FINAL WHERE serial_number = '{SERIAL}'"
    )
    return int(rows[0][0]) if rows else 0


def main() -> None:
    started = time.perf_counter()
    ensure_us_m1_schema()
    package_id = uuid.uuid4()
    publisher = SnapshotAwareUSBatchPublisher(
        clickhouse_client(),
        package_id=package_id,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_rank=SOURCE_RANK,
        batch_size=100,
    )

    try:
        publisher.add(_bundle(), "apc260108.xml")
        publish_counts = publisher.close()

        tables = (
            "us_correspondent_current",
            "us_design_search_current",
            "us_prior_registration_current",
            "us_foreign_application_current",
            "us_madrid_filing_current",
            "us_madrid_event_history",
        )
        table_counts = {table: _count(table) for table in tables}
        expected_counts = {table: 1 for table in tables}
        if table_counts != expected_counts:
            raise RuntimeError(
                f"US M1.3 official fact row counts failed: {table_counts} != {expected_counts}"
            )

        correspondent = _rows(
            "SELECT attorney_name, attorney_docket_number, domestic_representative_name "
            "FROM markorbit_facts.us_correspondent_current FINAL "
            f"WHERE serial_number = '{SERIAL}' AND is_deleted = 0"
        )
        foreign = _rows(
            "SELECT entry_number, application_number, country, filing_date, foreign_priority_claimed "
            "FROM markorbit_facts.us_foreign_application_current FINAL "
            f"WHERE serial_number = '{SERIAL}' AND is_deleted = 0"
        )
        madrid = _rows(
            "SELECT entry_number, reference_number, international_registration_number, "
            "international_status_code FROM markorbit_facts.us_madrid_filing_current FINAL "
            f"WHERE serial_number = '{SERIAL}' AND is_deleted = 0"
        )
        madrid_event = _rows(
            "SELECT filing_entry_number, event_entry_number, code, description_text "
            "FROM markorbit_facts.us_madrid_event_history FINAL "
            f"WHERE serial_number = '{SERIAL}'"
        )

        checks = {
            "correspondent": "PASS"
            if correspondent
            and correspondent[0]
            == ("Jane Q. Attorney", "MO-88990004", "Domestic Representative LLC")
            else "FAIL",
            "foreign_application": "PASS"
            if foreign
            and int(foreign[0][0]) == 1
            and foreign[0][1] == "UK0000346470"
            and foreign[0][2] == "GB"
            and int(foreign[0][4]) == 1
            else "FAIL",
            "madrid_filing_request": "PASS"
            if madrid and madrid[0] == (53, "A0048809", "1271416", "408")
            else "FAIL",
            "madrid_event_history": "PASS"
            if madrid_event
            and madrid_event[0]
            == (53, 1, "NEWAP", "NEW APPLICATION FOR IR RECEIVED")
            else "FAIL",
        }
        if any(value != "PASS" for value in checks.values()):
            raise RuntimeError(f"US M1.3 official fact checks failed: {checks}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_M1.3_OFFICIAL_FACT_FAMILIES_FIXTURE",
                    "package_id": str(package_id),
                    "publish_counts": publish_counts,
                    "table_counts": table_counts,
                    "checks": checks,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        _cleanup_package_outputs(package_id)
        residual = sum(
            _count(table)
            for table in (
                "us_case_current",
                "us_correspondent_current",
                "us_design_search_current",
                "us_prior_registration_current",
                "us_foreign_application_current",
                "us_madrid_filing_current",
                "us_madrid_event_history",
            )
        )
        if residual:
            raise RuntimeError(
                f"US M1.3 official fact fixture cleanup failed: residual_rows={residual}"
            )


if __name__ == "__main__":
    main()
