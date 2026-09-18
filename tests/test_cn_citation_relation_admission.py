from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from app.cn.citation_relation_admission import (
    ADMISSION_CONTRACT_VERSION,
    CitationRelationAdmissionError,
    TARGET_TABLE,
    admit_cn_citation_relation,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def candidate(*, status="VALIDATED", object_application=None, object_registration="12345678"):
    value = {
        "contractVersion": "MARKORBIT_FACT_CANDIDATE_V1",
        "objectType": "FACT_CANDIDATE",
        "candidateId": "fac_01ARZ3NDEKTSV4RRFFQ69G5FAY",
        "factType": "CITED_AS_REFERENCE_FOR_REFUSAL",
        "jurisdiction": "CN",
        "subject": {
            "resourceType": "TRADEMARK",
            "applicationNumber": "202312345678.9",
            "registrationNumber": None,
        },
        "object": {
            "resourceType": "TRADEMARK",
            "applicationNumber": object_application,
            "registrationNumber": object_registration,
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
    material = {
        key: value[key]
        for key in (
            "contractVersion",
            "objectType",
            "factType",
            "jurisdiction",
            "subject",
            "object",
            "eventDate",
            "effectiveDate",
            "sourceDocument",
            "evidenceLocator",
            "sourceAuthority",
            "extractionMethod",
            "confidence",
            "legalConclusion",
        )
    }
    value["candidateFingerprintSha256"] = sha256(canonical(material).encode()).hexdigest()
    return value


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class Client:
    def __init__(self):
        self.rows = {}
        self.inserts = []

    def query(self, _sql, *, parameters):
        fingerprint = parameters["fingerprint"]
        candidate_id = parameters["candidate_id"]
        matches = [
            (row["candidate_id"], stored_fingerprint)
            for stored_fingerprint, row in self.rows.items()
            if stored_fingerprint == fingerprint or row["candidate_id"] == candidate_id
        ]
        return Result(matches[:2])

    def insert(self, table, rows, *, column_names):
        assert table == TARGET_TABLE
        self.inserts.append((table, rows, column_names))
        data = dict(zip(column_names, rows[0], strict=True))
        self.rows[data["candidate_fingerprint_sha256"]] = data


def test_admits_validated_cn_citation_once_and_replays_stable_identity():
    client = Client()
    value = candidate()
    now = datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)

    first = admit_cn_citation_relation(value, client=client, now=now)
    replay = admit_cn_citation_relation(value, client=client, now=now)

    assert first.data_engine_contract_version == ADMISSION_CONTRACT_VERSION
    assert first.data_engine_fact_id.startswith("cn_citation_")
    assert first.replayed is False
    assert replay == type(first)(first.data_engine_fact_id, ADMISSION_CONTRACT_VERSION, True)
    assert len(client.inserts) == 1
    stored = client.rows[value["candidateFingerprintSha256"]]
    assert stored["object_application_number"] == ""
    assert stored["object_registration_number"] == "12345678"
    assert stored["extraction_method_id"] == "cn-trademark-citation-extraction"


def test_accepts_explicit_cited_application_number():
    client = Client()
    value = candidate(object_application="202355555555.5", object_registration=None)

    admit_cn_citation_relation(value, client=client)

    stored = client.rows[value["candidateFingerprintSha256"]]
    assert stored["object_application_number"] == "202355555555.5"
    assert stored["object_registration_number"] == ""


def test_rejects_proposed_candidate_before_persistence():
    client = Client()
    with pytest.raises(CitationRelationAdmissionError, match="VALIDATED"):
        admit_cn_citation_relation(candidate(status="PROPOSED"), client=client)
    assert client.inserts == []


def test_rejects_fingerprint_mismatch_before_persistence():
    client = Client()
    value = candidate()
    value["candidateFingerprintSha256"] = "f" * 64
    with pytest.raises(CitationRelationAdmissionError, match="fingerprint mismatch"):
        admit_cn_citation_relation(value, client=client)
    assert client.inserts == []


def test_rejects_business_or_unknown_fields_fail_closed():
    client = Client()
    value = candidate()
    value["workspaceId"] = "workspace-should-never-be-admitted"
    with pytest.raises(CitationRelationAdmissionError, match="shape is invalid"):
        admit_cn_citation_relation(value, client=client)
    assert client.inserts == []


def test_same_fact_fingerprint_replays_even_with_new_candidate_id():
    client = Client()
    value = candidate()
    first = admit_cn_citation_relation(value, client=client)

    replay_value = candidate()
    replay_value["candidateId"] = "fac_01ARZ3NDEKTSV4RRFFQ69G5FAA"
    replay = admit_cn_citation_relation(replay_value, client=client)

    assert replay.data_engine_fact_id == first.data_engine_fact_id
    assert replay.replayed is True
    assert len(client.inserts) == 1


def test_replay_detects_candidate_identity_fingerprint_conflict():
    client = Client()
    value = candidate()
    different = candidate(object_application="202355555555.5", object_registration=None)
    client.rows[different["candidateFingerprintSha256"]] = {
        "candidate_id": value["candidateId"]
    }

    with pytest.raises(CitationRelationAdmissionError, match="fingerprint conflict"):
        admit_cn_citation_relation(value, client=client)
