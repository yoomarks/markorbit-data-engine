from datetime import datetime, timezone
from pathlib import Path
import uuid

import app.main as main
from app.us_ttab import TTAB_JURISDICTION, TTAB_SCHEMA_VERSION, TTAB_SEMANTICS
from app.us_ttab.model import TTABProceedingBundle, TTABProceedingRecord
from app.us_ttab.publisher import TABLE_COLUMNS, TTABBatchPublisher
from app.us_ttab.repository import VALID_SOURCE_KINDS, normalize_snapshot_at, ttab_source_rank


def test_ttab_component_is_isolated() -> None:
    assert TTAB_JURISDICTION == "US_TTAB"
    assert TTAB_SCHEMA_VERSION == "US_TTAB_M1.2"
    assert "NOT_OUTCOME" in TTAB_SEMANTICS
    repository = Path("app/us_ttab/repository.py").read_text(encoding="utf-8")
    jobs = Path("app/us_ttab/jobs.py").read_text(encoding="utf-8")
    assert "US_TTAB" in repository
    assert "SNAPSHOT_AT" in repository
    assert "schema_version" in repository
    assert "markorbit:us:ttab-ingestion" in Path("app/us_ttab/run_guard.py").read_text(
        encoding="utf-8"
    )
    assert "retry-us-ttab.ps1" in jobs


def test_ttab_source_kinds_cover_rawxml_and_official_bulk() -> None:
    assert VALID_SOURCE_KINDS == {
        "TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
        "TTAB_BULK_DAILY_XML",
        "TTAB_BULK_HISTORICAL_XML",
    }
    register_script = Path("scripts/register-us-ttab.ps1").read_text(encoding="utf-8")
    run_script = Path("scripts/run-us-ttab.ps1").read_text(encoding="utf-8")
    for source_kind in VALID_SOURCE_KINDS:
        assert source_kind in register_script
    assert "apply-us-ttab-schema.ps1" in register_script
    assert "apply-us-ttab-schema.ps1" in run_script


def test_ttab_source_rank_orders_milliseconds_then_package_sequence() -> None:
    base = datetime(2026, 8, 9, 12, 0, 0, 1000, tzinfo=timezone.utc)
    next_millisecond = datetime(2026, 8, 9, 12, 0, 0, 2000, tzinfo=timezone.utc)
    older = ttab_source_rank(base, 999)
    newer = ttab_source_rank(next_millisecond, 1)
    same_millisecond_later_package = ttab_source_rank(base, 1000)
    assert older < newer
    assert older < same_millisecond_later_package
    normalized = normalize_snapshot_at(
        datetime(2026, 8, 9, 12, 0, 0, 1999, tzinfo=timezone.utc)
    )
    assert normalized.microsecond == 1000


def test_ttab_publisher_writes_four_append_only_fact_families() -> None:
    class Client:
        def __init__(self) -> None:
            self.inserts = []

        def insert(self, table, rows, column_names):
            self.inserts.append((table, [list(row) for row in rows], list(column_names)))

    client = Client()
    publisher = TTABBatchPublisher(
        client,
        package_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        source_kind="TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
        source_snapshot_at=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
        source_rank=123,
    )
    publisher.add(
        TTABProceedingBundle(
            proceeding=TTABProceedingRecord(
                proceeding_number="91234567",
                proceeding_type="Opposition",
                proceeding_type_code="OPP",
                status_text="Pending",
                status_code="9",
            )
        ),
        "fixture.xml",
    )
    counts = publisher.close()
    assert set(counts) == set(TABLE_COLUMNS)
    assert counts["markorbit_facts.us_ttab_proceeding_history"] == 1
    assert sum(counts.values()) == 1


def test_ttab_routes_are_read_only_and_mounted() -> None:
    expected = {
        "/api/us/ttab/schema",
        "/api/us/ttab/acceptance",
        "/api/us/ttab/readiness",
        "/api/us/ttab/by-serial/{serial_number}",
        "/api/us/ttab/timeline/{proceeding_number}",
        "/api/us/ttab/proceedings/{proceeding_number}",
    }
    paths = {route.path for route in main.app.routes}
    assert expected.issubset(paths)
    for route in main.app.routes:
        if route.path in expected:
            assert route.methods == {"GET"}


def test_ttab_schema_is_not_part_of_us_application_or_assignment_reset() -> None:
    base_schema = Path("database/clickhouse/init/010_us_ttab_m10.sql").read_text(encoding="utf-8")
    rawxml_upgrade = Path("database/clickhouse/init/011_us_ttab_m11_real_rawxml.sql").read_text(encoding="utf-8")
    bulk_upgrade = Path("database/clickhouse/init/012_us_ttab_m12_official_bulk.sql").read_text(encoding="utf-8")
    apply_script = Path("scripts/apply-us-ttab-schema.ps1").read_text(encoding="utf-8")
    us_apply = Path("scripts/apply-us-m1-schema.ps1").read_text(encoding="utf-8")
    us_reset = Path("app/us/reset_rebuild.py").read_text(encoding="utf-8")
    assignment_schema = Path("database/clickhouse/init/009_us_assignment_m10.sql").read_text(encoding="utf-8")
    for table in (
        "us_ttab_proceeding_history",
        "us_ttab_party_history",
        "us_ttab_property_history",
        "us_ttab_docket_history",
    ):
        assert table in base_schema
        assert table not in us_apply
        assert table not in us_reset
        assert table not in assignment_schema
    assert "US_TTAB_M1.1" in rawxml_upgrade
    assert "US_TTAB_M1.2" in bulk_upgrade
    assert "011_us_ttab_m11_real_rawxml.sql" in apply_script
    assert "012_us_ttab_m12_official_bulk.sql" in apply_script


def test_ttab_operational_scripts_and_ci_gate_exist() -> None:
    for name in (
        "apply-us-ttab-schema.ps1",
        "register-us-ttab.ps1",
        "run-us-ttab.ps1",
        "retry-us-ttab.ps1",
        "audit-us-ttab-real-data.ps1",
        "check-us-ttab-readiness.ps1",
        "validate-us-ttab-fixture.ps1",
    ):
        assert Path("scripts", name).is_file()
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "app.us_ttab.validate_fixture" in ci
    assert "US TTAB M1.1 real-rawxml contract fixture" in ci
