from pathlib import Path

from app.us.reference_acceptance import evaluate_reference_acceptance
from app.us.reference_evidence import sha256_file


def _metadata(path: Path) -> dict:
    return {
        "reference_version": "TEST",
        "source_document_name": path.name,
        "source_document_sha256": sha256_file(path),
    }


def _inventory(unmapped: int = 0) -> dict:
    return {"unmapped_code_count": unmapped}


def test_reference_acceptance_requires_evidence_and_full_coverage(tmp_path: Path) -> None:
    status_path = tmp_path / "reference" / "us" / "status" / "status.doc"
    event_path = tmp_path / "reference" / "us" / "event" / "event.doc"
    status_path.parent.mkdir(parents=True)
    event_path.parent.mkdir(parents=True)
    status_path.write_bytes(b"status")
    event_path.write_bytes(b"event")

    report = evaluate_reference_acceptance(
        raw_root=tmp_path,
        status_metadata=_metadata(status_path),
        status_inventory=_inventory(),
        event_metadata=_metadata(event_path),
        event_inventory=_inventory(),
    )
    assert report["status"] == "PASS"

    report = evaluate_reference_acceptance(
        raw_root=tmp_path,
        status_metadata=_metadata(status_path),
        status_inventory=_inventory(1),
        event_metadata=_metadata(event_path),
        event_inventory=_inventory(),
    )
    assert report["status"] == "NOT_READY"


def test_reference_acceptance_fails_on_tampered_source(tmp_path: Path) -> None:
    status_path = tmp_path / "reference" / "us" / "status" / "status.doc"
    event_path = tmp_path / "reference" / "us" / "event" / "event.doc"
    status_path.parent.mkdir(parents=True)
    event_path.parent.mkdir(parents=True)
    status_path.write_bytes(b"status")
    event_path.write_bytes(b"event")
    status_meta = _metadata(status_path)
    event_meta = _metadata(event_path)
    status_path.write_bytes(b"tampered")
    report = evaluate_reference_acceptance(
        raw_root=tmp_path,
        status_metadata=status_meta,
        status_inventory=_inventory(),
        event_metadata=event_meta,
        event_inventory=_inventory(),
    )
    assert report["status"] == "FAIL"
