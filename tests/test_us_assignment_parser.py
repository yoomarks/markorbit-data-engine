from datetime import date
from pathlib import Path

from app.us_assignment.parser import iter_assignment_bundles


def test_assignment_parser_reads_reel_frame_parties_and_properties() -> None:
    bundles = list(iter_assignment_bundles(Path("tests/fixtures/us_assignment_synthetic.xml")))
    assert len(bundles) == 1
    bundle = bundles[0]
    record = bundle.assignment
    assert record.reel_frame_id == "1234/0056"
    assert record.recorded_date == date(2026, 8, 1)
    assert record.last_update_date == date(2026, 8, 2)
    assert record.page_count == 7
    assert record.correspondent_name == "Fixture Counsel LLP"
    assert record.conveyance_text == "ASSIGNS THE ENTIRE INTEREST"
    assert [party.name for party in bundle.assignors] == [
        "Alpha Brand LLC",
        "Alpha Holdings Inc.",
    ]
    assert bundle.assignors[0].execution_date == date(2026, 7, 29)
    assert [party.name for party in bundle.assignees] == ["Beta Brand Inc."]
    assert [item.serial_number for item in bundle.properties] == ["88991234", "88995678"]


def test_assignment_parser_preserves_partial_or_invalid_dates_raw(tmp_path: Path) -> None:
    source = tmp_path / "partial.xml"
    source.write_text(
        """<trademark-assignments><assignment-entry><assignment>
        <reel-no>9</reel-no><frame-no>1</frame-no>
        <date-recorded>20260800</date-recorded><last-update-date>bad</last-update-date>
        </assignment></assignment-entry></trademark-assignments>""",
        encoding="utf-8",
    )
    record = next(iter_assignment_bundles(source)).assignment
    assert record.recorded_date is None
    assert record.recorded_date_raw == "20260800"
    assert record.last_update_date is None
    assert record.last_update_date_raw == "bad"
