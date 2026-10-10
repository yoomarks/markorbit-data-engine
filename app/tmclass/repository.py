from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from app.db import postgres_conn
from app.tmclass.contract import (
    CONTRACT_VERSION,
    SOURCE_ID,
    NormalizedTmclassEvidence,
    TmclassAdmissionError,
    normalize,
)
from app.tmclass.schema import assert_tmclass_schema


def _office_key(office: Mapping[str, Any]) -> str:
    code = office.get("code")
    if isinstance(code, str) and code:
        return "code:" + code.casefold()
    name = str(office["name"]).strip().casefold()
    return "name:" + hashlib.sha256(name.encode("utf-8")).hexdigest()


def _upsert_source(cur: Any, source_name: str, observed_at: Any) -> None:
    cur.execute(
        """
        INSERT INTO tmclass.source(source_name, first_observed_at, last_observed_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (source_name) DO UPDATE
        SET last_observed_at = GREATEST(tmclass.source.last_observed_at, EXCLUDED.last_observed_at)
        """,
        (source_name, observed_at, observed_at),
    )


def _upsert_term(
    cur: Any,
    *,
    term_id: str,
    text: str,
    nice_class: int,
    language_code: str,
    language_label: str | None,
    detail_observed: bool,
    evidence: NormalizedTmclassEvidence,
) -> None:
    cur.execute(
        """
        INSERT INTO tmclass.term(
            term_id, text_value, nice_class, language_code, language_label,
            detail_observed, first_observed_at, last_observed_at, latest_raw_artifact_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (term_id) DO UPDATE SET
            text_value = CASE
                WHEN EXCLUDED.detail_observed OR NOT tmclass.term.detail_observed
                THEN EXCLUDED.text_value ELSE tmclass.term.text_value END,
            nice_class = CASE
                WHEN EXCLUDED.detail_observed OR NOT tmclass.term.detail_observed
                THEN EXCLUDED.nice_class ELSE tmclass.term.nice_class END,
            language_code = CASE
                WHEN EXCLUDED.detail_observed OR NOT tmclass.term.detail_observed
                THEN EXCLUDED.language_code ELSE tmclass.term.language_code END,
            language_label = CASE
                WHEN EXCLUDED.detail_observed OR NOT tmclass.term.detail_observed
                THEN COALESCE(EXCLUDED.language_label, tmclass.term.language_label)
                ELSE tmclass.term.language_label END,
            detail_observed = tmclass.term.detail_observed OR EXCLUDED.detail_observed,
            last_observed_at = GREATEST(tmclass.term.last_observed_at, EXCLUDED.last_observed_at),
            latest_raw_artifact_id = CASE
                WHEN EXCLUDED.detail_observed OR NOT tmclass.term.detail_observed
                THEN EXCLUDED.latest_raw_artifact_id ELSE tmclass.term.latest_raw_artifact_id END
        """,
        (
            int(term_id),
            text,
            nice_class,
            language_code,
            language_label,
            detail_observed,
            evidence.observed_at,
            evidence.observed_at,
            evidence.raw_artifact_id,
        ),
    )


def _upsert_concept(
    cur: Any,
    *,
    concept_id: str,
    source_name: str,
    reference_id: str,
    nice_class: int,
    evidence: NormalizedTmclassEvidence,
    title: str | None = None,
    status: str | None = None,
    source_date_text: str | None = None,
    scope_status: str | None = None,
    master_count: int | None = None,
    variant_count: int | None = None,
    detail_observed: bool = False,
) -> None:
    _upsert_source(cur, source_name, evidence.observed_at)
    cur.execute(
        """
        INSERT INTO tmclass.concept(
            concept_id, source_name, reference_id, title, status, nice_class,
            source_date_text, scope_status, master_count, variant_count,
            detail_observed, first_observed_at, last_observed_at, latest_raw_artifact_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (concept_id) DO UPDATE SET
            source_name = EXCLUDED.source_name,
            reference_id = EXCLUDED.reference_id,
            nice_class = EXCLUDED.nice_class,
            title = CASE WHEN EXCLUDED.detail_observed THEN EXCLUDED.title
                         ELSE COALESCE(tmclass.concept.title, EXCLUDED.title) END,
            status = CASE WHEN EXCLUDED.detail_observed THEN EXCLUDED.status
                          ELSE COALESCE(tmclass.concept.status, EXCLUDED.status) END,
            source_date_text = CASE WHEN EXCLUDED.detail_observed THEN EXCLUDED.source_date_text
                                    ELSE tmclass.concept.source_date_text END,
            scope_status = CASE WHEN EXCLUDED.detail_observed THEN EXCLUDED.scope_status
                                ELSE COALESCE(tmclass.concept.scope_status, EXCLUDED.scope_status) END,
            master_count = COALESCE(EXCLUDED.master_count, tmclass.concept.master_count),
            variant_count = COALESCE(EXCLUDED.variant_count, tmclass.concept.variant_count),
            detail_observed = tmclass.concept.detail_observed OR EXCLUDED.detail_observed,
            last_observed_at = GREATEST(tmclass.concept.last_observed_at, EXCLUDED.last_observed_at),
            latest_raw_artifact_id = CASE
                WHEN EXCLUDED.detail_observed OR NOT tmclass.concept.detail_observed
                THEN EXCLUDED.latest_raw_artifact_id ELSE tmclass.concept.latest_raw_artifact_id END
        """,
        (
            int(concept_id),
            source_name,
            reference_id,
            title,
            status,
            nice_class,
            source_date_text,
            scope_status,
            master_count,
            variant_count,
            detail_observed,
            evidence.observed_at,
            evidence.observed_at,
            evidence.raw_artifact_id,
        ),
    )


