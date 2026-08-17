from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Iterable

from app.db import postgres_conn


CONTACT_ENTITY_DEDUPE_VERSION = "CONTACT_ENTITY_DEDUPE_V1"
_ADVISORY_LOCK_KEY = 771_341_921
_STRONG_IDENTIFIER_TYPES = {
    "CN_USCC",
    "REGISTRATION_ID",
    "CN_AGENT_CODE",
    "AGENT_CODE",
}
_PERSON_ENTITY_TYPES = {"AGENT_PERSON"}
_CONTACT_SOURCE_NAMES = {"", "CONTACT_INGEST"}
_MAX_CORROBORATION_GROUP = 50
_MIN_ADDRESS_KEY_LENGTH = 6


DEDUPE_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.entity_merge_run (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version text NOT NULL,
    status text NOT NULL,
    apply_mode boolean NOT NULL DEFAULT false,
    country_code char(2),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'BUSY'))
);

CREATE TABLE IF NOT EXISTS contact.entity_merge_decision (
    decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES contact.entity_merge_run(run_id) ON DELETE CASCADE,
    canonical_entity_id uuid NOT NULL REFERENCES entity.entity(entity_id),
    duplicate_entity_id uuid NOT NULL REFERENCES entity.entity(entity_id),
    decision_status text NOT NULL,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    CHECK (decision_status IN ('CANDIDATE', 'BLOCKED', 'APPLIED')),
    UNIQUE(run_id, duplicate_entity_id),
    CHECK (canonical_entity_id <> duplicate_entity_id)
);

CREATE INDEX IF NOT EXISTS ix_contact_entity_merge_run_started
ON contact.entity_merge_run(started_at DESC);

CREATE INDEX IF NOT EXISTS ix_contact_entity_merge_decision_status
ON contact.entity_merge_decision(run_id, decision_status);

