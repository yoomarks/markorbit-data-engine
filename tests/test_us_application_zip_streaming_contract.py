from pathlib import Path


def test_application_ingest_and_sample_audit_never_extract_zip_to_disk() -> None:
    ingest = Path("app/us/ingest.py").read_text(encoding="utf-8")
    audit = Path("app/us/sample_audit.py").read_text(encoding="utf-8")
    parser = Path("app/us/parser.py").read_text(encoding="utf-8")

    assert "archive.open(member" in ingest
    assert "archive.open(member" in audit
    assert "ET.iterparse" in parser
    for source in (ingest, audit):
        assert ".extract(" not in source
        assert ".extractall(" not in source