def _taxonomy_key(nice_class: int, prefix: list[Mapping[str, Any]]) -> str:
    last = prefix[-1]
    source_node_id = last.get("sourceNodeId")
    if source_node_id:
        return f"tmclass:{nice_class}:source:{source_node_id}"
    material = json.dumps(
        [nice_class, [str(node["label"]) for node in prefix]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "tmclass:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _replace_taxonomy(
    cur: Any,
    *,
    owner_kind: str,
    owner_id: str,
    nice_class: int,
    nodes: list[Mapping[str, Any]],
) -> None:
    table = "term_taxonomy" if owner_kind == "term" else "concept_taxonomy"
    id_column = "term_id" if owner_kind == "term" else "concept_id"
    cur.execute(f"DELETE FROM tmclass.{table} WHERE {id_column} = %s", (int(owner_id),))
    parent: str | None = None
    for depth, node in enumerate(nodes):
        node_key = _taxonomy_key(nice_class, nodes[: depth + 1])
        cur.execute(
            """
            INSERT INTO tmclass.taxonomy_node(
                node_key, nice_class, source_node_id, label, parent_node_key, depth
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (node_key) DO UPDATE SET
                label = EXCLUDED.label,
                source_node_id = COALESCE(EXCLUDED.source_node_id, tmclass.taxonomy_node.source_node_id),
                parent_node_key = EXCLUDED.parent_node_key,
                depth = EXCLUDED.depth
            """,
            (node_key, nice_class, node.get("sourceNodeId"), node["label"], parent, depth),
        )
        cur.execute(
            f"INSERT INTO tmclass.{table}({id_column}, node_key, depth) VALUES (%s, %s, %s)",
            (int(owner_id), node_key, depth),
        )
        parent = node_key


def _upsert_membership(
    cur: Any,
    *,
    concept_id: str,
    term_id: str,
    language_code: str,
    role: str,
    ordinal: int | None,
    evidence: NormalizedTmclassEvidence,
) -> None:
    cur.execute(
        """
        INSERT INTO tmclass.concept_term(
            concept_id, term_id, language_code, role, ordinal,
            latest_raw_artifact_id, observed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (concept_id, term_id) DO UPDATE SET
            language_code = EXCLUDED.language_code,
            role = CASE WHEN EXCLUDED.role = 'UNKNOWN' AND tmclass.concept_term.role <> 'UNKNOWN'
                        THEN tmclass.concept_term.role ELSE EXCLUDED.role END,
            ordinal = COALESCE(EXCLUDED.ordinal, tmclass.concept_term.ordinal),
            latest_raw_artifact_id = EXCLUDED.latest_raw_artifact_id,
            observed_at = EXCLUDED.observed_at
        """,
        (
            int(concept_id),
            int(term_id),
            language_code,
            role,
            ordinal,
            evidence.raw_artifact_id,
            evidence.observed_at,
        ),
    )


def _admit_term(cur: Any, evidence: NormalizedTmclassEvidence) -> dict[str, int]:
    page = evidence.page
    term_id = str(page["termId"])
    _upsert_term(
        cur,
        term_id=term_id,
        text=str(page["text"]),
        nice_class=int(page["niceClass"]),
        language_code=str(page["languageCode"]),
        language_label=str(page["languageLabel"]),
        detail_observed=True,
        evidence=evidence,
    )
    _replace_taxonomy(
        cur,
        owner_kind="term",
        owner_id=term_id,
        nice_class=int(page["niceClass"]),
        nodes=page["taxonomy"],
    )
    cur.execute("DELETE FROM tmclass.term_office_acceptance WHERE term_id = %s", (int(term_id),))
    for office in page["acceptedBy"]:
        key = _office_key(office)
        cur.execute(
            """
            INSERT INTO tmclass.office(
                office_key, office_code, office_name, first_observed_at, last_observed_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (office_key) DO UPDATE SET
                office_code = COALESCE(EXCLUDED.office_code, tmclass.office.office_code),
                office_name = EXCLUDED.office_name,
                last_observed_at = GREATEST(tmclass.office.last_observed_at, EXCLUDED.last_observed_at)
            """,
            (key, office.get("code"), office["name"], evidence.observed_at, evidence.observed_at),
        )
        cur.execute(
            """
            INSERT INTO tmclass.term_office_acceptance(
                term_id, office_key, latest_raw_artifact_id, observed_at
            ) VALUES (%s, %s, %s, %s)
            """,
            (int(term_id), key, evidence.raw_artifact_id, evidence.observed_at),
        )

    cur.execute("DELETE FROM tmclass.term_translation WHERE source_term_id = %s", (int(term_id),))
    for target in page["translationTargets"]:
        _upsert_term(
            cur,
            term_id=str(target["termId"]),
            text=str(target["text"]),
            nice_class=int(target["niceClass"]),
            language_code=str(target["languageCode"]),
            language_label=None,
            detail_observed=False,
            evidence=evidence,
        )
        cur.execute(
            """
            INSERT INTO tmclass.term_translation(
                source_term_id, target_term_id, target_language_code, target_nice_class,
                target_text, quality, latest_raw_artifact_id, observed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(term_id),
                int(target["termId"]),
                target["languageCode"],
                target["niceClass"],
                target["text"],
                target["quality"],
                evidence.raw_artifact_id,
                evidence.observed_at,
            ),
        )

    for source in page["sources"]:
        _upsert_concept(
            cur,
            concept_id=str(source["conceptId"]),
            source_name=str(source["sourceName"]),
            reference_id=str(source["referenceId"]),
            nice_class=int(page["niceClass"]),
            evidence=evidence,
        )
        _upsert_membership(
            cur,
            concept_id=str(source["conceptId"]),
            term_id=term_id,
            language_code=str(page["languageCode"]),
            role="UNKNOWN",
            ordinal=None,
            evidence=evidence,
        )
    return {
        "terms": 1 + len(page["translationTargets"]),
        "concepts": len(page["sources"]),
        "offices": len(page["acceptedBy"]),
        "translations": len(page["translationTargets"]),
        "memberships": len(page["sources"]),
    }


def _admit_concept(cur: Any, evidence: NormalizedTmclassEvidence) -> dict[str, int]:
    page = evidence.page
    concept_id = str(page["conceptId"])
    overview = page["pageKind"] == "CONCEPT_OVERVIEW"
    _upsert_concept(
        cur,
        concept_id=concept_id,
        source_name=str(page["sourceName"]),
        reference_id=str(page["referenceId"]),
        nice_class=int(page["niceClass"]),
        title=str(page["title"]),
        status=str(page["status"]),
        source_date_text=page.get("sourceDateText"),
        scope_status=str(page["scopeStatus"]),
        master_count=int(page["masterCount"]) if overview else None,
        variant_count=int(page["variantCount"]) if overview else None,
        detail_observed=True,
        evidence=evidence,
    )
    _replace_taxonomy(
        cur,
        owner_kind="concept",
        owner_id=concept_id,
        nice_class=int(page["niceClass"]),
        nodes=page["taxonomy"],
    )
    if overview:
        cur.execute(
            "DELETE FROM tmclass.concept_language WHERE concept_id = %s", (int(concept_id),)
        )
        for language in page["languages"]:
            _upsert_term(
                cur,
                term_id=str(language["masterTermId"]),
                text=str(language["masterTermText"]),
                nice_class=int(page["niceClass"]),
                language_code=str(language["languageCode"]),
                language_label=None,
                detail_observed=False,
                evidence=evidence,
            )
            cur.execute(
                """
                INSERT INTO tmclass.concept_language(
                    concept_id, language_code, master_term_id, variant_count,
                    total_term_count, latest_raw_artifact_id, observed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(concept_id),
                    language["languageCode"],
                    int(language["masterTermId"]),
                    language["variantCount"],
                    language["totalTermCount"],
                    evidence.raw_artifact_id,
                    evidence.observed_at,
                ),
            )
            _upsert_membership(
                cur,
                concept_id=concept_id,
                term_id=str(language["masterTermId"]),
                language_code=str(language["languageCode"]),
                role="MASTER",
                ordinal=1,
                evidence=evidence,
            )
        count = len(page["languages"])
        return {
            "terms": count,
            "concepts": 1,
            "offices": 0,
            "translations": 0,
            "memberships": count,
        }

    language_code = str(page["languageCode"])
    cur.execute(
        "DELETE FROM tmclass.concept_term WHERE concept_id = %s AND language_code = %s",
        (int(concept_id), language_code),
    )
    master_id: int | None = None
    variants = 0
    for term in page["terms"]:
        _upsert_term(
            cur,
            term_id=str(term["termId"]),
            text=str(term["text"]),
            nice_class=int(page["niceClass"]),
            language_code=language_code,
            language_label=None,
            detail_observed=False,
            evidence=evidence,
        )
        if term["role"] == "MASTER":
            master_id = int(term["termId"])
        else:
            variants += 1
        _upsert_membership(
            cur,
            concept_id=concept_id,
            term_id=str(term["termId"]),
            language_code=language_code,
            role=str(term["role"]),
            ordinal=int(term["ordinal"]),
            evidence=evidence,
        )
    cur.execute(
        """
        INSERT INTO tmclass.concept_language(
            concept_id, language_code, master_term_id, variant_count,
            total_term_count, latest_raw_artifact_id, observed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (concept_id, language_code) DO UPDATE SET
            master_term_id = EXCLUDED.master_term_id,
            variant_count = EXCLUDED.variant_count,
            total_term_count = EXCLUDED.total_term_count,
            latest_raw_artifact_id = EXCLUDED.latest_raw_artifact_id,
            observed_at = EXCLUDED.observed_at
        """,
        (
            int(concept_id),
            language_code,
            master_id,
            variants,
            len(page["terms"]),
            evidence.raw_artifact_id,
            evidence.observed_at,
        ),
    )
    return {
        "terms": len(page["terms"]),
        "concepts": 1,
        "offices": 0,
        "translations": 0,
        "memberships": len(page["terms"]),
    }


def admit_tmclass_evidence(
    package: Mapping[str, Any],
    *,
    connection_factory: Any = postgres_conn,
) -> dict[str, Any]:
    normalized = normalize(package)
    assert_tmclass_schema()
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sha256, page_kind FROM tmclass.source_evidence WHERE raw_artifact_id = %s",
                (normalized.raw_artifact_id,),
            )
            existing = cur.fetchone()
            if existing:
                if existing["sha256"] != normalized.evidence["sha256"]:
                    raise TmclassAdmissionError(
                        "RawArtifact identity already exists with different SHA-256"
                    )
                return {
                    "contract_version": CONTRACT_VERSION,
                    "outcome": "TMCLASS_SOURCE_EVIDENCE_ADMITTED",
                    "source_id": SOURCE_ID,
                    "raw_artifact_id": normalized.raw_artifact_id,
                    "page_kind": existing["page_kind"],
                    "replayed": True,
                    "facts": {
                        "terms": 0,
                        "concepts": 0,
                        "offices": 0,
                        "translations": 0,
                        "memberships": 0,
                    },
                }
            payload = {
                "contractVersion": CONTRACT_VERSION,
                "sourceId": SOURCE_ID,
                "observedAt": normalized.observed_at.isoformat(),
                "evidence": normalized.evidence,
                "page": normalized.page,
            }
            cur.execute(
                """
                INSERT INTO tmclass.source_evidence(
                    raw_artifact_id, artifact_version, workspace_id, source_definition_id,
                    collection_run_id, canonical_uri, source_uri, sha256, page_kind,
                    observed_at, normalized_payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    normalized.raw_artifact_id,
                    normalized.evidence["artifactVersion"],
                    normalized.evidence["workspaceId"],
                    normalized.evidence["sourceDefinitionId"],
                    normalized.evidence["collectionRunId"],
                    normalized.evidence["canonicalUri"],
                    normalized.evidence["sourceUri"],
                    normalized.evidence["sha256"],
                    normalized.page_kind,
                    normalized.observed_at,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            facts = (
                _admit_term(cur, normalized)
                if normalized.page_kind == "TERM"
                else _admit_concept(cur, normalized)
            )
        conn.commit()
    return {
        "contract_version": CONTRACT_VERSION,
        "outcome": "TMCLASS_SOURCE_EVIDENCE_ADMITTED",
        "source_id": SOURCE_ID,
        "raw_artifact_id": normalized.raw_artifact_id,
        "page_kind": normalized.page_kind,
        "replayed": False,
        "facts": facts,
    }