CREATE INDEX IF NOT EXISTS ix_contact_entity_merge_duplicate
ON contact.entity_merge_decision(duplicate_entity_id, applied_at DESC);
"""


@dataclass(frozen=True)
class EntitySnapshot:
    entity_id: str
    entity_type: str
    canonical_name: str
    normalized_name: str
    normalized_address: str
    source_country_code: str
    inferred_country_code: str
    inferred_country_confidence: float
    source_primary: str
    status: str
    confidence_score: float
    first_seen_at: datetime | None
    mention_count: int
    raw_record_count: int
    identifiers: tuple[tuple[str, str, str], ...]
    channels: tuple[tuple[str, str], ...]

    @property
    def effective_country_code(self) -> str:
        return self.source_country_code or self.inferred_country_code

    @property
    def has_official_identity(self) -> bool:
        return self.mention_count > 0 or self.source_primary.upper() not in _CONTACT_SOURCE_NAMES


@dataclass(frozen=True)
class MergeDecision:
    canonical_entity_id: str
    duplicate_entity_id: str
    status: str
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]


class _UnionFind:
    def __init__(self, ids: Iterable[str]) -> None:
        self.parent = {entity_id: entity_id for entity_id in ids}

    def find(self, entity_id: str) -> str:
        parent = self.parent[entity_id]
        if parent != entity_id:
            self.parent[entity_id] = self.find(parent)
        return self.parent[entity_id]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def ensure_dedupe_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DEDUPE_SCHEMA_SQL)


def _snapshot_from_row(row: dict[str, Any]) -> EntitySnapshot:
    identifiers = tuple(
        sorted(
            (
                str(item.get("identifier_type") or "").upper(),
                str(item.get("normalized_value") or ""),
                str(item.get("country_code") or "").upper(),
            )
            for item in (row.get("identifiers") or [])
            if str(item.get("identifier_type") or "").strip()
            and str(item.get("normalized_value") or "").strip()
        )
    )
    channels: list[tuple[str, str]] = []
    for raw in row.get("channels") or []:
        value = str(raw or "")
        if "|" not in value:
            continue
        channel_type, normalized_value = value.split("|", 1)
        if channel_type and normalized_value:
            channels.append((channel_type.upper(), normalized_value))
    return EntitySnapshot(
        entity_id=str(row["entity_id"]),
        entity_type=str(row.get("entity_type") or "").upper(),
        canonical_name=str(row.get("canonical_name") or ""),
        normalized_name=str(row.get("normalized_name") or ""),
        normalized_address=str(row.get("normalized_address") or ""),
        source_country_code=str(row.get("source_country_code") or "").upper(),
        inferred_country_code=str(row.get("inferred_country_code") or "").upper(),
        inferred_country_confidence=float(row.get("inferred_country_confidence") or 0),
        source_primary=str(row.get("source_primary") or ""),
        status=str(row.get("status") or ""),
        confidence_score=float(row.get("confidence_score") or 0),
        first_seen_at=row.get("first_seen_at"),
        mention_count=int(row.get("mention_count") or 0),
        raw_record_count=int(row.get("raw_record_count") or 0),
        identifiers=identifiers,
        channels=tuple(sorted(set(channels))),
    )


def _load_snapshots(cur, *, country_code: str = "") -> list[EntitySnapshot]:
    country_code = country_code.strip().upper()
    country_clause = ""
    params: list[Any] = []
    if country_code:
        country_clause = (
            "AND COALESCE(NULLIF(e.country_code, ''), active_ci.country_code, '') = %s"
        )
        params.append(country_code[:2])

    cur.execute(
        f"""
        WITH contact_entities AS (
            SELECT entity_id FROM contact.raw_record WHERE entity_id IS NOT NULL
            UNION
            SELECT entity_id FROM contact.entity_person_relation
            UNION
            SELECT entity_id FROM contact.channel WHERE entity_id IS NOT NULL
        ),
        entity_channels AS (
            SELECT c.entity_id, c.channel_type, c.normalized_value
            FROM contact.channel AS c
            WHERE c.entity_id IS NOT NULL AND c.normalized_value <> ''
            UNION
            SELECT r.entity_id, c.channel_type, c.normalized_value
            FROM contact.channel AS c
            JOIN contact.entity_person_relation AS r ON r.person_id = c.person_id
            WHERE c.person_id IS NOT NULL AND c.normalized_value <> ''
        )
        SELECT
            e.entity_id,
            e.entity_type,
            e.canonical_name,
            e.normalized_name,
            e.normalized_address,
            COALESCE(e.country_code, '') AS source_country_code,
            COALESCE(active_ci.country_code, '') AS inferred_country_code,
            COALESCE(active_ci.confidence, 0) AS inferred_country_confidence,
            COALESCE(e.source_primary, '') AS source_primary,
            e.status,
            COALESCE(e.confidence_score, 0) AS confidence_score,
            e.first_seen_at,
            (SELECT count(*) FROM entity.entity_mention AS m
             WHERE m.entity_id = e.entity_id) AS mention_count,
            (SELECT count(*) FROM contact.raw_record AS rr
             WHERE rr.entity_id = e.entity_id) AS raw_record_count,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'identifier_type', i.identifier_type,
                        'normalized_value', i.normalized_value,
                        'country_code', COALESCE(i.country_code, '')
                    )
                    ORDER BY i.identifier_type, i.normalized_value
                )
                FROM entity.entity_identifier AS i
                WHERE i.entity_id = e.entity_id
            ), '[]'::jsonb) AS identifiers,
            COALESCE((
                SELECT array_agg(DISTINCT ec.channel_type || '|' || ec.normalized_value)
                FROM entity_channels AS ec
                WHERE ec.entity_id = e.entity_id
            ), ARRAY[]::text[]) AS channels
        FROM contact_entities AS ce
        JOIN entity.entity AS e ON e.entity_id = ce.entity_id
        LEFT JOIN contact.entity_country_inference AS active_ci
          ON active_ci.entity_id = e.entity_id
         AND active_ci.status = 'ACCEPTED'
         AND active_ci.applied_at IS NOT NULL
        WHERE e.normalized_name <> ''
          AND e.status <> 'MERGED'
          {country_clause}
        ORDER BY e.normalized_name, e.entity_id
        """,
        params,
    )
    return [_snapshot_from_row(dict(row)) for row in cur.fetchall()]


def _canonical_score(snapshot: EntitySnapshot) -> tuple[Any, ...]:
    first_seen = snapshot.first_seen_at
    if first_seen is None:
        first_seen_ts = float("inf")
    else:
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        first_seen_ts = first_seen.timestamp()
    return (
        int(snapshot.mention_count > 0),
        int(snapshot.source_primary.upper() not in _CONTACT_SOURCE_NAMES),
        int(bool(snapshot.source_country_code)),
        int(bool(snapshot.inferred_country_code)),
        round(snapshot.inferred_country_confidence, 4),
        len(snapshot.identifiers),
        snapshot.raw_record_count,
        len(snapshot.channels),
        round(snapshot.confidence_score, 4),
        -first_seen_ts,
        snapshot.entity_id,
    )


def _component_block_reasons(component: list[EntitySnapshot]) -> list[str]:
    reasons: list[str] = []
    effective_countries = {
        item.effective_country_code for item in component if item.effective_country_code
    }
    if len(effective_countries) > 1:
        reasons.append("COUNTRY_CONFLICT")

    person_flags = {item.entity_type in _PERSON_ENTITY_TYPES for item in component}
    if len(person_flags) > 1:
        reasons.append("PERSON_ORGANIZATION_TYPE_CONFLICT")

    official_members = [item for item in component if item.has_official_identity]
    if len(official_members) > 1:
        reasons.append("MULTIPLE_OFFICIAL_IDENTITIES")

    values_by_type: dict[str, set[str]] = defaultdict(set)
    for item in component:
        for identifier_type, value, _country in item.identifiers:
            if identifier_type in _STRONG_IDENTIFIER_TYPES and value:
                values_by_type[identifier_type].add(value)
    if any(len(values) > 1 for values in values_by_type.values()):
        reasons.append("STRONG_IDENTIFIER_CONFLICT")
    return reasons


def plan_entity_merges(
    snapshots: list[EntitySnapshot],
) -> tuple[list[MergeDecision], dict[str, Any]]:
    by_id = {item.entity_id: item for item in snapshots}
    union = _UnionFind(by_id)
    evidence_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    oversized_groups = 0

    def connect(groups: dict[tuple[Any, ...], list[str]], evidence_prefix: str) -> None:
        nonlocal oversized_groups
        for key, entity_ids in groups.items():
            unique_ids = sorted(set(entity_ids))
            if len(unique_ids) < 2:
                continue
            if len(unique_ids) > _MAX_CORROBORATION_GROUP:
                oversized_groups += 1
                continue
            anchor = unique_ids[0]
            for other in unique_ids[1:]:
                union.union(anchor, other)
                evidence_by_pair[(anchor, other)].add(
                    f"{evidence_prefix}:{'|'.join(str(part) for part in key)}"
                )

    address_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    channel_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for item in snapshots:
        if len(item.normalized_address) >= _MIN_ADDRESS_KEY_LENGTH:
            address_groups[(item.normalized_name, item.normalized_address)].append(item.entity_id)
        for channel_type, normalized_value in item.channels:
            channel_groups[(item.normalized_name, channel_type, normalized_value)].append(
                item.entity_id
            )
    connect(address_groups, "EXACT_NAME_ADDRESS")
    connect(channel_groups, "EXACT_NAME_CHANNEL")

    components: dict[str, list[EntitySnapshot]] = defaultdict(list)
    for item in snapshots:
        components[union.find(item.entity_id)].append(item)

    decisions: list[MergeDecision] = []
    candidate_clusters = 0
    blocked_clusters = 0
    candidate_duplicates = 0
    blocked_duplicates = 0
    for members in components.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda item: item.entity_id)
        canonical = max(members, key=_canonical_score)
        reasons = _component_block_reasons(members)
        status = "BLOCKED" if reasons else "CANDIDATE"
        if reasons:
            blocked_clusters += 1
        else:
            candidate_clusters += 1

        member_ids = {item.entity_id for item in members}
        corroboration = sorted(
            evidence
            for pair, pair_evidence in evidence_by_pair.items()
            if pair[0] in member_ids and pair[1] in member_ids
            for evidence in pair_evidence
        )
        evidence = {
            "component_size": len(members),
            "normalized_name": canonical.normalized_name,
            "canonical_name": canonical.canonical_name,
            "effective_country_code": canonical.effective_country_code,
            "corroboration": corroboration,
            "member_ids": sorted(member_ids),
            "canonical_score": list(_canonical_score(canonical)[:-1]),
        }
        for member in members:
            if member.entity_id == canonical.entity_id:
                continue
            decisions.append(
                MergeDecision(
                    canonical_entity_id=canonical.entity_id,
                    duplicate_entity_id=member.entity_id,
                    status=status,
                    reason_codes=tuple(reasons),
                    evidence=evidence,
                )
            )
            if status == "CANDIDATE":
                candidate_duplicates += 1
            else:
                blocked_duplicates += 1

    metrics = {
        "scanned_entities": len(snapshots),
        "candidate_clusters": candidate_clusters,
        "blocked_clusters": blocked_clusters,
        "candidate_duplicates": candidate_duplicates,
        "blocked_duplicates": blocked_duplicates,
        "oversized_corroboration_groups": oversized_groups,
    }
    return decisions, metrics


def _runtime_apply_guard(cur) -> None:
    cur.execute(
        """
        SELECT count(*) AS active_packages
        FROM control.source_package
        WHERE status = 'PROCESSING'
        """
    )
    if int(cur.fetchone()["active_packages"] or 0):
        raise RuntimeError("CONTACT_ENTITY_DEDUPE_APPLY_BLOCKED_ACTIVE_SOURCE_PACKAGE")
    cur.execute(
        """
        SELECT count(*) AS active_imports
        FROM contact.import_run
        WHERE status = 'RUNNING'
        """
    )
    if int(cur.fetchone()["active_imports"] or 0):
        raise RuntimeError("CONTACT_ENTITY_DEDUPE_APPLY_BLOCKED_ACTIVE_CONTACT_IMPORT")


def _move_entity_channels(cur, canonical_entity_id: str, duplicate_entity_id: str) -> int:
    cur.execute(
        """
        SELECT channel_id::text, channel_type, normalized_value
        FROM contact.channel
        WHERE entity_id = %s
        ORDER BY channel_id
        """,
        (duplicate_entity_id,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    moved = 0
    for row in rows:
        cur.execute(
            """
            SELECT channel_id::text
            FROM contact.channel
            WHERE entity_id = %s AND channel_type = %s AND normalized_value = %s
            LIMIT 1
            """,
            (canonical_entity_id, row["channel_type"], row["normalized_value"]),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE contact.channel_observation SET channel_id = %s WHERE channel_id = %s",
                (existing["channel_id"], row["channel_id"]),
            )
            cur.execute("DELETE FROM contact.channel WHERE channel_id = %s", (row["channel_id"],))
        else:
            cur.execute(
                "UPDATE contact.channel SET entity_id = %s, last_seen_at = now() WHERE channel_id = %s",
                (canonical_entity_id, row["channel_id"]),
            )
        moved += 1
    return moved


def _merge_relations(cur, canonical_entity_id: str, duplicate_entity_id: str) -> int:
    cur.execute(
        """
        INSERT INTO contact.entity_person_relation AS current_relation (
            relation_id, entity_id, person_id, relation_type, title, department,
            confidence_score, first_source_id, last_source_id, first_seen_at, last_seen_at
        )
        SELECT
            gen_random_uuid(), %s, person_id, relation_type, title, department,
            confidence_score, first_source_id, last_source_id, first_seen_at, last_seen_at
        FROM contact.entity_person_relation
        WHERE entity_id = %s
        ON CONFLICT (entity_id, person_id, relation_type)
        DO UPDATE SET
            title = CASE
                WHEN length(EXCLUDED.title) > length(current_relation.title)
                THEN EXCLUDED.title ELSE current_relation.title END,
            department = CASE
                WHEN length(EXCLUDED.department) > length(current_relation.department)
                THEN EXCLUDED.department ELSE current_relation.department END,
            confidence_score = GREATEST(
                current_relation.confidence_score, EXCLUDED.confidence_score
            ),
            first_source_id = COALESCE(
                current_relation.first_source_id, EXCLUDED.first_source_id
            ),
            last_source_id = COALESCE(
                EXCLUDED.last_source_id, current_relation.last_source_id
            ),
            first_seen_at = LEAST(current_relation.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_at = GREATEST(current_relation.last_seen_at, EXCLUDED.last_seen_at)
        """,
        (canonical_entity_id, duplicate_entity_id),
    )
    cur.execute(
        "DELETE FROM contact.entity_person_relation WHERE entity_id = %s",
        (duplicate_entity_id,),
    )
    return int(cur.rowcount or 0)


def _move_contact_aliases(cur, canonical_entity_id: str, duplicate_entity_id: str) -> int:
    cur.execute(
        """
        INSERT INTO entity.entity_alias AS current_alias (
            entity_id, alias_name, normalized_name, language_code, source, confidence_score
        )
        SELECT %s, alias_name, normalized_name, language_code, source, confidence_score
        FROM entity.entity_alias
        WHERE entity_id = %s AND source = 'CONTACT_INGEST'
        ON CONFLICT (entity_id, normalized_name, source)
        DO UPDATE SET
            alias_name = CASE
                WHEN length(EXCLUDED.alias_name) > length(current_alias.alias_name)
                THEN EXCLUDED.alias_name ELSE current_alias.alias_name END,
            language_code = COALESCE(current_alias.language_code, EXCLUDED.language_code),
            confidence_score = GREATEST(
                current_alias.confidence_score, EXCLUDED.confidence_score
            )
        """,
        (canonical_entity_id, duplicate_entity_id),
    )
    cur.execute(
        "DELETE FROM entity.entity_alias WHERE entity_id = %s AND source = 'CONTACT_INGEST'",
        (duplicate_entity_id,),
    )
    return int(cur.rowcount or 0)


def _apply_duplicate(cur, decision: MergeDecision) -> dict[str, int]:
    canonical = decision.canonical_entity_id
    duplicate = decision.duplicate_entity_id

    cur.execute(
        "SELECT status, normalized_name FROM entity.entity WHERE entity_id = %s FOR UPDATE",
        (canonical,),
    )
    canonical_row = cur.fetchone()
    cur.execute(
        "SELECT status, normalized_name FROM entity.entity WHERE entity_id = %s FOR UPDATE",
        (duplicate,),
    )
    duplicate_row = cur.fetchone()
    if not canonical_row or not duplicate_row:
        raise RuntimeError("CONTACT_ENTITY_DEDUPE_ENTITY_MISSING_AT_APPLY")
    if str(duplicate_row["status"] or "") == "MERGED":
        return {"already_merged": 1}
    if canonical_row["normalized_name"] != duplicate_row["normalized_name"]:
        raise RuntimeError("CONTACT_ENTITY_DEDUPE_NAME_DRIFT_AT_APPLY")

    cur.execute(
        "SELECT count(*) AS mention_count FROM entity.entity_mention WHERE entity_id = %s",
        (duplicate,),
    )
    if int(cur.fetchone()["mention_count"] or 0):
        raise RuntimeError("CONTACT_ENTITY_DEDUPE_DUPLICATE_GAINED_TRADEMARK_MENTION")

    cur.execute(
        "UPDATE contact.raw_record SET entity_id = %s, updated_at = now() WHERE entity_id = %s",
        (canonical, duplicate),
    )
    raw_records = int(cur.rowcount or 0)
    relations = _merge_relations(cur, canonical, duplicate)
    channels = _move_entity_channels(cur, canonical, duplicate)
    aliases = _move_contact_aliases(cur, canonical, duplicate)

    cur.execute(
        """
        UPDATE entity.entity_identifier
        SET entity_id = %s, last_seen_at = now()
        WHERE entity_id = %s AND source = 'CONTACT_INGEST'
        """,
        (canonical, duplicate),
    )
    identifiers = int(cur.rowcount or 0)

    cur.execute(
        """
        UPDATE entity.entity
        SET status = 'MERGED',
            resolution_method = %s,
            updated_at = now()
        WHERE entity_id = %s
        """,
        (CONTACT_ENTITY_DEDUPE_VERSION, duplicate),
    )
    if int(cur.rowcount or 0) != 1:
        raise RuntimeError("CONTACT_ENTITY_DEDUPE_TOMBSTONE_FAILED")
    cur.execute("UPDATE entity.entity SET updated_at = now() WHERE entity_id = %s", (canonical,))
    return {
        "raw_records_rewired": raw_records,
        "relations_rewired": relations,
        "entity_channels_rewired": channels,
        "contact_aliases_rewired": aliases,
        "contact_identifiers_rewired": identifiers,
    }


def _create_run(cur, *, apply_mode: bool, country_code: str) -> str:
    cur.execute(
        """
        INSERT INTO contact.entity_merge_run(rule_version, status, apply_mode, country_code)
        VALUES (%s, 'RUNNING', %s, NULLIF(%s, ''))
        RETURNING run_id::text
        """,
        (CONTACT_ENTITY_DEDUPE_VERSION, apply_mode, country_code),
    )
    return str(cur.fetchone()["run_id"])


def _persist_decisions(cur, run_id: str, decisions: list[MergeDecision]) -> None:
    for decision in decisions:
        cur.execute(
            """
            INSERT INTO contact.entity_merge_decision (
                run_id, canonical_entity_id, duplicate_entity_id,
                decision_status, reason_codes, evidence
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                run_id,
                decision.canonical_entity_id,
                decision.duplicate_entity_id,
                decision.status,
                _json(list(decision.reason_codes)),
                _json(decision.evidence),
            ),
        )


