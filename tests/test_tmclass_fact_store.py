from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy

import pytest

from app.tmclass import api, repository
from app.tmclass.contract import CONTRACT_VERSION, TmclassAdmissionError, normalize
from app.tmclass.schema import SCHEMA_SQL


ARTIFACT_ID = "art_01ARZ3NDEKTSV4RRFFQ69G5FAV"


def envelope(page: dict) -> dict:
    return {
        "contractVersion": CONTRACT_VERSION,
        "objectType": "TMCLASS_SOURCE_EVIDENCE",
        "sourceOwner": "MARKORBIT_KNOWLEDGE",
        "sourceId": "EUIPO_TMCLASS",
        "observedAt": "2026-10-10T03:04:14.000Z",
        "evidence": {
            "workspaceId": "global-public",
            "sourceDefinitionId": "src_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "collectionRunId": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "rawArtifactId": ARTIFACT_ID,
            "artifactVersion": 1,
            "canonicalUri": "markorbit://knowledge/tmclass/artifacts/" + ARTIFACT_ID,
            "sourceUri": (
                "https://euipo.europa.eu/ec2/term/262"
                if page["pageKind"] == "TERM"
                else "https://euipo.europa.eu/ec2/concept/"
                + page["conceptId"]
                + ("/" + page["languageCode"] if page["pageKind"] == "CONCEPT_LANGUAGE" else "")
            ),
            "sha256": "a" * 64,
        },
        "page": page,
    }


def term_page() -> dict:
    return {
        "pageKind": "TERM",
        "termId": "262",
        "text": "Abrasives (Auxiliary fluids for use with -)",
        "niceClass": 1,
        "languageCode": "en",
        "languageLabel": "English",
        "acceptedBy": [
            {"name": "European Union Intellectual Property Office", "code": "EUIPO"},
            {"name": "Israel Patent Office", "code": "ILPO"},
        ],
        "taxonomy": [
            {"label": "Class 1", "sourceNodeId": None},
            {
                "label": "Chemical substances, chemical materials and chemical preparations",
                "sourceNodeId": None,
            },
        ],
        "translationTargets": [
            {
                "termId": "242746758",
                "languageCode": "ja",
                "niceClass": 1,
                "text": "研磨用補助液",
                "quality": "Terminology",
            }
        ],
        "sources": [
            {"conceptId": "19896306", "sourceName": "Harmonized", "referenceId": "0024010"},
            {
                "conceptId": "11452493",
                "sourceName": "ILPO Supplement",
                "referenceId": "0024010",
            },
            {
                "conceptId": "17729293",
                "sourceName": "Nice (IPONZ)",
                "referenceId": "0024010",
            },
        ],
    }


def concept_language_page() -> dict:
    return {
        "pageKind": "CONCEPT_LANGUAGE",
        "conceptId": "11452493",
        "title": "Abrasives (Auxiliary fluids for use with -)",
        "status": "Published",
        "niceClass": 1,
        "sourceName": "ILPO Supplement",
        "sourceDateText": None,
        "referenceId": "0024010",
        "scopeStatus": "Accepted",
        "taxonomy": [{"label": "Class 1", "sourceNodeId": None}],
        "languageCode": "en",
        "terms": [
            {
                "termId": "262",
                "text": "Abrasives (Auxiliary fluids for use with -)",
                "role": "MASTER",
                "ordinal": 1,
            },
            {
                "termId": "263",
                "text": "Fluids for use with abrasives (Auxiliary -)",
                "role": "VARIANT",
                "ordinal": 2,
            },
        ],
    }


def test_contract_keeps_term_acceptance_translation_source_and_taxonomy_facts():
    normalized = normalize(envelope(term_page()))
    assert normalized.page["acceptedBy"][1]["code"] == "ILPO"
    assert normalized.page["translationTargets"][0]["termId"] == "242746758"
    assert [item["sourceName"] for item in normalized.page["sources"]] == [
        "Harmonized",
        "ILPO Supplement",
        "Nice (IPONZ)",
    ]
    assert normalized.page["taxonomy"][1]["label"].startswith("Chemical substances")


def test_contract_keeps_master_and_variant_as_independent_terms_on_one_concept():
    normalized = normalize(envelope(concept_language_page()))
    assert [(item["termId"], item["role"]) for item in normalized.page["terms"]] == [
        ("262", "MASTER"),
        ("263", "VARIANT"),
    ]
    assert normalized.page["sourceName"] == "ILPO Supplement"
    assert normalized.page["referenceId"] == "0024010"


