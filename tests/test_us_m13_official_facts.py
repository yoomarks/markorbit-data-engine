from datetime import date
from pathlib import Path
import uuid

from app.us.parser import iter_case_bundles
from app.us.publisher import TABLE_COLUMNS, bundle_rows
from app.us.publisher_m12 import SNAPSHOT_CHILD_TABLES


FIXTURE = Path("tests/fixtures/us_m13_official_facts.xml")


def test_real_layout_parser_extracts_official_fact_families() -> None:
    bundle = list(iter_case_bundles(FIXTURE))[0]

    assert bundle.case.serial_number == "88990004"
    assert bundle.case.international_registration_number == ""

    assert bundle.correspondent is not None
    assert bundle.correspondent.address_1 == "Peter S. Sloane Leason Ellis LLP"
    assert bundle.correspondent.address_4 == "United States"
    assert bundle.correspondent.attorney_name == "Jane Q. Attorney"
    assert bundle.correspondent.attorney_docket_number == "MO-88990004"
    assert bundle.correspondent.domestic_representative_name == "Domestic Representative LLC"

    assert [item.code for item in bundle.design_searches] == ["010725"]
    assert len(bundle.prior_registrations) == 1
    assert bundle.prior_registrations[0].relationship_type == "0"
    assert bundle.prior_registrations[0].number == "520350"

    foreign = bundle.foreign_applications[0]
    assert foreign.entry_number == 1
    assert foreign.application_number == "UK0000346470"
    assert foreign.country == "GB"
    assert foreign.filing_date == date(2020, 2, 6)
    assert foreign.foreign_priority_claimed is True

    filing = bundle.madrid_filings[0]
    assert filing.entry_number == 53
    assert filing.reference_number == "A0048809"
    assert filing.original_filing_date_uspto == date(2015, 3, 4)
    assert filing.international_registration_number == "1271416"
    assert filing.international_registration_date == date(2015, 5, 7)
    assert filing.international_status_code == "408"
    assert filing.international_status_date == date(2026, 1, 7)
    assert filing.international_renewal_date == date(2035, 5, 7)

    event = bundle.madrid_events[0]
    assert event.filing_entry_number == 53
    assert event.filing_reference_number == "A0048809"
    assert event.event_entry_number == 1
    assert event.code == "NEWAP"
    assert event.event_date == date(2015, 3, 4)
    assert event.description_text == "NEW APPLICATION FOR IR RECEIVED"


def test_madrid_filing_request_is_distinct_from_inbound_66a_case_fact() -> None:
    bundle = list(iter_case_bundles(FIXTURE))[0]
    assert bundle.case.madrid_66a_current is False
    assert bundle.case.international_registration_number == ""
    assert bundle.madrid_filings[0].international_registration_number == "1271416"


def test_m13_publisher_outputs_all_official_fact_tables_with_lineage() -> None:
    bundle = list(iter_case_bundles(FIXTURE))[0]
    package_id = uuid.UUID("66666666-6666-6666-6666-666666666666")
    rows = bundle_rows(
        bundle,
        package_id=package_id,
        package_kind="DAILY_APPLICATIONS",
        source_effective_date=date(2026, 1, 8),
        source_file="apc260108.xml",
        source_rank=200,
    )

    expected_counts = {
        "markorbit_facts.us_correspondent_current": 1,
        "markorbit_facts.us_design_search_current": 1,
        "markorbit_facts.us_prior_registration_current": 1,
        "markorbit_facts.us_foreign_application_current": 1,
        "markorbit_facts.us_madrid_filing_current": 1,
        "markorbit_facts.us_madrid_event_history": 1,
    }
    for table, expected in expected_counts.items():
        assert len(rows[table]) == expected
        assert len(rows[table][0]) == len(TABLE_COLUMNS[table])

    for table in expected_counts:
        columns = TABLE_COLUMNS[table]
        row = rows[table][0]
        package_column = (
            "source_package_id"
            if table.endswith("_history")
            else "last_source_package_id"
        )
        assert row[columns.index(package_column)] == package_id
        assert row[columns.index("source_rank")] == 200
        assert row[columns.index("source_file")] == "apc260108.xml"


def test_m13_replaceable_facts_join_snapshot_reconciliation_only() -> None:
    expected_current = {
        "markorbit_facts.us_correspondent_current",
        "markorbit_facts.us_design_search_current",
        "markorbit_facts.us_prior_registration_current",
        "markorbit_facts.us_foreign_application_current",
        "markorbit_facts.us_madrid_filing_current",
    }
    assert expected_current.issubset(SNAPSHOT_CHILD_TABLES)
    assert "markorbit_facts.us_madrid_event_history" not in SNAPSHOT_CHILD_TABLES
