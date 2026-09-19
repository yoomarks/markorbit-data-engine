from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.fact_candidate_admission import fact_candidate_fingerprint_sha256_v1
from app.temporal_relationship_contract import TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
from app.us.citation_relation_admission import (
    ADMISSION_CONTRACT_VERSION,
    CitationRelationAdmissionError,
    TARGET_TABLE,
    admit_us_citation_relation,
)


NOW = datetime(2026, 9, 20, 6, 1, tzinfo=timezone.utc)


def candidate(*, status="VALIDATED", candidate_id="fac_01ARZ3NDEKTSV4RRFFQ69G5FAY"):
    value = {
        "contractVersion": "MARKORBIT_FACT_CANDIDATE_V1",
        "objectType": "FACT_CANDIDATE",
        "candidateId": candidate_id,
        "factType": "CITED_AS_REFERENCE_FOR_REFUSAL",
        "jurisdiction": "US",
        "subject": {
            "resourceType": "TRADEMARK",
            "applicationNumber": "99047647",
            "registrationNumber": None,
        },
        "object": {
            "resourceType": "TRADEMARK",
            "applicationNumber": None,
            "registrationNumber": "7265161",
        },
        "eventDate": "2026-07-22",
        "effectiveDate": None,
        "sourceDocument": {
            "owner": "MARKORBIT_KNOWLEDGE",
            "sourceId": "src_01M2X01CES1FAEVW3GWCC075J6",
            "documentId": "art_01M2XWP58XJ8SXW7XYBR3BA55Q",
            "documentVersion": 1,
            "documentSha256": (
                "eae8d5d049f3efef0b2e055d1b8e9ef4fdea38f1c9d14ab4703a39e081c013f3"
            ),
            "sourceUri": (
                "https://tsdrsec.uspto.gov/ts/cd/tmcasedoc/downloadproxy?"
                "url=/api/casedoc/cms/case/99047647/office-action/OfficeAction8740681.pdf"
            ),
        },
        "evidenceLocator": {
            "locatorType": "PAGE_TEXT_RANGE",
            "pageNumber": 3,
            "startOffset": 0,
            "endOffset": 128,
            "textSha256": "d" * 64,
        },
        "sourceAuthority": "DIRECT_OFFICIAL",
        "extractionMethod": {
            "owner": "MARKORBIT_BRAIN_METHOD",
            "methodId": "us-trademark-citation-extraction",
            "methodVersion": "1.1.0",
        },
        "confidence": {
            "scoreBasisPoints": 9900,
            "evidenceLevel": "EXPLICIT_DOCUMENT_TEXT",
        },
        "candidateFingerprintSha256": "",
        "ingestion": {
            "status": status,
            "validation": (
                {
                    "outcome": "ACCEPTED",
                    "validatorId": "citation-candidate-validator",
                    "validatorVersion": "1.0.0",
                    "decidedAt": "2026-09-20T06:00:00.000Z",
                    "reasonCode": None,
                }
                if status == "VALIDATED"
                else None
            ),
            "admission": None,
        },
        "producedAt": "2026-09-20T05:59:00.000Z",
        "legalConclusion": False,
    }
    value["candidateFingerprintSha256"] = fact_candidate_fingerprint_sha256_v1(value)
    return value


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self):
        self.rows = {}
        self.inserts = []

    def query(self, _sql, *, parameters):
        matches = []
        for fingerprint, row in self.rows.items():
            if "fingerprint" in parameters:
                matched = (
                    fingerprint == parameters["fingerprint"]
                    and row["source_resource_id"] == parameters["source_resource_id"]
                )
            else:
                matched = row["candidate_id"] == parameters["candidate_id"]
            if matched:
                matches.append(
                    (
                        row["candidate_id"],
                        fingerprint,
                        row["edge_id"],
                        row["contract_version"],
                    )
                )
        return Result(matches[:1])
    def insert(self, table, rows, *, column_names):
        assert table == TARGET_TABLE
        self.inserts.append((table, rows, column_names))
        data = dict(zip(column_names, rows[0], strict=True))
        self.rows[data["candidate_fingerprint_sha256"]] = data


