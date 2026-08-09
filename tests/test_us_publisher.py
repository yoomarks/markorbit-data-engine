from datetime import date
from pathlib import Path
import uuid

from app.us.parser import iter_case_bundles
from app.us.publisher import TABLE_COLUMNS, USBatchPublisher, bundle_rows, case_id


FIXTURE = Path("tests/fixtures/us_m1_daily.xml")


class FakeClickHouse:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[list[object]], list[str]]] = []

    def insert(self, table: str, rows: list[list[object]], column_names: list[str]) -> None:
        self.inserts.append((table, [list(row) for row in rows], list(column_names)))


def test_bundle_rows_have_deterministic_identity_and_lineage() -> None:
    bundle = list(iter_case_bundles(FIXTURE))[0]
    package_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    rows = bundle_rows(
        bundle,
        package_id=package_id,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 8, 9),
        source_file="apc260809.xml",
        source_rank=123,
    )
    case_row = rows["markorbit_facts.us_case_current"][0]
    columns = TABLE_COLUMNS["markorbit_facts.us_case_current"]
    values = dict(zip(columns, case_row, strict=True))
    assert values["case_id"] == case_id("97123456")
    assert values["serial_number"] == "97123456"
    assert values["status_code"] == "700"
    assert values["last_source_package_id"] == package_id
    assert values["source_rank"] == 123
    assert len(str(values["record_hash"])) == 64


def test_batch_publisher_emits_populated_legacy_fixture_families() -> None:
    client = FakeClickHouse()
    publisher = USBatchPublisher(
        client,
        package_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 8, 9),
        source_rank=456,
        batch_size=100,
    )
    bundles = list(iter_case_bundles(FIXTURE))
    for bundle in bundles:
        publisher.add(bundle, "fixture.xml")
    counts = publisher.close()

    expected_populated = {
        "markorbit_facts.us_case_current",
        "markorbit_facts.us_owner_current",
        "markorbit_facts.us_classification_current",
        "markorbit_facts.us_event_history",
        "markorbit_facts.us_statement_current",
    }
    assert counts["markorbit_facts.us_case_current"] == 2
    assert counts["markorbit_facts.us_owner_current"] == 2
    assert counts["markorbit_facts.us_classification_current"] == 2
    assert counts["markorbit_facts.us_event_history"] == 2
    assert counts["markorbit_facts.us_statement_current"] == 2
    assert {table for table, _rows, _columns in client.inserts} == expected_populated
    assert expected_populated.issubset(TABLE_COLUMNS)