def execute_entity_dedupe(*, country_code: str = "CN", apply: bool = False) -> dict[str, Any]:
    country_code = country_code.strip().upper()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            ensure_dedupe_schema(conn)
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (_ADVISORY_LOCK_KEY,))
            if not bool(cur.fetchone()["locked"]):
                return {
                    "status": "BUSY",
                    "rule_version": CONTACT_ENTITY_DEDUPE_VERSION,
                    "country_code": country_code,
                    "apply_mode": apply,
                }
            conn.commit()

        run_id = ""
        try:
            with conn.cursor() as cur:
                snapshots = _load_snapshots(cur, country_code=country_code)
            conn.commit()  # session advisory lock survives commit; avoid idle tx while planning.
            decisions, metrics = plan_entity_merges(snapshots)

            with conn.cursor() as cur:
                if apply:
                    _runtime_apply_guard(cur)
                run_id = _create_run(cur, apply_mode=apply, country_code=country_code)
                _persist_decisions(cur, run_id, decisions)
                apply_metrics: dict[str, int] = defaultdict(int)
                if apply:
                    for decision in decisions:
                        if decision.status != "CANDIDATE":
                            continue
                        changed = _apply_duplicate(cur, decision)
                        for key, value in changed.items():
                            apply_metrics[key] += int(value)
                        cur.execute(
                            """
                            UPDATE contact.entity_merge_decision
                            SET decision_status = 'APPLIED', applied_at = now()
                            WHERE run_id = %s AND duplicate_entity_id = %s
                            """,
                            (run_id, decision.duplicate_entity_id),
                        )
                result_metrics = {
                    **metrics,
                    "applied_duplicates": sum(
                        1 for decision in decisions
                        if apply and decision.status == "CANDIDATE"
                    ),
                    **dict(apply_metrics),
                }
                cur.execute(
                    """
                    UPDATE contact.entity_merge_run
                    SET status = 'SUCCESS', metrics = %s::jsonb, finished_at = now()
                    WHERE run_id = %s
                    """,
                    (_json(result_metrics), run_id),
                )
            conn.commit()
            return {
                "status": "SUCCESS",
                "run_id": run_id,
                "rule_version": CONTACT_ENTITY_DEDUPE_VERSION,
                "country_code": country_code,
                "apply_mode": apply,
                "metrics": result_metrics,
            }
        except Exception as exc:
            conn.rollback()
            if run_id:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE contact.entity_merge_run
                        SET status = 'FAILED', error_message = %s, finished_at = now()
                        WHERE run_id = %s
                        """,
                        (str(exc), run_id),
                    )
                conn.commit()
            raise
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
            conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit and conservatively merge duplicate contact-owned entities. "
            "Preview is the default; --apply is blocked while source packages/imports are active."
        )
    )
    parser.add_argument("--country", default="CN", help="Effective country filter, default CN")
    parser.add_argument("--apply", action="store_true", help="Apply safe candidate merges")
    args = parser.parse_args()
    result = execute_entity_dedupe(country_code=args.country, apply=args.apply)
    print(_json({"event": "CONTACT_ENTITY_DEDUPE_COMPLETE", **result}))


if __name__ == "__main__":
    main()
