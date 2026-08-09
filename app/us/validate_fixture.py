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
    USClassificationRecord,
    USEventRecord,
    USOwnerRecord,
    USStatementRecord,
)
from app.us.publisher import USBatchPublisher


DIRECT_SERIAL = "88990001"
MADRID_SERIAL = "88990002"
SOURCE_RANK = 18_000_000_000_000_000_000


def _bundles() -> tuple[USCaseBundle, USCaseBundle]:
    direct = USCaseBundle(
        case=USCaseRecord(
            serial_number=DIRECT_SERIAL,
            registration_number="7990001",
            transaction_date=date(2026, 1, 6),
            filing_date=date(2025, 1, 2),
            registration_date=date(2026, 1, 6),
            status_code="700",
            status_date=date(2026, 1, 6),
            mark_identification="MARKORBIT US RUNTIME FIXTURE",
            mark_drawing_code="4000",
            standard_character_claimed=True,
            use_1a=True,
            use_1a_filed=True,
            use_1a_current=True,
            section_8_accepted=True,
        ),
        owners=(
            USOwnerRecord(
                serial_number=DIRECT_SERIAL,
                entry_number=1,
                party_type="10",
                legal_entity_type_code="16",
                party_name="Runtime Fixture LLC",
                nationality_country="US",
                nationality_state="DE",
                city="Wilmington",
                state="DE",
                country="US",
            ),
        ),
        classifications=(
            USClassificationRecord(
                serial_number=DIRECT_SERIAL,
                primary_code="009",
                international_codes=("009",),
                us_codes=("021", "023"),
                status_code="6",
                first_use_anywhere=date(2024, 1, 1),
                first_use_anywhere_raw="20240101",
                first_use_commerce=date(2024, 2, 1),
                first_use_commerce_raw="20240201",
            ),
        ),
        events=(
            USEventRecord(
                serial_number=DIRECT_SERIAL,
                event_code="NWAP",
                event_date=date(2025, 1, 2),
                event_sequence=1,
                event_type_code="A",
                description_text="NEW APPLICATION ENTERED",
            ),
        ),
        statements=(
            USStatementRecord(
                serial_number=DIRECT_SERIAL,
                type_code="GS0091",
                text="Runtime fixture downloadable trademark management software.",
            ),
        ),
    )
    madrid = USCaseBundle(
        case=USCaseRecord(
            serial_number=MADRID_SERIAL,
            transaction_date=date(2026, 2, 3),
            filing_date=date(2025, 3, 4),
            status_code="630",
            status_date=date(2026, 2, 3),
            mark_identification="MARKORBIT MADRID RUNTIME FIXTURE",
            mark_drawing_code="4000",
            madrid_66a=True,
            madrid_66a_filed=True,
            madrid_66a_current=True,
            international_registration_number="1990001",
            international_registration_date=date(2025, 3, 4),
            international_registration_status_code="001",
            international_registration_status_date=date(2026, 2, 3),
        ),
        owners=(
            USOwnerRecord(
                serial_number=MADRID_SERIAL,
                entry_number=1,
                party_type="10",
                legal_entity_type_code="99",
                party_name="Runtime Fixture AG",
                nationality_country="CH",
                city="Zurich",
                country="CH",
            ),
        ),
        classifications=(
            USClassificationRecord(
                serial_number=MADRID_SERIAL,
                primary_code="025",
                international_codes=("025",),
                status_code="6",
                first_use_anywhere_raw="20190600",
                first_use_commerce_raw="00000000",
            ),
        ),
        events=(
            USEventRecord(
                serial_number=MADRID_SERIAL,
                event_code="REPR",
                event_date=date(2025, 3, 4),
                event_sequence=1,
                event_type_code="M",
                description_text="SN ASSIGNED FOR SECT 66A APPL FROM IB",
            ),
        ),
        statements=(
            USStatementRecord(
                serial_number=MADRID_SERIAL,
                type_code="GS0251",
                text="Runtime fixture clothing.",
            ),
        ),
    )
    return direct, madrid


def _scalar(sql: str) -> int:
    rows = clickhouse_client().query(sql).result_rows
    return int(rows[0][0]) if rows else 0


