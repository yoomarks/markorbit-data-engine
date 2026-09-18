from __future__ import annotations

import hashlib
import json
from datetime import timezone
from typing import Sequence

from app.db import postgres_conn

from .model import CnipaJudgmentListFact, CnipaJudgmentWindowObservation


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _observation_key(fact: CnipaJudgmentListFact) -> str:
    return _canonical_sha256(
        {
            "document_kind": fact.document_kind,
            "source_record_id": fact.source_record_id,
            "source_row_sha256": fact.source_row_sha256,
            "observed_at": fact.observed_at.astimezone(timezone.utc).isoformat(),
            "source_artifact_ref": fact.source_artifact_ref,
            "detail_canonical_uri": fact.detail_canonical_uri,
        }
    )


def _window_key(window: CnipaJudgmentWindowObservation) -> str:
    return _canonical_sha256(
        {
            "document_kind": window.document_kind,
            "query_from": window.query_from.isoformat() if window.query_from else None,
            "query_to": window.query_to.isoformat() if window.query_to else None,
            "observed_at": window.observed_at.astimezone(timezone.utc).isoformat(),
            "source_artifact_ref": window.source_artifact_ref,
            "record_count": window.record_count,
        }
    )


def _fact_values(fact: CnipaJudgmentListFact, observation_key: str) -> tuple[object, ...]:
    return (
        observation_key,
        fact.document_kind,
        fact.source_record_id,
        fact.semantic_family,
        fact.detail_canonical_uri,
        fact.source_row_sha256,
        fact.observed_at,
        fact.source_artifact_ref,
        json.dumps(dict(fact.source_fields), ensure_ascii=False, sort_keys=True),
        fact.application_number,
        fact.registration_number,
        fact.trademark_name,
        fact.source_title,
        fact.source_date,
        fact.source_document_number,
        fact.cited_registration_text,
        fact.initial_markdown_ref,
        fact.initial_markdown_sha256 or None,
    )


_OBSERVATION_INSERT = """
INSERT INTO cnipa_judgment.list_observation (
    observation_key, document_kind, source_record_id, semantic_family,
    detail_canonical_uri, source_row_sha256, observed_at, source_artifact_ref,
    source_fields, application_number, registration_number, trademark_name,
    source_title, source_date, source_document_number, cited_registration_text,
    initial_markdown_ref, initial_markdown_sha256
)
VALUES (
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s::jsonb, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s
)
ON CONFLICT (observation_key) DO NOTHING
"""

_CURRENT_UPSERT = """
INSERT INTO cnipa_judgment.current (
    document_kind, source_record_id, observation_key, semantic_family,
    detail_canonical_uri, source_row_sha256, observed_at, source_artifact_ref,
    source_fields, application_number, registration_number, trademark_name,
    source_title, source_date, source_document_number, cited_registration_text,
    initial_markdown_ref, initial_markdown_sha256
)
VALUES (
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s::jsonb, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s
)
ON CONFLICT (document_kind, source_record_id)
DO UPDATE SET
    observation_key = EXCLUDED.observation_key,
    semantic_family = EXCLUDED.semantic_family,
    detail_canonical_uri = EXCLUDED.detail_canonical_uri,
    source_row_sha256 = EXCLUDED.source_row_sha256,
    observed_at = EXCLUDED.observed_at,
    source_artifact_ref = EXCLUDED.source_artifact_ref,
    source_fields = EXCLUDED.source_fields,
    application_number = EXCLUDED.application_number,
    registration_number = EXCLUDED.registration_number,
    trademark_name = EXCLUDED.trademark_name,
    source_title = EXCLUDED.source_title,
    source_date = EXCLUDED.source_date,
    source_document_number = EXCLUDED.source_document_number,
    cited_registration_text = EXCLUDED.cited_registration_text,
    initial_markdown_ref = EXCLUDED.initial_markdown_ref,
    initial_markdown_sha256 = EXCLUDED.initial_markdown_sha256,
    updated_at = now()
WHERE cnipa_judgment.current.observed_at < EXCLUDED.observed_at
   OR (
       cnipa_judgment.current.observed_at = EXCLUDED.observed_at
       AND cnipa_judgment.current.observation_key = EXCLUDED.observation_key
   )
"""


def _validate_window(
    window: CnipaJudgmentWindowObservation,
    facts: Sequence[CnipaJudgmentListFact],
) -> None:
    if window.record_count < 0:
        raise ValueError("record_count must be non-negative")
    if window.record_count != len(facts):
        raise ValueError("record_count must match the number of supplied facts")
    if (window.query_from is None) != (window.query_to is None):
        raise ValueError("query_from and query_to must be supplied together")
    if window.query_from and window.query_to and window.query_from > window.query_to:
        raise ValueError("query_from must not be after query_to")
    if window.observed_at.tzinfo is None:
        raise ValueError("window observed_at must include timezone information")
    if not window.source_artifact_ref.strip():
        raise ValueError("window source_artifact_ref is required")

    identities: set[tuple[str, str]] = set()
    for fact in facts:
        if fact.document_kind != window.document_kind:
            raise ValueError("all facts must match the window document_kind")
        if fact.observed_at != window.observed_at:
            raise ValueError("all facts must match the window observed_at")
        if fact.source_artifact_ref != window.source_artifact_ref:
            raise ValueError("all facts must match the window source_artifact_ref")
        identity = (fact.document_kind, fact.source_record_id)
        if identity in identities:
            raise ValueError(f"duplicate CNIPA LIST identity in one window: {identity}")
        identities.add(identity)


def ingest_cnipa_judgment_window(
    window: CnipaJudgmentWindowObservation,
    facts: Sequence[CnipaJudgmentListFact],
) -> dict[str, int]:
    _validate_window(window, facts)
    inserted_observations = 0
    current_updates = 0

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cnipa_judgment.window_observation (
                    window_key, document_kind, query_from, query_to,
                    observed_at, source_artifact_ref, record_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (window_key) DO NOTHING
                """,
                (
                    _window_key(window),
                    window.document_kind,
                    window.query_from,
                    window.query_to,
                    window.observed_at,
                    window.source_artifact_ref,
                    window.record_count,
                ),
            )

            for fact in facts:
                cur.execute(
                    """
                    SELECT observed_at, source_row_sha256
                    FROM cnipa_judgment.current
                    WHERE document_kind = %s AND source_record_id = %s
                    """,
                    (fact.document_kind, fact.source_record_id),
                )
                current = cur.fetchone()
                if (
                    current
                    and current["observed_at"] == fact.observed_at
                    and current["source_row_sha256"] != fact.source_row_sha256
                ):
                    raise RuntimeError(
                        "conflicting CNIPA LIST rows share one identity/observation timestamp"
                    )

                key = _observation_key(fact)
                values = _fact_values(fact, key)
                cur.execute(_OBSERVATION_INSERT, values)
                inserted_observations += int(cur.rowcount > 0)

                cur.execute(
                    _CURRENT_UPSERT,
                    (
                        fact.document_kind,
                        fact.source_record_id,
                        key,
                        *values[3:],
                    ),
                )
                current_updates += int(cur.rowcount > 0)
        conn.commit()

    return {
        "window_record_count": window.record_count,
        "inserted_observations": inserted_observations,
        "current_updates": current_updates,
    }
