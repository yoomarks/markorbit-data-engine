from pathlib import Path

from app.us.reference_evidence import sha256_file, verify_source_evidence


def test_reference_evidence_pass_missing_and_tamper(tmp_path: Path) -> None:
    path = tmp_path / "reference" / "us" / "status" / "source.doc"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"official-source")
    expected = sha256_file(path)
    metadata = {
        "source_document_name": "source.doc",
        "source_document_sha256": expected,
    }
    assert verify_source_evidence(metadata, tmp_path, family="status")["status"] == "PASS"
    path.write_bytes(b"tampered")
    assert verify_source_evidence(metadata, tmp_path, family="status")["status"] == "FAIL"
    path.unlink()
    assert verify_source_evidence(metadata, tmp_path, family="status")["status"] == "NOT_READY"