def _assert_fixture(package_id: uuid.UUID) -> dict[str, object]:
    package = str(package_id)
    expected = {
        "us_case_current": 2,
        "us_owner_current": 2,
        "us_classification_current": 2,
        "us_event_history": 2,
        "us_statement_current": 2,
    }
    actual = {
        "us_case_current": _scalar(
            "SELECT count() FROM markorbit_facts.us_case_current FINAL "
            f"WHERE last_source_package_id = toUUID('{package}') AND is_deleted = 0"
        ),
        "us_owner_current": _scalar(
            "SELECT count() FROM markorbit_facts.us_owner_current FINAL "
            f"WHERE last_source_package_id = toUUID('{package}') AND is_deleted = 0"
        ),
        "us_classification_current": _scalar(
            "SELECT count() FROM markorbit_facts.us_classification_current FINAL "
            f"WHERE last_source_package_id = toUUID('{package}') AND is_deleted = 0"
        ),
        "us_event_history": _scalar(
            "SELECT count() FROM markorbit_facts.us_event_history FINAL "
            f"WHERE source_package_id = toUUID('{package}')"
        ),
        "us_statement_current": _scalar(
            "SELECT count() FROM markorbit_facts.us_statement_current FINAL "
            f"WHERE last_source_package_id = toUUID('{package}') AND is_deleted = 0"
        ),
    }
    if actual != expected:
        raise RuntimeError(f"US M1.1 runtime table-count contract failed: {actual}")

    direct_ok = _scalar(
        "SELECT count() FROM markorbit_facts.us_case_current FINAL "
        f"WHERE serial_number = '{DIRECT_SERIAL}' "
        f"AND last_source_package_id = toUUID('{package}') "
        "AND status_code = '700' AND use_1a = 1 "
        "AND use_1a_filed = 1 AND use_1a_current = 1 "
        "AND section_8_accepted = 1 AND is_deleted = 0"
    )
    madrid_ok = _scalar(
        "SELECT count() FROM markorbit_facts.us_case_current FINAL "
        f"WHERE serial_number = '{MADRID_SERIAL}' "
        f"AND last_source_package_id = toUUID('{package}') "
        "AND madrid_66a = 1 AND madrid_66a_filed = 1 AND madrid_66a_current = 1 "
        "AND international_registration_number = '1990001' "
        "AND international_registration_status_code = '001' AND is_deleted = 0"
    )
    event_description_ok = _scalar(
        "SELECT count() FROM markorbit_facts.us_event_history FINAL "
        f"WHERE serial_number = '{MADRID_SERIAL}' "
        f"AND source_package_id = toUUID('{package}') "
        "AND event_code = 'REPR' AND description_text != ''"
    )
    partial_date_ok = _scalar(
        "SELECT count() FROM markorbit_facts.us_classification_current FINAL "
        f"WHERE serial_number = '{MADRID_SERIAL}' "
        f"AND last_source_package_id = toUUID('{package}') "
        "AND first_use_anywhere IS NULL AND first_use_anywhere_raw = '20190600' "
        "AND first_use_commerce IS NULL AND first_use_commerce_raw = '00000000' "
        "AND is_deleted = 0"
    )
    if (direct_ok, madrid_ok, event_description_ok, partial_date_ok) != (1, 1, 1, 1):
        raise RuntimeError(
            "US M1.1 runtime semantic contract failed: "
            f"direct={direct_ok} madrid={madrid_ok} event={event_description_ok} "
            f"partial_date={partial_date_ok}"
        )
    return {
        "table_counts": actual,
        "direct_case": "PASS",
        "madrid_66a": "PASS",
        "event_description": "PASS",
        "partial_first_use_date": "PASS",
    }


def main() -> None:
    started = time.perf_counter()
    ensure_us_m1_schema()
    package_id = uuid.uuid4()
    publisher = USBatchPublisher(
        clickhouse_client(),
        package_id=package_id,
        package_kind="RUNTIME_FIXTURE",
        source_effective_date=date(2026, 8, 9),
        source_rank=SOURCE_RANK,
        batch_size=100,
    )
    try:
        for bundle in _bundles():
            publisher.add(bundle, "runtime/us_m11_fixture.xml")
        publish_counts = publisher.close()
        checks = _assert_fixture(package_id)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_M1.1_RUNTIME_FIXTURE",
                    "package_id": str(package_id),
                    "publish_counts": publish_counts,
                    "checks": checks,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_package_outputs(package_id)
        residual = sum(
            _scalar(
                f"SELECT count() FROM {table} FINAL WHERE {column} = "
                f"toUUID('{package_id}')"
            )
            for table, column in {
                "markorbit_facts.us_case_current": "last_source_package_id",
                "markorbit_facts.us_owner_current": "last_source_package_id",
                "markorbit_facts.us_classification_current": "last_source_package_id",
                "markorbit_facts.us_event_history": "source_package_id",
                "markorbit_facts.us_statement_current": "last_source_package_id",
            }.items()
        )
        if residual:
            raise RuntimeError(
                f"US M1.1 runtime fixture cleanup failed: residual_rows={residual}"
            )


if __name__ == "__main__":
    main()
