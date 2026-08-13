from app.contact_ingest.directory_api import _CONTACT_ROLLUP_CTE
from app.contact_ingest.migrations import SCHEMA_SQL


def test_contact_directory_uses_real_entity_mention_jurisdiction_column() -> None:
    """Keep directory analytics aligned with the Entity Hub mention schema."""
    assert "array_agg(DISTINCT jurisdiction" in SCHEMA_SQL

    mention_stats = _CONTACT_ROLLUP_CTE[
        _CONTACT_ROLLUP_CTE.index("mention_stats AS (") :
        _CONTACT_ROLLUP_CTE.index("source_stats AS (")
    ]
    assert "DISTINCT jurisdiction" in mention_stats
    assert "max(jurisdiction)" in mention_stats
    assert "jurisdiction ~ '^[A-Z]{2}$'" in mention_stats
    assert "DISTINCT country_code" not in mention_stats
    assert "max(country_code)" not in mention_stats
