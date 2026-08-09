from datetime import date
from pathlib import Path
import uuid

import app.main as main
from app.us_assignment import ASSIGNMENT_JURISDICTION, ASSIGNMENT_SCHEMA_VERSION
from app.us_assignment.model import AssignmentBundle, AssignmentParty, AssignmentProperty, AssignmentRecord
from app.us_assignment.publisher import AssignmentBatchPublisher, TABLE_COLUMNS
from app.us_assignment.repository import assignment_source_rank


def test_assignment_component_is_isolated_from_us_application_queue() -> None:
    assert ASSIGNMENT_JURISDICTION == "US_ASSIGNMENT"
    assert ASSIGNMENT_SCHEMA_VERSION == "US_ASSIGNMENT_M1.0"
    repository = Path("app/us_assignment/repository.py").read_text(encoding="utf-8")
    jobs = Path("app/us_assignment/jobs.py").read_text(encoding="utf-8")
    assert "US_ASSIGNMENT" in repository
    assert "DELIVERY_DATE" in repository
    assert "Revision precedence is not modeled" in repository
    assert "source precedence is immutable" in repository
    assert "retry-us-assignment.ps1" in jobs
    assert "jurisdiction = 'US'" not in repository


def test_assignment_source_rank_is_effective_date_then_package_sequence() -> None:
    older = assignment_source_rank(date(2026, 8, 1), 999)
    newer = assignment_source_rank(date(2026, 8, 2), 1)
    same_day_later = assignment_source_rank(date(2026, 8, 1), 1000)
    assert older < newer
    assert older < same_day_later


def test_assignment_publisher_builds_all_four_fact_families() -> None:
    class Client:
        def __init__(self) -> None:
            self.inserts: list[tuple[str, list[list[object]], list[str]]] = []

        def insert(self, table, rows, column_names):
            self.inserts.append((table, [list(row) for row in rows], list(column_names)))

    client = Client()
    publisher = AssignmentBatchPublisher(
        client,
        package_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        source_kind="DAILY_ASSIGNMENT_XML",
        source_effective_date=date(2026, 8, 1),
        source_rank=123,
    )
    bundle = AssignmentBundle(
        assignment=AssignmentRecord(reel_no="1", frame_no="2", reel_frame_id="1/2"),
        assignors=(AssignmentParty(reel_frame_id="1/2", ordinal=1, name="Alpha LLC"),),
        assignees=(AssignmentParty(reel_frame_id="1/2", ordinal=1, name="Beta Inc."),),
        properties=(AssignmentProperty(reel_frame_id="1/2", ordinal=1, serial_number="88991234"),),
    )
    publisher.add(bundle, "fixture.xml")
    counts = publisher.close()
    assert set(counts) == set(TABLE_COLUMNS)
    assert all(value == 1 for value in counts.values())
    assert len(client.inserts) == 4


def test_assignment_routes_are_get_only_and_mounted() -> None:
    expected = {
        "/api/us/assignments/reel-frame/{reel_no}/{frame_no}",
        "/api/us/assignments/{serial_number}",
        "/api/us/assignments/{serial_number}/reconciliation",
    }
    paths = {route.path for route in main.app.routes}
    assert expected.issubset(paths)
    for route in main.app.routes:
        if route.path in expected:
            assert route.methods == {"GET"}


def test_assignment_schema_is_separate_from_us_m14_reset_and_apply() -> None:
    schema = Path("database/clickhouse/init/009_us_assignment_m10.sql").read_text(encoding="utf-8")
    reset = Path("app/us/reset_rebuild.py").read_text(encoding="utf-8")
    us_apply = Path("scripts/apply-us-m1-schema.ps1").read_text(encoding="utf-8")
    assignment_apply = Path("scripts/apply-us-assignment-schema.ps1").read_text(encoding="utf-8")
    for table in (
        "us_assignment_record_history",
        "us_assignment_assignor_history",
        "us_assignment_assignee_history",
        "us_assignment_property_history",
    ):
        assert table in schema
        assert table not in reset
    assert "009_us_assignment_m10.sql" not in us_apply
    assert "009_us_assignment_m10.sql" in assignment_apply
    assert "US_ASSIGNMENT_M1.0" in schema


def test_assignment_uses_existing_us_live_job_without_new_ci_job() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert workflow.count("us-runtime-fixture:") == 1
    assert "Run US Assignment M1.0 recorded-interest fixture" in workflow
    assert "python -m app.us_assignment.validate_fixture" in workflow


def test_assignment_operational_wrappers_are_separate() -> None:
    for script in (
        "apply-us-assignment-schema.ps1",
        "register-us-assignment.ps1",
        "run-us-assignment.ps1",
        "retry-us-assignment.ps1",
        "validate-us-assignment-fixture.ps1",
    ):
        assert Path("scripts", script).is_file()
