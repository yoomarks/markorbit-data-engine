from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.temporal_relationship_contract import (
    AUTHORITY_LEVELS,
    QUERY_SCOPES,
    RELATIONSHIP_TYPES,
    RESOURCE_TYPES,
    TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
    RelationshipDerivationRef,
    RelationshipEvidenceRef,
    RelationshipResourceRef,
    TemporalRelationshipContractError,
    build_temporal_relationship_edge,
    temporal_relationship_contract,
)


FIXTURE = Path("tests/fixtures/temporal_relationship_edge_v1.json")
SCHEMA = Path("docs/integrations/markorbit/MARKORBIT_TEMPORAL_RELATIONSHIP_V1.schema.json")


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _build(value: dict[str, object]) -> dict[str, object]:
    return build_temporal_relationship_edge(
        jurisdiction=str(value["jurisdiction"]),
        relationship_type=str(value["relationship_type"]),
        source=RelationshipResourceRef(**value["source"]),
        target=RelationshipResourceRef(**value["target"]),
        authority_level=str(value["authority_level"]),
        evidence=RelationshipEvidenceRef(**value["evidence"]),
        observed_at=str(value["observed_at"]),
        is_current=value["is_current"],
        valid_from=value["valid_from"],
        valid_to=value["valid_to"],
        event_at=value["event_at"],
        derivation=RelationshipDerivationRef(**value["derivation"]),
    )


def test_machine_schema_and_runtime_registry_remain_aligned() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert schema["properties"]["contract_version"]["const"] == (
        TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
    )
    assert tuple(schema["$defs"]["resource"]["properties"]["resource_type"]["enum"]) == (
        RESOURCE_TYPES
    )
    assert tuple(schema["properties"]["relationship_type"]["enum"]) == RELATIONSHIP_TYPES
    assert (
        tuple(schema["properties"]["provenance"]["properties"]["authority_level"]["enum"])
        == AUTHORITY_LEVELS
    )


def test_direct_official_citation_fixture_has_reproducible_identity() -> None:
    value = _fixture()
    first = _build(value)
    second = _build(value)

    assert first == second
    assert first["contract_version"] == TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
    assert first["edge_id"].startswith("rel_")
    assert first["fingerprint"].startswith("sha256:")
    assert first["serving_authority"] == "DATA_ENGINE_FACT_READ_MODEL"
    assert first["legal_conclusion"] is False


def test_refusal_event_cannot_be_promoted_to_citation_edge() -> None:
    value = _fixture()
    value["evidence"] = {
        **value["evidence"],
        "evidence_kind": "STRUCTURED_SOURCE",
        "source_document_id": None,
        "source_document_version": None,
        "evidence_locator": None,
    }

    with pytest.raises(
        TemporalRelationshipContractError,
        match="direct OFFICIAL_DOCUMENT evidence",
    ):
        _build(value)


def test_citation_requires_versioned_document_extraction_identity() -> None:
    value = _fixture()
    value["derivation"] = {
        "derivation_kind": "DIRECT_MAPPING",
        "identity": "refusal-event-map",
        "version": "1.0.0",
    }

    with pytest.raises(
        TemporalRelationshipContractError,
        match="versioned DOCUMENT_EXTRACTION identity",
    ):
        _build(value)


def test_derived_history_and_inference_admission_are_distinct() -> None:
    evidence = RelationshipEvidenceRef(
        evidence_kind="STRUCTURED_SOURCE",
        source_authority="USPTO Assignment",
        source_domain="US_ASSIGNMENT",
        source_record_id="1234/5678",
    )
    derived = build_temporal_relationship_edge(
        jurisdiction="US",
        relationship_type="FORMER_OWNER",
        source=RelationshipResourceRef("OWNER", "entity:former-owner"),
        target=RelationshipResourceRef("TRADEMARK", "US:90000001"),
        authority_level="DERIVED_FROM_OFFICIAL_HISTORY",
        evidence=evidence,
        observed_at="2026-09-18T00:00:00Z",
        event_at="2020-01-01",
        is_current=False,
        derivation=RelationshipDerivationRef(
            "HISTORY_DERIVATION", "assignment-owner-chain", "1.0.0"
        ),
    )
    inferred = build_temporal_relationship_edge(
        jurisdiction="US",
        relationship_type="FORMER_OWNER",
        source=RelationshipResourceRef("ENTITY", "entity:probable-owner"),
        target=RelationshipResourceRef("TRADEMARK", "US:90000001"),
        authority_level="INFERRED",
        evidence=evidence,
        observed_at="2026-09-18T00:00:00Z",
        is_current=False,
        derivation=RelationshipDerivationRef(
            "METHOD_INFERENCE", "probable-owner-resolution", "1.0.0"
        ),
    )

    assert derived["serving_authority"] == "DATA_ENGINE_FACT_READ_MODEL"
    assert inferred["serving_authority"] == "METHOD_OUTPUT_ONLY"


def test_query_contract_is_bounded_and_fail_closed() -> None:
    contract = temporal_relationship_contract()

    assert tuple(contract["query"]["scopes"]) == QUERY_SCOPES
    assert contract["query"]["pagination"] == "KEYSET_CURSOR_BOUND_TO_QUERY_AND_SNAPSHOT"
    assert contract["query"]["unsupported_filter_behavior"] == "CAPABILITY_ERROR_FAIL_CLOSED"
    assert contract["query"]["arbitrary_sql"] is False
    assert contract["admission"]["refusal_event_alone_is_citation_evidence"] is False
    assert contract["ownership"]["documents"] == "MARKORBIT_KNOWLEDGE"


def test_current_edge_cannot_have_valid_to() -> None:
    value = _fixture()
    value["relationship_type"] = "CURRENT_OWNER"
    value["is_current"] = True
    value["valid_to"] = "2026-01-01"

    with pytest.raises(
        TemporalRelationshipContractError,
        match="current relationship must not have valid_to",
    ):
        _build(value)


def test_current_and_former_relationship_names_match_current_flag() -> None:
    value = _fixture()
    value["relationship_type"] = "CURRENT_OWNER"

    with pytest.raises(
        TemporalRelationshipContractError,
        match="CURRENT_OWNER requires is_current=true",
    ):
        _build(value)


def test_observed_at_requires_rfc3339_utc_timestamp() -> None:
    value = _fixture()
    value["observed_at"] = "2026-09-18"

    with pytest.raises(
        TemporalRelationshipContractError,
        match="RFC 3339 UTC timestamp ending in Z",
    ):
        _build(value)
