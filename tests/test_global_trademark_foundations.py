from app.global_trademarks.catalog import SourceRole, country_plan
from app.global_trademarks.schema import SCHEMA_SQL


def test_tm_link_is_only_active_for_eu_and_nz() -> None:
    active_tm_link = {
        jurisdiction
        for jurisdiction in ("US", "GB", "EU", "CA", "AU", "NZ")
        for source in country_plan(jurisdiction).sources
        if source.source_id.startswith("TM_LINK_") and source.active_now
    }
    assert active_tm_link == {"EU", "NZ"}


def test_country_plans_preserve_source_specific_strategies() -> None:
    assert country_plan("US").store_schema == "us"
    assert country_plan("GB").store_schema == "trademark_gb"
    assert country_plan("CA").store_schema == "trademark_ca"
    assert country_plan("AU").store_schema == "trademark_au"

    ca_roles = {source.role for source in country_plan("CA").sources if source.active_now}
    assert SourceRole.PRIMARY in ca_roles
    assert SourceRole.INCREMENTAL in ca_roles

    au_sources = {source.source_id for source in country_plan("AU").sources if source.active_now}
    assert au_sources == {"IPGOD_2022"}

    assert country_plan("EM") is country_plan("EU")


def test_schema_is_country_native_not_one_lossy_common_table() -> None:
    for schema in ("trademark_gb", "trademark_eu", "trademark_ca", "trademark_au", "trademark_nz"):
        assert f"CREATE SCHEMA IF NOT EXISTS {schema};" in SCHEMA_SQL

    assert "global_trademark_source_object" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS global_trademark" not in SCHEMA_SQL


def test_australia_preserves_six_source_domains() -> None:
    for table in (
        "trademark_au.application",
        "trademark_au.party_activity",
        "trademark_au.application_link",
        "trademark_au.application_event",
        "trademark_au.application_classification",
        "trademark_au.application_description",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL

    assert "effective_from_date" in SCHEMA_SQL
    assert "effective_to_date" in SCHEMA_SQL
    assert "linked_application_country" in SCHEMA_SQL


def test_canada_preserves_rich_st96_domains() -> None:
    for table in (
        "trademark_ca.st96_record",
        "trademark_ca.party",
        "trademark_ca.goods_service",
        "trademark_ca.event",
        "trademark_ca.relationship",
        "trademark_ca.asset",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL


def test_thin_seeds_are_explicitly_unverified() -> None:
    eu_sql = SCHEMA_SQL.split("CREATE SCHEMA IF NOT EXISTS trademark_eu;", 1)[1]
    nz_sql = SCHEMA_SQL.split("CREATE SCHEMA IF NOT EXISTS trademark_nz;", 1)[1]
    assert "current_state_verified boolean NOT NULL DEFAULT false" in eu_sql
    assert "current_state_verified boolean NOT NULL DEFAULT false" in nz_sql
