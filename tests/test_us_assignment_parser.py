from datetime import date
import io
from pathlib import Path

import pytest

import app.us_assignment.parser as assignment_parser
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


def test_assignment_parser_preserves_current_official_bulk_party_tags() -> None:
    bundles = list(
        iter_assignment_bundles(Path("tests/fixtures/us_assignment_current_bulk_party_tags.xml"))
    )
    assert len(bundles) == 2
    first, second = bundles
    assert first.assignment.reel_frame_id == "1/0058"
    assert first.assignors[0].country == "DENMARK"
    assert first.assignors[0].acknowledgement_date == date(1954, 6, 16)
    assert second.assignors[0].dba_statement == "DBA HANDY ROLL CO."
    assert second.assignors[0].acknowledgement_date == date(1955, 1, 17)
    assert second.assignees[0].composed_of_statement == "H.I. SALSBURY & EARL SHULTZ"


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


def test_assignment_parser_streams_entry_fragments_across_tiny_chunks(monkeypatch) -> None:
    monkeypatch.setattr(assignment_parser, "_FRAGMENT_CHUNK_SIZE", 17)
    source = io.BytesIO(
        b"<trademark-assignments><assignment-information>"
        b"<assignment-entry><assignment><reel-no>1</reel-no><frame-no>1</frame-no>"
        b"</assignment></assignment-entry>"
        b"<assignment-entry><assignment><reel-no>2</reel-no><frame-no>2</frame-no>"
        b"</assignment></assignment-entry>"
        b"</assignment-information></trademark-assignments>"
    )
    assert [bundle.assignment.reel_frame_id for bundle in iter_assignment_bundles(source)] == [
        "1/1",
        "2/2",
    ]


def test_assignment_parser_allows_official_zero_record_delivery(monkeypatch) -> None:
    monkeypatch.setattr(assignment_parser, "_FRAGMENT_CHUNK_SIZE", 13)
    source = io.StringIO(
        "<trademark-assignments><assignment-information>"
        "<data-available-code>N</data-available-code>"
        "</assignment-information></trademark-assignments>"
    )
    assert list(iter_assignment_bundles(source)) == []


def test_assignment_parser_rejects_truncated_document_after_valid_entry() -> None:
    source = io.BytesIO(
        b"<trademark-assignments><assignment-information>"
        b"<assignment-entry><assignment><reel-no>1</reel-no><frame-no>1</frame-no>"
        b"</assignment></assignment-entry>"
    )
    with pytest.raises(assignment_parser.ET.ParseError):
        list(iter_assignment_bundles(source))


def test_assignment_parser_rejects_malformed_markup_between_entries() -> None:
    source = io.BytesIO(
        b"<trademark-assignments><assignment-information>"
        b"<assignment-entry><assignment><reel-no>1</reel-no><frame-no>1</frame-no>"
        b"</assignment></assignment-entry><broken>"
        b"<assignment-entry><assignment><reel-no>2</reel-no><frame-no>2</frame-no>"
        b"</assignment></assignment-entry>"
        b"</assignment-information></trademark-assignments>"
    )
    with pytest.raises(assignment_parser.ET.ParseError):
        list(iter_assignment_bundles(source))
