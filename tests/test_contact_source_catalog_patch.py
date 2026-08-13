from __future__ import annotations

from pathlib import Path

from app.contact_ingest.source_catalog import lookup_source_catalog, normalize_source_name
from app.contact_ingest.source_catalog_patch import SOURCE_METADATA_SQL


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_matches_reviewed_country_agent_and_direct_sources() -> None:
    ukraine = lookup_source_catalog("agent_Ukraine agent list.html")
    assert ukraine is not None
    assert (ukraine.scope_label, ukraine.segment, ukraine.default_country_code) == (
        "乌克兰",
        "AGENT",
        "UA",
    )

    us_direct = lookup_source_catalog("agent_美国申请人.csv")
    assert us_direct is not None
    assert (us_direct.scope_label, us_direct.segment, us_direct.default_country_code) == (
        "美国",
        "DIRECT",
        "US",
    )

    qcc = lookup_source_catalog("【企查查】批量查询-企业基础工商信息(0416_142564808).xlsx")
    assert qcc is not None
    assert (qcc.scope_label, qcc.segment, qcc.default_country_code) == (
        "中国",
        "DIRECT",
        "CN",
    )


def test_catalog_preserves_scope_only_sources_without_inventing_country() -> None:
    comprehensive = lookup_source_catalog("agent_2025-Attorney-List.pdf")
    assert comprehensive is not None
    assert comprehensive.scope_label == "综合"
    assert comprehensive.default_country_code == ""

    aripo = lookup_source_catalog("agent_aripo agents 官网.xls")
    assert aripo is not None
    assert aripo.scope_label == "ARIPO"
    assert aripo.default_country_code == ""


def test_catalog_normalizes_nbsp_case_and_path_but_not_unknown_agent_files() -> None:
    assert normalize_source_name("C:/tmp/AGENT_UKRAINE AGENT LIST.HTML") == normalize_source_name(
        "agent_Ukraine agent list.html"
    )
    assert lookup_source_catalog("C:/tmp/AGENT_UKRAINE AGENT LIST.HTML") is not None
    assert lookup_source_catalog("agent_future_france.xlsx") is None


def test_patch_schema_adds_source_metadata_columns() -> None:
    assert "source_segment text" in SOURCE_METADATA_SQL
    assert "source_scope text" in SOURCE_METADATA_SQL
    assert "default_country_code char(2)" in SOURCE_METADATA_SQL


def test_patch_never_overwrites_explicit_country_and_requires_unambiguous_fallback() -> None:
    source = (ROOT / "app" / "contact_ingest" / "source_catalog_patch.py").read_text(
        encoding="utf-8"
    )
    assert "e.country_code IS NULL OR btrim(e.country_code) = ''" in source
    assert "count(DISTINCT s.default_country_code) = 1" in source
    assert "p.country_code IS NULL OR btrim(p.country_code) = ''" in source
    assert "count(DISTINCT e.country_code) = 1" in source


def test_patch_command_is_explicit_and_contacts_only() -> None:
    script = (ROOT / "scripts" / "patch-contact-source-catalog.ps1").read_text(
        encoding="utf-8"
    )
    assert "app.contact_ingest.source_catalog_patch" in script
    assert "api must be running" in script
    assert "app.cn" not in script
    assert "app.us" not in script
