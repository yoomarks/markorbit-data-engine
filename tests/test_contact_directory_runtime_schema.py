from app.contact_ingest.directory_api import _CONTACT_ROLLUP_CTE
from app.contact_ingest.migrations import SCHEMA_SQL


def test_contact_directory_uses_explicit_mention_country_not_office_jurisdiction() -> None:
    """A filing office jurisdiction must never be treated as the contact's country."""
    # The contact schema may still expose trademark jurisdictions for evidence and
    # reporting; the Contacts country fallback itself must use explicit source
    # country_code from Entity Hub mentions.
    assert "array_agg(DISTINCT jurisdiction" in SCHEMA_SQL

    mention_stats = _CONTACT_ROLLUP_CTE[
        _CONTACT_ROLLUP_CTE.index("mention_stats AS (") :
        _CONTACT_ROLLUP_CTE.index("source_stats AS (")
    ]
    assert "DISTINCT country_code" in mention_stats
    assert "max(country_code)" in mention_stats
    assert "country_code ~ '^[A-Z]{2}$'" in mention_stats
    assert "DISTINCT jurisdiction" not in mention_stats
    assert "max(jurisdiction)" not in mention_stats