def test_admits_us_temporal_edge_once_and_replays_stable_identity():
    client = Client()
    value = candidate()
    first = admit_us_citation_relation(value, client=client, now=NOW)
    replay = admit_us_citation_relation(value, client=client, now=NOW)

    assert ADMISSION_CONTRACT_VERSION == TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
    assert first.data_engine_fact_id.startswith("rel_")
    assert first.data_engine_contract_version == TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
    assert first.replayed is False
    assert replay == type(first)(
        first.data_engine_fact_id,
        TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
        True,
    )
    assert len(client.inserts) == 1
    stored = client.rows[value["candidateFingerprintSha256"]]
    assert stored["relationship_type"] == "CITED_AS_REFERENCE_FOR_REFUSAL"
    assert stored["source_resource_id"] == "US:99047647"
    assert stored["target_resource_id"] == "US:7265161"
    assert '"source_authority":"USPTO"' in stored["evidence_json"]
    assert '"source_domain":"US_TSDR"' in stored["evidence_json"]
    assert '"derivation_kind":"DOCUMENT_EXTRACTION"' in stored["provenance_json"]
    assert '"relationship_type":"CITED_AS_REFERENCE_FOR_REFUSAL"' in stored["fact_json"]


def test_rejects_proposed_candidate_before_persistence():
    client = Client()
    with pytest.raises(CitationRelationAdmissionError, match="VALIDATED"):
        admit_us_citation_relation(candidate(status="PROPOSED"), client=client)
    assert client.inserts == []


def test_rejects_fingerprint_mismatch_before_persistence():
    client = Client()
    value = candidate()
    value["candidateFingerprintSha256"] = "f" * 64
    with pytest.raises(CitationRelationAdmissionError, match="fingerprint"):
        admit_us_citation_relation(value, client=client)
    assert client.inserts == []


def test_rejects_business_or_unknown_fields_fail_closed():
    client = Client()
    value = candidate()
    value["workspaceId"] = "workspace-should-never-be-admitted"
    with pytest.raises(CitationRelationAdmissionError, match="fields are invalid"):
        admit_us_citation_relation(value, client=client)
    assert client.inserts == []


def test_same_fact_fingerprint_replays_even_with_new_candidate_id():
    client = Client()
    first_value = candidate()
    first = admit_us_citation_relation(first_value, client=client, now=NOW)

    replay_value = candidate(candidate_id="fac_01ARZ3NDEKTSV4RRFFQ69G5FAA")
    assert replay_value["candidateFingerprintSha256"] == first_value["candidateFingerprintSha256"]
    replay = admit_us_citation_relation(replay_value, client=client, now=NOW)

    assert replay.data_engine_fact_id == first.data_engine_fact_id
    assert replay.replayed is True
    assert len(client.inserts) == 1


def test_replay_detects_candidate_identity_fingerprint_conflict():
    client = Client()
    value = candidate()
    other = candidate()
    other["object"]["registrationNumber"] = "7265172"
    other["candidateFingerprintSha256"] = fact_candidate_fingerprint_sha256_v1(other)
    client.rows[other["candidateFingerprintSha256"]] = {
        "candidate_id": value["candidateId"],
        "edge_id": "rel_other",
        "contract_version": TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
    }

    with pytest.raises(CitationRelationAdmissionError, match="fingerprint conflict"):
        admit_us_citation_relation(value, client=client, now=NOW)


def test_rejects_non_us_candidate_on_us_owner_endpoint():
    client = Client()
    value = candidate()
    value["jurisdiction"] = "CN"
    value["candidateFingerprintSha256"] = fact_candidate_fingerprint_sha256_v1(value)

    with pytest.raises(CitationRelationAdmissionError, match="only US"):
        admit_us_citation_relation(value, client=client)
