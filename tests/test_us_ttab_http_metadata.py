from pathlib import Path

import pytest

from app.us_ttab.http_metadata import evaluate_ttab_http_evidence


def _source(tmp_path: Path, name: str = "tt260911.zip") -> tuple[Path, list[dict]]:
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    path = incoming / name
    path.write_bytes(b"official-source")
    specs = [
        {
            "path": f"incoming/us_ttab/{name}",
            "source_kind": "TTAB_BULK_DAILY_XML",
        }
    ]
    return path, specs


def _evidence(path: Path, **overrides) -> dict:
    row = {
        "file_name": path.name,
        "url": f"https://data.uspto.gov/files/TTABTDXF/{path.name}?signed=1",
        "last_modified": "Sat, 12 Sep 2026 04:10:45 GMT",
        "etag": '"4ac853ea5d872f37391af9684c9598f3"',
        "content_length": path.stat().st_size,
    }
    row.update(overrides)
    return row


def test_ttab_http_evidence_normalizes_official_file_metadata(tmp_path: Path) -> None:
    path, specs = _source(tmp_path)
    report = evaluate_ttab_http_evidence(
        evidence=[_evidence(path)], source_specs=specs, raw_root=tmp_path
    )
    assert report["status"] == "READY"
    assert report["safe"] is True
    assert report["resolved_file_count"] == 1
    assert report["normalized_metadata"] == [
        {
            "productIdentifier": "ttabtdxf",
            "files": [
                {
                    "fileName": path.name,
                    "releaseDateTime": "2026-09-12T04:10:45.000Z",
                }
            ],
        }
    ]
    assert report["local_mtime_used_as_authority"] is False


@pytest.mark.parametrize(
    ("overrides", "issue_type"),
    [
        ({"url": "https://example.com/files/TTABTDXF/tt260911.zip"}, "HTTP_EVIDENCE_URL_MISMATCH"),
        ({"url": "https://data.uspto.gov/files/TTABYR/tt260911.zip"}, "HTTP_EVIDENCE_URL_MISMATCH"),
        ({"last_modified": "2026-09-12 04:10:45"}, "HTTP_EVIDENCE_TIMESTAMP_INVALID"),
        ({"etag": ""}, "HTTP_EVIDENCE_ETAG_MISSING"),
        ({"content_length": 999}, "HTTP_EVIDENCE_SIZE_MISMATCH"),
        ({"content_length": None}, "HTTP_EVIDENCE_CONTENT_LENGTH_INVALID"),
    ],
)
def test_ttab_http_evidence_fails_closed(tmp_path: Path, overrides: dict, issue_type: str) -> None:
    path, specs = _source(tmp_path)
    report = evaluate_ttab_http_evidence(
        evidence=[_evidence(path, **overrides)], source_specs=specs, raw_root=tmp_path
    )
    assert report["status"] == "NOT_READY"
    assert issue_type in {row["type"] for row in report["issues"]}
