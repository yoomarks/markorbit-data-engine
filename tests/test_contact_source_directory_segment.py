from app.contact_ingest.directory_api import _CONTACT_ROLLUP_CTE, _PAGE_HYDRATION_SQL
from app.contact_ingest.migrations import SCHEMA_SQL


def test_contact_schema_persists_source_catalog_metadata() -> None:
    assert "source_segment text NOT NULL DEFAULT 'UNKNOWN'" in SCHEMA_SQL
    assert "source_scope text NOT NULL DEFAULT ''" in SCHEMA_SQL
    assert "default_country_code char(2)" in SCHEMA_SQL
    assert "ADD COLUMN IF NOT EXISTS source_segment" in SCHEMA_SQL


def test_directory_classification_uses_curated_source_segment_as_evidence() -> None:
    assert "s.source_segment = 'AGENT'" in _CONTACT_ROLLUP_CTE
    assert "s.source_segment = 'DIRECT'" in _CONTACT_ROLLUP_CTE
    assert "LEFT JOIN contact.source AS s ON s.source_id = rr.source_id" in _CONTACT_ROLLUP_CTE
    assert "rr.source_profile = 'AGENT_CONTACT_LIST'" in _CONTACT_ROLLUP_CTE
    assert "rr.source_profile = 'QCC_COMPANY_EXPORT'" in _CONTACT_ROLLUP_CTE


def test_directory_page_hydrates_source_segment_and_scope_for_operator_visibility() -> None:
    assert "source_segments" in _PAGE_HYDRATION_SQL
    assert "source_scopes" in _PAGE_HYDRATION_SQL
    assert "s.source_segment <> 'UNKNOWN'" in _PAGE_HYDRATION_SQL
    assert "s.source_scope <> ''" in _PAGE_HYDRATION_SQL
