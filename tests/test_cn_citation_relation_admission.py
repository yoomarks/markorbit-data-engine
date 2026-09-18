from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.cn.citation_relation_admission import (
    ADMISSION_CONTRACT_VERSION,
    CitationRelationAdmissionError,
    TARGET_TABLE,
    admit_cn_citation_relation,
)
from app.fact_candidate_admission import fact_candidate_fingerprint_sha256_v1
from app.temporal_relationship_contract import TEMPORAL_RELATIONSHIP_CONTRACT_VERSION


def candidate(*, status="VALIDATED", candidate_id="fac_01ARZ3NDEKTSV4RRFFQ69G5FAY"):
    value = {
        "contractVersion": "MARKORBIT_FACT_CANDIDATE_V1",
        "objectType": "FACT_CANDIDATE",
        "candidateId": candidate_id,
        "factType": "CITED_AS_REFERENCE_FOR_REFUSAL",
        "jurisdiction": "CN",
        "subject": {
            "resourceType": "TRADEMARK",
            "applicationNumber": "202312345678.9",
            "registrationNumber": None,
        },
        "object": {
            "resourceType": "TRADEMARK",
            "applicationNumber": None,
            "registrationNumber": "12345678",
        },
        "eventDate": "2026-08-21",
        "effectiveDate": None,
        "sourceDocument": {
            "owner": "MARKORBIT_KNOWLEDGE",
            "sourceId": "src_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "documentId": "std_01ARZ3NDEKTSV4RRFFQ69G5FAZ",
            "documentVersion": 2,
            "documentSha256": "c" * 64,
            "sourceUri": "https://pub.sbj.cnipa.gov.cn/document/example",
        },
        "evidenceLocator": {
            "locatorType": "CANONICAL_TEXT_RANGE",
            "startOffset": 188,
            "endOffset": 210,
            "textSha256": "d" * 64,
        },
        "sourceAuthority": "DIRECT_OFFICIAL",
        "extractionMethod": {
            "owner": "MARKORBIT_BRAIN_METHOD",
            "methodId": "cn-trademark-citation-extraction",
            "methodVersion": "1.0.0",
        },
        "confidence": {
            "scoreBasisPoints": 9800,
            "evidenceLevel": "EXPLICIT_DOCUMENT_TEXT",
        },
        "candidateFingerprintSha256": "",
        "ingestion": {
            "status": status,
            "validation": (
                {
                    "outcome": "ACCEPTED",
                    "validatorId": "citation-validator",
                    "validatorVersion": "1.0.0",
                    "decidedAt": "2026-09-18T16:00:00.000Z",
                    "reasonCode": None,
                }
                if status == "VALIDATED"
                else None
            ),
            "admission": None,
        },
        "producedAt": "2026-09-18T15:50:00.000Z",
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
            if (
                fingerprint == parameters["fingerprint"]
                or row["candidate_id"] == parameters["candidate_id"]
            ):
                matches.append(
                    (
                        row["candidate_id"],
                        fingerprint,
                        row["edge_id"],
                        row["contract_version"],
                    )
                )
        return Result(matches[:2])

    def insert(self, table, rows, *, column_names):
        assert table == TARGET_TABLE
        self.inserts.append((table, rows, column_names))
        data = dict(zip(column_names, rows[0], strict=True))
        self.rows[data["candidate_fingerprint_sha256"]] = data


def test_admits_foundation_temporal_edge_once_and_replays_stable_identity():
    client = Client()
    value = candidate()
    now = datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)

    first = admit_cn_citation_relation(value, client=client, now=now)
    replay = admit_cn_citation_relation(value, client=client, now=now)

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
    assert stored["source_resource_id"] == "CN:202312345678.9"
    assert stored["target_resource_id"] == "CN:12345678"
    assert '"source_authority":"CNIPA"' in stored["evidence_json"]
    assert '"derivation_kind":"DOCUMENT_EXTRACTION"' in stored["provenance_json"]


def test_rejects_proposed_candidate_before_persistence():
    client = Client()
    with pytest.raises(CitationRelationAdmissionError, match="VALIDATED"):
        admit_cn_citation_relation(candidate(status="PROPOSED"), client=client)
    assert client.inserts == []


def test_rejects_fingerprint_mismatch_before_persistence():
    client = Client()
    value = candidate()
    value["candidateFingerprintSha256"] = "f" * 64
    with pytest.raises(CitationRelationAdmissionError, match="fingerprint"):
        admit_cn_citation_relation(value, client=client)
    assert client.inserts == []


def test_rejects_business_or_unknown_fields_fail_closed():
    client = Client()
    value = candidate()
    value["workspaceId"] = "workspace-should-never-be-admitted"
    with pytest.raises(CitationRelationAdmissionError, match="fields are invalid"):
        admit_cn_citation_relation(value, client=client)
    assert client.inserts == []


def test_same_fact_fingerprint_replays_even_with_new_candidate_id():
    client = Client()
    first_value = candidate()
    first = admit_cn_citation_relation(first_value, client=client)

    replay_value = candidate(candidate_id="fac_01ARZ3NDEKTSV4RRFFQ69G5FAA")
    assert replay_value["candidateFingerprintSha256"] == first_value["candidateFingerprintSha256"]
    replay = admit_cn_citation_relation(replay_value, client=client)

    assert replay.data_engine_fact_id == first.data_engine_fact_id
    assert replay.replayed is True
    assert len(client.inserts) == 1


def test_replay_detects_candidate_identity_fingerprint_conflict():
    client = Client()
    value = candidate()
    other = candidate()
    other["object"]["registrationNumber"] = "87654321"
    other["candidateFingerprintSha256"] = fact_candidate_fingerprint_sha256_v1(other)
    client.rows[other["candidateFingerprintSha256"]] = {
        "candidate_id": value["candidateId"],
        "edge_id": "rel_other",
        "contract_version": TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
    }

    with pytest.raises(CitationRelationAdmissionError, match="fingerprint conflict"):
        admit_cn_citation_relation(value, client=client)


def test_rejects_non_cn_candidate_on_cn_owner_endpoint():
    client = Client()
    value = candidate()
    value["jurisdiction"] = "US"
    value["candidateFingerprintSha256"] = fact_candidate_fingerprint_sha256_v1(value)

    with pytest.raises(CitationRelationAdmissionError, match="only CN"):
        admit_cn_citation_relation(value, client=client)
