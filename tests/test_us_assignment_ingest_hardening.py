from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from app.us_assignment.ingest import (
    _coalesce_assignment_bundles,
    _empty_assignment_package_is_acceptable,
    _source_xml_member_names,
)
from app.us_assignment.model import AssignmentBundle, AssignmentRecord


def _bundle(*, conveyance: str = "ASSIGNS THE ENTIRE INTEREST") -> AssignmentBundle:
    return AssignmentBundle(
        assignment=AssignmentRecord(
            reel_no="9180",
            frame_no="0001",
            reel_frame_id="9180/0001",
            conveyance_text=conveyance,
        )
    )


def test_assignment_package_coalesces_exact_duplicate_reel_frame() -> None:
    bundle = _bundle()
    rows = list(_coalesce_assignment_bundles(iter([("daily.xml", bundle), ("daily.xml", bundle)])))
    assert rows == [("daily.xml", bundle)]


def test_assignment_package_rejects_conflicting_duplicate_reel_frame() -> None:
    with pytest.raises(RuntimeError, match="Conflicting duplicate reel/frame"):
        list(
            _coalesce_assignment_bundles(
                iter([("daily.xml", _bundle()), ("daily.xml", _bundle(conveyance="SECURITY INTEREST"))])
            )
        )


def test_assignment_package_keeps_source_provenance_in_duplicate_identity() -> None:
    bundle = _bundle()
    with pytest.raises(RuntimeError, match="Conflicting duplicate reel/frame"):
        list(_coalesce_assignment_bundles(iter([("part-1.xml", bundle), ("part-2.xml", bundle)])))


def _write_assignment_xml(path: Path, *, no_data: bool) -> None:
    payload = (
        "<trademark-assignments><assignment-information>"
        + ("<data-available-code>N</data-available-code>" if no_data else "")
        + "</assignment-information></trademark-assignments>"
    )
    path.write_text(payload, encoding="utf-8")


def test_daily_zero_record_delivery_requires_explicit_no_data_code(tmp_path: Path) -> None:
    source = tmp_path / "daily.xml"
    _write_assignment_xml(source, no_data=True)
    assert _empty_assignment_package_is_acceptable(source, "DAILY_ASSIGNMENT_XML") is True
    assert _empty_assignment_package_is_acceptable(source, "ASSIGNMENT_SNAPSHOT_XML") is False

    _write_assignment_xml(source, no_data=False)
    assert _empty_assignment_package_is_acceptable(source, "DAILY_ASSIGNMENT_XML") is False


def test_zip_zero_record_delivery_requires_every_xml_member_to_declare_no_data(tmp_path: Path) -> None:
    source = tmp_path / "daily.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("a.xml", "<root><data-available-code>N</data-available-code></root>")
        archive.writestr("b.xml", "<root><data-available-code>N</data-available-code></root>")
    assert _empty_assignment_package_is_acceptable(source, "DAILY_ASSIGNMENT_XML") is True
    assert _source_xml_member_names(source) == ["a.xml", "b.xml"]

    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("a.xml", "<root><data-available-code>N</data-available-code></root>")
        archive.writestr("b.xml", "<root></root>")
    assert _empty_assignment_package_is_acceptable(source, "DAILY_ASSIGNMENT_XML") is False
