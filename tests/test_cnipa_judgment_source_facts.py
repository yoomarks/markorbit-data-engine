from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.cnipa_judgment.migrations import SCHEMA_SQL
from app.cnipa_judgment.model import CnipaJudgmentWindowObservation
from app.cnipa_judgment.parser import (
    cnipa_fact_event_envelope,
    parse_cnipa_list_fact_projection,
)
import app.cnipa_judgment.repository as repository


OBSERVED_AT = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)
ARTIFACT = "knowledge:raw-artifact:list-1"


def _projection(kind: str, source_id: str, fields: dict) -> dict:
    return {
        "schemaVersion": "cnipa-list-fact-projection-v1",
        "jurisdiction": "CN",
        "sourceAuthority": "CNIPA",
        "documentKind": kind,
        "sourceRecordId": source_id,
        "sourceFields": fields,
        "sourceRowSha256": "a" * 64,
    }


@pytest.mark.parametrize(
    ("kind", "source_id", "fields", "detail_path", "family"),
    [
        (
            "REGISTRATION_EXAMINATION",
            "exam-1",
            {
                "adjuOpenId": "exam-1",
                "applyNo": "A100",
                "regNo": "R100",
                "tmName": "MARK A",
                "adjuTitle": "注册审查决定",
                "returnDateStr": "2026-09-18",
                "sendNoStr": "DOC-100",
                "citeTmRegNo": "CITED-100",
                "applicantCnName": "申请人甲",
                "agentInstName": "代理机构甲",
            },
            "tmscJudgment",
            "EXAMINATION",
        ),
        (
            "OPPOSITION_DECISION",
            "opp-1",
            {
                "adjuOpenId": "opp-1",
                "applyNo": "A200",
                "regNo": "R200",
                "tmName": "MARK B",
                "adjuTitle": "异议决定",
                "returnDateStr": "2026-09-17",
                "snedNoStr": "DOC-200",
                "citeTms": "R201,R202",
                "objenderCnName": "异议人",
                "objeperCnName": "被异议人",
                "objenderAgentName": "异议代理",
                "objeperAgentName": "被异议代理",
            },
            "tmyyJudgment",
            "PROCEEDING",
        ),
        (
            "REVIEW_ADJUDICATION",
            "review-1",
            {
                "pubId": "review-1",
                "applyNo": "A300",
                "regNo": "R300",
                "tmName": "MARK C",
                "fileTitle": "评审裁定",
                "judgeDateStr": "2026-09-16",
                "sendDocNo": "DOC-300",
                "applicantName": "申请人乙",
                "respondentName": "被申请人乙",
                "agentInstName": "代理机构乙",
            },
            "tmpsJudgment",
            "PROCEEDING",
        ),
    ],
)
def test_parses_all_three_knowledge_list_projection_shapes(
    kind, source_id, fields, detail_path, family
):
    detail_uri = (
        "https://pub.sbj.cnipa.gov.cn/toas-pub-prod/pub-prod-api/pubnotice/portal/"
        f"{detail_path}/queryInfo?id={source_id}"
    )
    fact = parse_cnipa_list_fact_projection(
        _projection(kind, source_id, fields),
        detail_canonical_uri=detail_uri,
        observed_at=OBSERVED_AT,
        source_artifact_ref=ARTIFACT,
        initial_markdown_ref="knowledge:markdown:initial",
        initial_markdown_sha256="b" * 64,
    )

    assert fact.document_kind == kind
    assert fact.source_record_id == source_id
    assert fact.semantic_family == family
    assert fact.detail_canonical_uri == detail_uri
    assert fact.application_number == fields["applyNo"]
    assert fact.registration_number == fields["regNo"]
    assert fact.trademark_name == fields["tmName"]
    assert fact.source_fields == fields

    envelope = cnipa_fact_event_envelope(fact)
    assert envelope["authority"] == "DATA_ENGINE_SOURCE_FACT"
    assert envelope["legal_conclusion"] is False
    assert envelope["normalization"]["cross_jurisdiction_legal_equivalence"] is False
    assert envelope["provenance"]["source_row_hash"] == "a" * 64
    assert envelope["provenance"]["source_file"] == ARTIFACT
    assert "markdownBody" not in envelope["payload"]
    assert "detailBody" not in envelope["payload"]


