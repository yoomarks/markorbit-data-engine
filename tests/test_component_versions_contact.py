from pathlib import Path

from app.component_versions import component_versions
from app.contact_ingest import CONTACT_INGEST_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_component_matrix_exposes_contact_ingestion_version() -> None:
    assert component_versions()["components"]["contact_ingestion"]["version"] == CONTACT_INGEST_VERSION


def test_component_version_doc_includes_contact_ingestion_version() -> None:
    text = (ROOT / "docs" / "COMPONENT_VERSIONS.md").read_text(encoding="utf-8")
    assert CONTACT_INGEST_VERSION in text
