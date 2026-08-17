from __future__ import annotations

from app.contact_ingest import country_inference as engine
from app.contact_ingest.country_inference_runtime import (
    CONTACT_COUNTRY_RUNTIME_MODEL_VERSION,
    _CONTACT_CITY_COUNTS_SQL,
    build_runtime_reference_models,
)


def test_runtime_city_training_is_contact_scoped_and_avoids_global_mentions() -> None:
    assert CONTACT_COUNTRY_RUNTIME_MODEL_VERSION == "CONTACT_COUNTRY_RUNTIME_MODEL_V3"
    assert "contact.raw_record" in _CONTACT_CITY_COUNTS_SQL
    assert "contact.entity_person_relation" in _CONTACT_CITY_COUNTS_SQL
    assert "contact.channel" in _CONTACT_CITY_COUNTS_SQL
    assert "entity.entity_mention" not in _CONTACT_CITY_COUNTS_SQL
    assert "ci.applied_at IS NOT NULL" in _CONTACT_CITY_COUNTS_SQL
    assert "ci.entity_id IS NULL" in _CONTACT_CITY_COUNTS_SQL


def test_runtime_reference_builder_keeps_v1_model_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.contact_ingest.country_inference_runtime.build_contact_scoped_city_model",
        lambda: {"london": ("GB", 1.0, 4)},
    )
    monkeypatch.setattr(
        engine,
        "_build_domain_model",
        lambda: {"example.co.uk": ("GB", 1.0, 2)},
    )
    models = build_runtime_reference_models()
    assert isinstance(models, engine.ReferenceModels)
    assert models.city_country["london"] == ("GB", 1.0, 4)
    assert models.domain_country["example.co.uk"] == ("GB", 1.0, 2)