def test_parser_rejects_identity_or_detail_uri_mismatch():
    projection = _projection(
        "OPPOSITION_DECISION",
        "opp-1",
        {"adjuOpenId": "different", "regNo": "R1"},
    )
    with pytest.raises(ValueError, match="sourceRecordId"):
        parse_cnipa_list_fact_projection(
            projection,
            detail_canonical_uri=(
                "https://pub.sbj.cnipa.gov.cn/toas-pub-prod/pub-prod-api/"
                "pubnotice/portal/tmyyJudgment/queryInfo?id=opp-1"
            ),
            observed_at=OBSERVED_AT,
            source_artifact_ref=ARTIFACT,
        )

    projection["sourceFields"]["adjuOpenId"] = "opp-1"
    with pytest.raises(ValueError, match="detail_canonical_uri"):
        parse_cnipa_list_fact_projection(
            projection,
            detail_canonical_uri="https://example.test/wrong",
            observed_at=OBSERVED_AT,
            source_artifact_ref=ARTIFACT,
        )


class _Cursor:
    def __init__(self, store):
        self.store = store
        self.rowcount = 0
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.rowcount = 0
        self._row = None

        if normalized.startswith("INSERT INTO cnipa_judgment.window_observation"):
            key = params[0]
            if key not in self.store["windows"]:
                self.store["windows"][key] = params
                self.rowcount = 1
            return

        if normalized.startswith("SELECT observed_at, source_row_sha256"):
            current = self.store["current"].get((params[0], params[1]))
            self._row = (
                {
                    "observed_at": current["observed_at"],
                    "source_row_sha256": current["source_row_sha256"],
                }
                if current
                else None
            )
            return

        if normalized.startswith("INSERT INTO cnipa_judgment.list_observation"):
            key = params[0]
            if key not in self.store["observations"]:
                self.store["observations"][key] = params
                self.rowcount = 1
            return

        if normalized.startswith("INSERT INTO cnipa_judgment.current"):
            key = (params[0], params[1])
            incoming = {
                "observation_key": params[2],
                "source_row_sha256": params[5],
                "observed_at": params[6],
                "source_artifact_ref": params[7],
                "source_fields_json": params[8],
            }
            existing = self.store["current"].get(key)
            if (
                existing is None
                or incoming["observed_at"] > existing["observed_at"]
                or (
                    incoming["observed_at"] == existing["observed_at"]
                    and incoming["observation_key"] == existing["observation_key"]
                )
            ):
                self.store["current"][key] = incoming
                self.rowcount = 1
            return

        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, store):
        self.store = store
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self.store)

    def commit(self):
        self.commits += 1


def _fake_postgres(monkeypatch):
    store = {"windows": {}, "observations": {}, "current": {}}
    connection = _Connection(store)

    @contextmanager
    def connect():
        yield connection

    monkeypatch.setattr(repository, "postgres_conn", connect)
    return store, connection


def _fact(
    source_id="opp-1",
    *,
    observed_at=OBSERVED_AT,
    row_hash="a" * 64,
    artifact=ARTIFACT,
):
    projection = _projection(
        "OPPOSITION_DECISION",
        source_id,
        {
            "adjuOpenId": source_id,
            "applyNo": "A200",
            "regNo": "R200",
            "tmName": "MARK B",
            "adjuTitle": "异议决定",
            "returnDateStr": "2026-09-17",
        },
    )
    projection["sourceRowSha256"] = row_hash
    return parse_cnipa_list_fact_projection(
        projection,
        detail_canonical_uri=(
            "https://pub.sbj.cnipa.gov.cn/toas-pub-prod/pub-prod-api/"
            f"pubnotice/portal/tmyyJudgment/queryInfo?id={source_id}"
        ),
        observed_at=observed_at,
        source_artifact_ref=artifact,
    )


def _window(*, observed_at=OBSERVED_AT, artifact=ARTIFACT, count=1):
    return CnipaJudgmentWindowObservation(
        document_kind="OPPOSITION_DECISION",
        query_from=date(2026, 9, 17),
        query_to=date(2026, 9, 18),
        observed_at=observed_at,
        source_artifact_ref=artifact,
        record_count=count,
    )