def test_contract_rejects_uri_identity_mismatch_and_missing_master():
    wrong_uri = envelope(term_page())
    wrong_uri["evidence"]["sourceUri"] = "https://euipo.europa.eu/ec2/term/263"
    with pytest.raises(TmclassAdmissionError, match="sourceUri"):
        normalize(wrong_uri)

    no_master = concept_language_page()
    no_master["terms"][0]["role"] = "VARIANT"
    with pytest.raises(TmclassAdmissionError, match="exactly one MASTER"):
        normalize(envelope(no_master))


def test_schema_models_source_scoped_reference_and_every_capability_relation():
    compact = " ".join(SCHEMA_SQL.split())
    assert "UNIQUE (source_name, reference_id)" in compact
    for relation in (
        "tmclass.source_evidence",
        "tmclass.office",
        "tmclass.term",
        "tmclass.concept",
        "tmclass.taxonomy_node",
        "tmclass.term_office_acceptance",
        "tmclass.term_translation",
        "tmclass.concept_language",
        "tmclass.concept_term",
    ):
        assert relation in SCHEMA_SQL
    assert "'MASTER', 'VARIANT', 'UNKNOWN'" in SCHEMA_SQL


class RecordingCursor:
    def __init__(self, existing: dict | None = None):
        self.calls: list[tuple[str, tuple | None]] = []
        self.existing = existing
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple | None = None):
        self._last_sql = " ".join(sql.split())
        self.calls.append((self._last_sql, params))

    def fetchone(self):
        if "FROM tmclass.source_evidence WHERE raw_artifact_id" in self._last_sql:
            return self.existing
        return None


class RecordingConnection:
    def __init__(self, existing: dict | None = None):
        self.cursor_value = RecordingCursor(existing)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


def test_repository_writes_all_term_relations_in_one_transaction(monkeypatch):
    monkeypatch.setattr(repository, "assert_tmclass_schema", lambda: None)
    connection = RecordingConnection()

    @contextmanager
    def factory():
        yield connection

    receipt = repository.admit_tmclass_evidence(envelope(term_page()), connection_factory=factory)
    sql = "\n".join(call[0] for call in connection.cursor_value.calls)
    assert connection.commits == 1
    assert receipt["facts"] == {
        "terms": 2,
        "concepts": 3,
        "offices": 2,
        "translations": 1,
        "memberships": 3,
    }
    for relation in (
        "tmclass.source_evidence",
        "tmclass.term_office_acceptance",
        "tmclass.term_translation",
        "tmclass.concept_term",
        "tmclass.term_taxonomy",
    ):
        assert relation in sql


def test_repository_persists_concept_language_roles_and_replays_exact_artifact(monkeypatch):
    monkeypatch.setattr(repository, "assert_tmclass_schema", lambda: None)
    connection = RecordingConnection()

    @contextmanager
    def factory():
        yield connection

    receipt = repository.admit_tmclass_evidence(
        envelope(concept_language_page()), connection_factory=factory
    )
    membership_params = [
        params
        for sql, params in connection.cursor_value.calls
        if "INSERT INTO tmclass.concept_term" in sql
    ]
    assert receipt["facts"]["memberships"] == 2
    assert [params[3] for params in membership_params] == ["MASTER", "VARIANT"]

    replay = RecordingConnection({"sha256": "a" * 64, "page_kind": "CONCEPT_LANGUAGE"})

    @contextmanager
    def replay_factory():
        yield replay

    replayed = repository.admit_tmclass_evidence(
        envelope(concept_language_page()), connection_factory=replay_factory
    )
    assert replayed["replayed"] is True
    assert replay.commits == 0


def test_admission_router_maps_contract_rejection_and_storage_failure(monkeypatch):
    monkeypatch.setattr(
        api,
        "admit_tmclass_evidence",
        lambda _package: (_ for _ in ()).throw(TmclassAdmissionError("bad evidence")),
    )
    with pytest.raises(Exception) as rejected:
        api.admit_evidence({})
    assert rejected.value.status_code == 400
    assert rejected.value.detail["code"] == "TMCLASS_EVIDENCE_REJECTED"

    monkeypatch.setattr(
        api,
        "admit_tmclass_evidence",
        lambda _package: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    with pytest.raises(Exception) as unavailable:
        api.admit_evidence({})
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["code"] == "TMCLASS_FACT_STORE_UNAVAILABLE"


def test_same_reference_id_is_valid_for_different_sources():
    first = normalize(envelope(concept_language_page()))
    second_page = deepcopy(concept_language_page())
    second_page["conceptId"] = "17729293"
    second_page["sourceName"] = "Nice (IPONZ)"
    second_page["terms"][0]["termId"] = "264"
    second_page["terms"][0]["text"] = "Auxiliary fluids for use with abrasives"
    second_page["terms"][1]["termId"] = "265"
    second = normalize(envelope(second_page))
    assert first.page["referenceId"] == second.page["referenceId"] == "0024010"
    assert first.page["sourceName"] != second.page["sourceName"]
