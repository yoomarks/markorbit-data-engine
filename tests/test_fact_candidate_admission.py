from __future__ import annotations

import copy

import pytest

from app.fact_candidate_admission import (
    FactCandidateAdmissionError,
    admit_fact_candidate_v1,
    fact_candidate_fingerprint_sha256_v1,
)
from app.temporal_relationship_contract import TEMPORAL_RELATIONSHIP_CONTRACT_VERSION


def _validated_us_candidate() -> dict[str, object]:
    return {
        "contractVersion": "MARKORBIT_FACT_CANDIDATE_V1",
        "objectType": "FACT_CANDIDATE",
        "candidateId": "fac_01ARZ3NDEKTSV4RRFFQ69G5FAX",
        "factType": "CITED_AS_REFERENCE_FOR_REFUSAL",
        "jurisdiction": "US",
        "subject": {
            "resourceType": "TRADEMARK",
            "applicationNumber": "97123456",
            "registrationNumber": None,
        },
        "object": {
            "resourceType": "TRADEMARK",
            "applicationNumber": None,
            "registrationNumber": "6123456",
        },
        "eventDate": "2026-08-20",
        "effectiveDate": None,
        "sourceDocument": {
            "owner": "MARKORBIT_KNOWLEDGE",
            "sourceId": "src_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "documentId": "std_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "documentVersion": 1,
            "documentSha256": "a" * 64,
            "sourceUri": "https://tsdr.uspto.gov/document/97123456/example",
        },
        "evidenceLocator": {
            "locatorType": "PAGE_TEXT_RANGE",
            "pageNumber": 3,
            "startOffset": 412,
            "endOffset": 507,
            "textSha256": "b" * 64,
        },
        "sourceAuthority": "DIRECT_OFFICIAL",
        "extractionMethod": {
            "owner": "MARKORBIT_BRAIN_METHOD",
            "methodId": "us-trademark-citation-extraction",
            "methodVersion": "1.0.0",
        },
        "confidence": {
            "scoreBasisPoints": 9900,
            "evidenceLevel": "EXPLICIT_DOCUMENT_TEXT",
        },
        "candidateFingerprintSha256": (
            "0743f5b7bf8efab7fbb2d63c356e8f69b9b0333cd7370734c70bc7ec622550cb"
        ),
        "ingestion": {
            "status": "VALIDATED",
            "validation": {
                "outcome": "ACCEPTED",
                "validatorId": "citation-candidate-validator",
                "validatorVersion": "1.0.0",
                "decidedAt": "2026-09-18T00:01:00.000Z",
                "reasonCode": None,
            },
            "admission": None,
        },
        "producedAt": "2026-09-18T00:00:00.000Z",
        "legalConclusion": False,
    }


def test_shared_us_fixture_fingerprint_reproduces() -> None:
    candidate = _validated_us_candidate()
    assert (
        fact_candidate_fingerprint_sha256_v1(candidate)
        == "0743f5b7bf8efab7fbb2d63c356e8f69b9b0333cd7370734c70bc7ec622550cb"
    )


def test_validated_citation_maps_to_canonical_temporal_relationship() -> None:
    candidate = _validated_us_candidate()
    result = admit_fact_candidate_v1(
        candidate,
        decided_at="2026-09-18T00:02:00.000Z",
    )

    fact = result["fact"]
    assert fact["contract_version"] == TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
    assert fact["relationship_type"] == "CITED_AS_REFERENCE_FOR_REFUSAL"
    assert fact["source"] == {"resource_type": "TRADEMARK", "resource_id": "US:97123456"}
    assert fact["target"] == {"resource_type": "TRADEMARK", "resource_id": "US:6123456"}
    assert fact["provenance"]["authority_level"] == "DIRECT_OFFICIAL"
    assert fact["provenance"]["derivation"] == {
        "derivation_kind": "DOCUMENT_EXTRACTION",
        "identity": "us-trademark-citation-extraction",
        "version": "1.0.0",
    }
    assert fact["evidence"]["evidence_kind"] == "OFFICIAL_DOCUMENT"
    assert fact["evidence"]["source_domain"] == "US_TSDR"
    assert fact["evidence"]["source_document_id"] == "std_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert fact["evidence"]["source_document_version"] == "v1"
    assert fact["evidence"]["source_hash"] == f"sha256:{'a' * 64}"
    assert fact["evidence"]["evidence_locator"] == (
        f"PAGE_TEXT_RANGE:page=3;start=412;end=507;text_sha256={'b' * 64}"
    )
    assert fact["temporal"]["event_at"] == "2026-08-20"
    assert fact["legal_conclusion"] is False

    admission = result["admission"]
    assert admission["outcome"] == "ADMITTED"
    assert admission["dataEngineFactId"] == fact["edge_id"]
    assert admission["dataEngineContractVersion"] == TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
    assert result["candidate"]["ingestion"]["status"] == "ADMITTED"
    assert candidate["ingestion"]["status"] == "VALIDATED"


def test_proposed_candidate_cannot_skip_validation() -> None:
    candidate = _validated_us_candidate()
    candidate["ingestion"] = {
        "status": "PROPOSED",
        "validation": None,
        "admission": None,
    }

    with pytest.raises(FactCandidateAdmissionError, match="requires VALIDATED candidate"):
        admit_fact_candidate_v1(candidate, decided_at="2026-09-18T00:02:00.000Z")


def test_fingerprint_tampering_fails_closed() -> None:
    candidate = _validated_us_candidate()
    candidate["object"]["registrationNumber"] = "7654321"

    with pytest.raises(FactCandidateAdmissionError, match="fingerprint does not reproduce"):
        admit_fact_candidate_v1(candidate, decided_at="2026-09-18T00:02:00.000Z")


def test_ambiguous_trademark_identity_fails_closed() -> None:
    candidate = _validated_us_candidate()
    candidate["object"]["applicationNumber"] = "98765432"
    candidate["candidateFingerprintSha256"] = fact_candidate_fingerprint_sha256_v1(candidate)

    with pytest.raises(FactCandidateAdmissionError, match="exactly one"):
        admit_fact_candidate_v1(candidate, decided_at="2026-09-18T00:02:00.000Z")


def test_admission_cannot_precede_validation() -> None:
    candidate = _validated_us_candidate()

    with pytest.raises(FactCandidateAdmissionError, match="cannot precede validation"):
        admit_fact_candidate_v1(candidate, decided_at="2026-09-18T00:00:30.000Z")


def test_unknown_candidate_fields_fail_closed() -> None:
    candidate = copy.deepcopy(_validated_us_candidate())
    candidate["unexpected"] = True

    with pytest.raises(FactCandidateAdmissionError, match="fields are invalid"):
        admit_fact_candidate_v1(candidate, decided_at="2026-09-18T00:02:00.000Z")