def test_replay_is_idempotent_and_provenance_survives_current_projection(monkeypatch):
    store, connection = _fake_postgres(monkeypatch)
    fact = _fact()
    window = _window()

    first = repository.ingest_cnipa_judgment_window(window, [fact])
    second = repository.ingest_cnipa_judgment_window(window, [fact])

    assert first["inserted_observations"] == 1
    assert second["inserted_observations"] == 0
    assert len(store["observations"]) == 1
    assert len(store["windows"]) == 1
    current = store["current"][("OPPOSITION_DECISION", "opp-1")]
    assert current["source_row_sha256"] == "a" * 64
    assert current["source_artifact_ref"] == ARTIFACT
    assert connection.commits == 2


def test_older_replay_enters_history_without_replacing_current(monkeypatch):
    store, _ = _fake_postgres(monkeypatch)
    newer_time = OBSERVED_AT + timedelta(hours=1)
    repository.ingest_cnipa_judgment_window(
        _window(observed_at=newer_time, artifact="artifact:new"),
        [_fact(observed_at=newer_time, row_hash="b" * 64, artifact="artifact:new")],
    )
    repository.ingest_cnipa_judgment_window(
        _window(observed_at=OBSERVED_AT, artifact="artifact:old"),
        [_fact(observed_at=OBSERVED_AT, row_hash="a" * 64, artifact="artifact:old")],
    )

    assert len(store["observations"]) == 2
    current = store["current"][("OPPOSITION_DECISION", "opp-1")]
    assert current["source_row_sha256"] == "b" * 64
    assert current["source_artifact_ref"] == "artifact:new"


def test_same_timestamp_conflicting_row_fails_closed(monkeypatch):
    _fake_postgres(monkeypatch)
    repository.ingest_cnipa_judgment_window(_window(), [_fact()])

    with pytest.raises(RuntimeError, match="conflicting CNIPA LIST rows"):
        repository.ingest_cnipa_judgment_window(
            _window(artifact="artifact:conflict"),
            [_fact(row_hash="c" * 64, artifact="artifact:conflict")],
        )


def test_zero_row_window_is_a_successful_durable_observation(monkeypatch):
    store, _ = _fake_postgres(monkeypatch)
    result = repository.ingest_cnipa_judgment_window(_window(count=0), [])

    assert result == {
        "window_record_count": 0,
        "inserted_observations": 0,
        "current_updates": 0,
    }
    assert len(store["windows"]) == 1
    assert store["current"] == {}


def test_duplicate_identity_in_one_window_is_rejected_before_storage(monkeypatch):
    store, _ = _fake_postgres(monkeypatch)
    with pytest.raises(ValueError, match="duplicate CNIPA LIST identity"):
        repository.ingest_cnipa_judgment_window(_window(count=2), [_fact(), _fact()])
    assert store["windows"] == {}


def test_migration_has_append_only_history_current_projection_and_no_document_storage():
    required = (
        "CREATE TABLE IF NOT EXISTS cnipa_judgment.list_observation",
        "CREATE TABLE IF NOT EXISTS cnipa_judgment.current",
        "CREATE TABLE IF NOT EXISTS cnipa_judgment.window_observation",
        "PRIMARY KEY (document_kind, source_record_id)",
        "record_count integer NOT NULL CHECK (record_count >= 0)",
        "source_fields jsonb NOT NULL",
        "source_artifact_ref text NOT NULL",
    )
    for fragment in required:
        assert fragment in SCHEMA_SQL

    forbidden = ("detail_body", "markdown_body", "next_attempt_at", "retry", "matter_id", "client_id")
    lowered = SCHEMA_SQL.lower()
    for fragment in forbidden:
        assert fragment not in lowered


def test_repository_source_contains_no_business_or_detail_queue_state():
    source = (
        Path(__file__).parents[1] / "app" / "cnipa_judgment" / "repository.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in (
        "matter_id",
        "client_id",
        "portfolio",
        "next_attempt_at",
        "attempt_count",
        "browser",
        "detail_body",
        "markdown_body",
    ):
        assert forbidden not in source
