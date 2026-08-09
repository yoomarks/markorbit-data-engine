from datetime import date
from pathlib import Path
import zipfile

from app.us_assignment.parser import iter_assignment_bundles
from app.us_assignment.sample_audit import audit_assignment_sample


REAL_SHAPE = Path("tests/fixtures/us_assignment_real_historical_shape.xml")


def test_real_historical_shape_preserves_legacy_assignment_tags() -> None:
    bundle = next(iter_assignment_bundles(REAL_SHAPE))
    assert bundle.assignment.reel_frame_id == "1/0001"
    assert bundle.assignment.recorded_date == date(1955, 1, 3)
    assert bundle.assignment.last_update_date == date(1991, 7, 16)
    assert bundle.assignors[0].name == "HAWK AND BUCK COMPANY, INC., THE"
    assert bundle.assignors[0].execution_date == date(1953, 5, 13)
    assert bundle.assignees[0].name == "GRIFFIN, C. C., MANUFACTURING COMPANY"
    assert bundle.properties[0].serial_number == "71231446"
    assert bundle.properties[0].registration_number == "218184"


def test_sample_audit_profiles_real_historical_shape_without_filename_inference() -> None:
    report = audit_assignment_sample(
        REAL_SHAPE,
        source_kind="HISTORICAL",
        effective_date=date(2021, 12, 31),
    )
    assert report["status"] == "PASS"
    assert report["source"]["source_kind"] == "HISTORICAL"
    assert report["source"]["effective_date"] == "2021-12-31"
    assert report["source"]["effective_date_inferred_from_filename"] is False
    assert report["counts"]["assignments"] == 1
    assert report["counts"]["assignors"] == 1
    assert report["counts"]["assignees"] == 1
    assert report["counts"]["properties"] == 1
    assert report["parsed_field_coverage"]["property"]["serial_number"] == 1
    assert report["legal_ownership_conclusion"] is False
    assert report["gate"]["scale_up_authorized"] is False


def test_sample_audit_reads_xml_members_inside_zip(tmp_path: Path) -> None:
    archive_path = tmp_path / "assignment-sample.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(REAL_SHAPE, arcname="nested/assignment.xml")
        archive.writestr("README.txt", "ignored")

    report = audit_assignment_sample(archive_path, source_kind="DAILY")
    assert report["status"] == "PASS"
    assert report["source"]["xml_members"] == ["nested/assignment.xml"]
    assert report["counts"]["assignments"] == 1
    assert report["gate"]["ready_for_sample_ingest"] is True


def test_sample_audit_fails_closed_when_zip_has_no_xml(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.txt", "no xml")

    report = audit_assignment_sample(archive_path, source_kind="HISTORICAL")
    assert report["status"] == "FAIL"
    assert report["gate"]["ready_for_sample_ingest"] is False
    assert report["gate"]["scale_up_authorized"] is False
