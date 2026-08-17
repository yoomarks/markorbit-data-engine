from __future__ import annotations

from pathlib import Path

from app.contact_ingest.country_inference import (
    Evidence,
    ReferenceModels,
    _cctld_country,
    _countries_in_text,
    _country_from_explicit_value,
    _entity_evidence,
    _phone_country,
    infer_from_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


def test_explicit_country_names_codes_and_business_aliases_are_supported() -> None:
    assert _country_from_explicit_value("Germany") == "DE"
    assert _country_from_explicit_value("DEU") == "DE"
    assert _country_from_explicit_value("UK") == "GB"
    assert _country_from_explicit_value("UAE") == "AE"
    assert _country_from_explicit_value("South Korea") == "KR"
    assert _country_from_explicit_value("中国") == "CN"
    assert _country_from_explicit_value("香港") == "HK"
    assert _countries_in_text("Room 8, Shibuya-ku, Tokyo, Japan") == {"JP"}
    assert _countries_in_text("Suite 8, New York, USA") == {"US"}
    assert _countries_in_text("深圳市南山区，中国") == {"CN"}
    assert _countries_in_text("中国香港九龙") == {"HK"}


def test_ambiguous_country_words_do_not_become_high_confidence_address_evidence() -> None:
    assert _countries_in_text("Atlanta, Georgia 30303") == set()
    assert _countries_in_text("Jordan Road, Hong Kong") == {"HK"}


def test_international_phone_country_uses_numbering_plan_not_manual_prefix_table() -> None:
    assert _phone_country("+44 20 8366 1177") == ("GB", 0.94)
    assert _phone_country("+1 650 253 2222") == ("US", 0.94)
    assert _phone_country("020 8366 1177") is None


def test_country_code_domains_are_signal_but_genericized_cctlds_are_not() -> None:
    assert _cctld_country("example.de") == ("DE", 0.90)
    assert _cctld_country("firm.co.uk") == ("GB", 0.92)
    assert _cctld_country("startup.io") is None
    assert _cctld_country("brand.ai") is None


def test_high_confidence_single_signal_can_be_accepted() -> None:
    result = infer_from_evidence(
        [Evidence("JP", "RAW_EXPLICIT_COUNTRY_FIELD", 0.995, "Japan", "country")]
    )
    assert result.status == "ACCEPTED"
    assert result.country_code == "JP"
    assert result.confidence == 0.995


def test_conflicting_strong_phone_and_domain_fail_closed() -> None:
    result = infer_from_evidence(
        [
            Evidence("GB", "INTERNATIONAL_PHONE", 0.94, "+442083661177", "PHONE"),
            Evidence("DE", "COUNTRY_CODE_DOMAIN", 0.90, "example.de", "WEBSITE"),
        ]
    )
    assert result.status == "CONFLICT"
    assert result.country_code == "GB"
    assert result.runner_up_country_code == "DE"


def test_two_medium_independent_signals_can_cross_threshold() -> None:
    result = infer_from_evidence(
        [
            Evidence("SG", "CITY_CORPUS_MODEL", 0.74, "Singapore", "city"),
            Evidence("SG", "COUNTRY_CODE_DOMAIN", 0.70, "example.sg", "EMAIL"),
        ]
    )
    assert result.status == "ACCEPTED"
    assert result.country_code == "SG"
    assert result.confidence > 0.90


def test_entity_evidence_does_not_use_trademark_jurisdiction_as_country() -> None:
    entity = {
        "entity_id": "00000000-0000-0000-0000-000000000001",
        "canonical_name": "Chinese Applicant",
        "normalized_address": "",
        "city": "",
    }
    context = {
        "raw": [],
        "channels": [],
        "identifiers": [],
        "mentions": [
            {
                "country_code": "",
                "raw_address": "",
                "city": "",
                "region_code": "",
                "role": "APPLICANT",
                "jurisdiction": "US",
            }
        ],
    }
    evidence = _entity_evidence(entity, context, ReferenceModels({}, {}))
    assert evidence == []


def test_source_default_country_and_corporate_domain_are_traceable_evidence() -> None:
    entity = {
        "entity_id": "00000000-0000-0000-0000-000000000002",
        "canonical_name": "Firm",
        "normalized_address": "",
        "city": "",
    }
    context = {
        "raw": [
            {
                "default_country_code": "AU",
                "source_name": "Australia agents.xlsx",
                "raw_data": {},
            }
        ],
        "channels": [
            {
                "channel_type": "EMAIL",
                "channel_value": "hello@firm.com",
                "normalized_value": "hello@firm.com",
            }
        ],
        "identifiers": [],
        "mentions": [],
    }
    models = ReferenceModels({}, {"firm.com": ("AU", 1.0, 4)})
    result = infer_from_evidence(_entity_evidence(entity, context, models))
    assert result.status == "ACCEPTED"
    assert result.country_code == "AU"
    assert {item.kind for item in result.evidence} == {
        "SOURCE_DEFAULT_COUNTRY",
        "KNOWN_CORPORATE_DOMAIN",
    }


def test_inference_schema_cli_and_training_preserve_safety_contracts() -> None:
    source = (ROOT / "app" / "contact_ingest" / "country_inference.py").read_text(
        encoding="utf-8"
    )
    migration = (
        ROOT / "database" / "postgres" / "init" / "010_contact_country_inference.sql"
    ).read_text(encoding="utf-8")
    assert "contact.country_inference_run" in source
    assert "contact.entity_country_inference" in source
    assert "WHERE entity_id = %s AND country_code IS NULL" in source
    assert "ci.applied_at IS NOT NULL" in source
    assert "AND ci.entity_id IS NULL" in source
    assert "--apply" in source
    assert "INFERRED_CONTACT_GEO_OVERLAY_NOT_OFFICIAL_TRADEMARK_FACT" in source
    assert '"source_country_fields_mutated": False' in source
    assert "UPDATE entity.entity" not in source
    assert "UPDATE contact.person" not in source
    assert "CREATE TABLE IF NOT EXISTS contact.entity_country_inference" in migration
    assert "ix_contact_country_inference_applied" in migration


def test_directory_fallback_uses_explicit_mention_country_not_jurisdiction() -> None:
    runtime = (ROOT / "app" / "contact_ingest" / "directory_runtime.py").read_text(
        encoding="utf-8"
    )
    evidence_sql = runtime.split("_PAGE_EVIDENCE_SQL", 1)[1].split(
        "def _channel_exists_sql", 1
    )[0]
    assert "DISTINCT m.country_code" in evidence_sql
    assert "max(m.country_code)" in evidence_sql
    assert "DISTINCT m.jurisdiction" not in evidence_sql
    assert "active_ci.status = 'ACCEPTED'" in runtime
    assert "active_ci.applied_at IS NOT NULL" in runtime

    analytics = (ROOT / "app" / "contact_ingest" / "directory_api.py").read_text(
        encoding="utf-8"
    )
    mention_cte = analytics.split("mention_stats AS (", 1)[1].split("source_stats AS (", 1)[0]
    assert "DISTINCT country_code" in mention_cte
    assert "DISTINCT jurisdiction" not in mention_cte
    assert "SOURCE_ENTITY_THEN_SOURCE_MENTION_THEN_APPLIED_INFERENCE" in analytics
