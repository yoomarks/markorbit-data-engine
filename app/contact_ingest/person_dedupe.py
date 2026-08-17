from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from app.db import postgres_conn


CONTACT_PERSON_DEDUPE_VERSION = "CONTACT_PERSON_DEDUPE_V1"
_ADVISORY_LOCK_KEY = 771_341_922
_MAX_CORROBORATION_GROUP = 10
_PHONE_CHANNEL_TYPES = {"MOBILE", "LANDLINE", "PHONE", "PHONE_UNKNOWN", "WHATSAPP"}


PERSON_DEDUPE_SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS contact;

CREATE TABLE IF NOT EXISTS contact.person_merge_run (
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

CREATE TABLE IF NOT EXISTS contact.person_merge_decision (
    decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES contact.person_merge_run(run_id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES entity.entity(entity_id),
    canonical_person_id uuid NOT NULL REFERENCES contact.person(person_id),
    duplicate_person_id uuid NOT NULL REFERENCES contact.person(person_id),
    decision_status text NOT NULL,
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    CHECK (decision_status IN ('CANDIDATE', 'BLOCKED', 'APPLIED')),
    UNIQUE(run_id, duplicate_person_id),
    CHECK (canonical_person_id <> duplicate_person_id)
);

CREATE INDEX IF NOT EXISTS ix_contact_person_merge_run_started
ON contact.person_merge_run(started_at DESC);

CREATE INDEX IF NOT EXISTS ix_contact_person_merge_decision_status
ON contact.person_merge_decision(run_id, decision_status);

CREATE INDEX IF NOT EXISTS ix_contact_person_merge_duplicate
ON contact.person_merge_decision(duplicate_person_id, applied_at DESC);
"""


@dataclass(frozen=True)
class PersonSnapshot:
    entity_id: str
    person_id: str
    canonical_name: str
    normalized_name: str
    country_code: str
    status: str
    first_seen_at: datetime | None
    relation_count: int
    relation_types: tuple[str, ...]
    channels: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PersonMergeDecision:
    entity_id: str
    canonical_person_id: str
    duplicate_person_id: str
    status: str
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def ensure_person_dedupe_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(PERSON_DEDUPE_SCHEMA_SQL)


def _channel_evidence_key(channel_type: str, normalized_value: str) -> tuple[str, str]:
    channel_type = str(channel_type or "").upper()
    normalized_value = str(normalized_value or "")
    family = "PHONE" if channel_type in _PHONE_CHANNEL_TYPES else channel_type
    return family, normalized_value


def _load_person_snapshots(cur, *, country_code: str = "") -> list[PersonSnapshot]:
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
        WITH eligible_entities AS MATERIALIZED (
            SELECT e.entity_id
            FROM entity.entity AS e
            LEFT JOIN contact.entity_country_inference AS active_ci
              ON active_ci.entity_id = e.entity_id
             AND active_ci.status = 'ACCEPTED'
             AND active_ci.applied_at IS NOT NULL
            WHERE e.status <> 'MERGED'
              {country_clause}
        ),
        person_relations AS MATERIALIZED (
            SELECT
                r.entity_id,
                p.person_id,
                p.canonical_name,
                p.normalized_name,
                COALESCE(p.country_code, '') AS country_code,
                p.status,
                p.first_seen_at,
                count(*) AS relation_count,
                array_agg(DISTINCT r.relation_type ORDER BY r.relation_type)
                    AS relation_types
            FROM contact.entity_person_relation AS r
            JOIN eligible_entities AS e ON e.entity_id = r.entity_id
            JOIN contact.person AS p ON p.person_id = r.person_id
            WHERE p.status <> 'MERGED'
              AND p.normalized_name <> ''
            GROUP BY
                r.entity_id, p.person_id, p.canonical_name, p.normalized_name,
                p.country_code, p.status, p.first_seen_at
        ),
        channel_stats AS (
            SELECT
                c.person_id,
                array_agg(DISTINCT c.channel_type || '|' || c.normalized_value)
                    AS channels
            FROM contact.channel AS c
            JOIN (
                SELECT DISTINCT person_id FROM person_relations
            ) AS p ON p.person_id = c.person_id
            WHERE c.person_id IS NOT NULL
              AND c.normalized_value <> ''
            GROUP BY c.person_id
        )
        SELECT
            pr.entity_id,
            pr.person_id,
            pr.canonical_name,
            pr.normalized_name,
            pr.country_code,
            pr.status,
            pr.first_seen_at,
            pr.relation_count,
            pr.relation_types,
            COALESCE(cs.channels, ARRAY[]::text[]) AS channels
        FROM person_relations AS pr
        LEFT JOIN channel_stats AS cs ON cs.person_id = pr.person_id
        ORDER BY pr.entity_id, pr.normalized_name, pr.person_id
        """,
        params,
    )

    snapshots: list[PersonSnapshot] = []
    for row in cur.fetchall():
        item = dict(row)
        channels: list[tuple[str, str]] = []
        for raw in item.get("channels") or []:
            text = str(raw or "")
            if "|" not in text:
                continue
            channel_type, normalized_value = text.split("|", 1)
            if channel_type and normalized_value:
                channels.append((channel_type.upper(), normalized_value))
        snapshots.append(
            PersonSnapshot(
                entity_id=str(item["entity_id"]),
                person_id=str(item["person_id"]),
                canonical_name=str(item.get("canonical_name") or ""),
                normalized_name=str(item.get("normalized_name") or ""),
                country_code=str(item.get("country_code") or "").upper(),
                status=str(item.get("status") or ""),
                first_seen_at=item.get("first_seen_at"),
                relation_count=int(item.get("relation_count") or 0),
                relation_types=tuple(sorted(str(x) for x in (item.get("relation_types") or []))),
                channels=tuple(sorted(set(channels))),
            )
        )
    return snapshots


def _canonical_score(snapshot: PersonSnapshot) -> tuple[Any, ...]:
    first_seen = snapshot.first_seen_at
    if first_seen is None:
        first_seen_ts = float("inf")
    else:
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        first_seen_ts = first_seen.timestamp()
    return (
        snapshot.relation_count,
        len(snapshot.channels),
        int(bool(snapshot.country_code)),
        -first_seen_ts,
        snapshot.person_id,
    )


def plan_person_merges(
    snapshots: list[PersonSnapshot],
) -> tuple[list[PersonMergeDecision], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[PersonSnapshot]] = defaultdict(list)
    for item in snapshots:
        grouped[(item.entity_id, item.normalized_name)].append(item)

    decisions: list[PersonMergeDecision] = []
    candidate_clusters = 0
    blocked_clusters = 0
    candidate_duplicates = 0
    blocked_duplicates = 0
    same_name_without_shared_channel = 0
    oversized_groups = 0

    for (entity_id, normalized_name), members in grouped.items():
        if len(members) < 2:
            continue
        if len(members) > _MAX_CORROBORATION_GROUP:
            oversized_groups += 1
            continue

        by_channel: dict[tuple[str, str], set[str]] = defaultdict(set)
        member_by_id = {member.person_id: member for member in members}
        for member in members:
            for channel_type, normalized_value in member.channels:
                key = _channel_evidence_key(channel_type, normalized_value)
                if key[0] and key[1]:
                    by_channel[key].add(member.person_id)

        adjacency: dict[str, set[str]] = defaultdict(set)
        evidence_by_person: dict[str, set[str]] = defaultdict(set)
        for (family, normalized_value), ids in by_channel.items():
            if len(ids) < 2:
                continue
            sorted_ids = sorted(ids)
            anchor = sorted_ids[0]
            evidence = f"SAME_PERSON_CHANNEL:{family}|{normalized_value}"
            for other in sorted_ids[1:]:
                adjacency[anchor].add(other)
                adjacency[other].add(anchor)
                evidence_by_person[anchor].add(evidence)
                evidence_by_person[other].add(evidence)

        visited: set[str] = set()
        components: list[list[PersonSnapshot]] = []
        for person_id in sorted(member_by_id):
            if person_id in visited or person_id not in adjacency:
                continue
            stack = [person_id]
            component_ids: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component_ids:
                    continue
                component_ids.add(current)
                visited.add(current)
                stack.extend(sorted(adjacency.get(current, set()) - component_ids))
            if len(component_ids) >= 2:
                components.append([member_by_id[person_id] for person_id in sorted(component_ids)])

        if not components:
            same_name_without_shared_channel += 1
            continue

        for component in components:
            countries = {item.country_code for item in component if item.country_code}
            reasons: list[str] = []
            if len(countries) > 1:
                reasons.append("PERSON_COUNTRY_CONFLICT")
            canonical = max(component, key=_canonical_score)
            status = "BLOCKED" if reasons else "CANDIDATE"
            if reasons:
                blocked_clusters += 1
            else:
                candidate_clusters += 1

            component_ids = {item.person_id for item in component}
            corroboration = sorted(
                evidence
                for person_id in component_ids
                for evidence in evidence_by_person.get(person_id, set())
            )
            evidence = {
                "entity_id": entity_id,
                "normalized_name": normalized_name,
                "canonical_name": canonical.canonical_name,
                "component_size": len(component),
                "member_ids": sorted(component_ids),
                "corroboration": sorted(set(corroboration)),
                "canonical_relation_types": list(canonical.relation_types),
            }
            for member in component:
                if member.person_id == canonical.person_id:
                    continue
                decisions.append(
                    PersonMergeDecision(
                        entity_id=entity_id,
                        canonical_person_id=canonical.person_id,
                        duplicate_person_id=member.person_id,
                        status=status,
                        reason_codes=tuple(reasons),
                        evidence=evidence,
                    )
                )
                if status == "CANDIDATE":
                    candidate_duplicates += 1
                else:
                    blocked_duplicates += 1

    return decisions, {
        "scanned_person_relations": len(snapshots),
        "candidate_clusters": candidate_clusters,
        "blocked_clusters": blocked_clusters,
        "candidate_duplicates": candidate_duplicates,
        "blocked_duplicates": blocked_duplicates,
        "same_name_groups_without_shared_person_channel": same_name_without_shared_channel,
        "oversized_same_name_groups": oversized_groups,
    }


def _runtime_apply_guard(cur) -> None:
    cur.execute(
        "SELECT count(*) AS n FROM control.source_package WHERE status = 'PROCESSING'"
    )
    if int(cur.fetchone()["n"] or 0):
        raise RuntimeError("CONTACT_PERSON_DEDUPE_APPLY_BLOCKED_ACTIVE_SOURCE_PACKAGE")
    cur.execute("SELECT count(*) AS n FROM contact.import_run WHERE status = 'RUNNING'")
    if int(cur.fetchone()["n"] or 0):
        raise RuntimeError("CONTACT_PERSON_DEDUPE_APPLY_BLOCKED_ACTIVE_CONTACT_IMPORT")


def _merge_person_relations(cur, canonical_person_id: str, duplicate_person_id: str) -> int:
    cur.execute(
        """
        INSERT INTO contact.entity_person_relation AS current_relation (
            relation_id, entity_id, person_id, relation_type, title, department,
            confidence_score, first_source_id, last_source_id, first_seen_at, last_seen_at
        )
        SELECT
            gen_random_uuid(), entity_id, %s, relation_type, title, department,
            confidence_score, first_source_id, last_source_id, first_seen_at, last_seen_at
        FROM contact.entity_person_relation
        WHERE person_id = %s
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
        (canonical_person_id, duplicate_person_id),
    )
    cur.execute(
        "DELETE FROM contact.entity_person_relation WHERE person_id = %s",
        (duplicate_person_id,),
    )
    return int(cur.rowcount or 0)


def _merge_person_channels(cur, canonical_person_id: str, duplicate_person_id: str) -> int:
    cur.execute(
        """
        SELECT channel_id::text, channel_type, normalized_value
        FROM contact.channel
        WHERE person_id = %s
        ORDER BY channel_id
        """,
        (duplicate_person_id,),
    )
    channels = [dict(row) for row in cur.fetchall()]
    moved = 0
    for channel in channels:
        cur.execute(
            """
            SELECT channel_id::text
            FROM contact.channel
            WHERE person_id = %s AND channel_type = %s AND normalized_value = %s
            LIMIT 1
            """,
            (canonical_person_id, channel["channel_type"], channel["normalized_value"]),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE contact.channel_observation SET channel_id = %s WHERE channel_id = %s",
                (existing["channel_id"], channel["channel_id"]),
            )
            cur.execute(
                "DELETE FROM contact.channel WHERE channel_id = %s",
                (channel["channel_id"],),
            )
        else:
            cur.execute(
                "UPDATE contact.channel SET person_id = %s, last_seen_at = now() WHERE channel_id = %s",
                (canonical_person_id, channel["channel_id"]),
            )
        moved += 1
    return moved


def _apply_duplicate(cur, decision: PersonMergeDecision) -> dict[str, int]:
    cur.execute(
        "SELECT status, normalized_name, country_code FROM contact.person WHERE person_id = %s FOR UPDATE",
        (decision.canonical_person_id,),
    )
    canonical = cur.fetchone()
    cur.execute(
        "SELECT status, normalized_name, country_code FROM contact.person WHERE person_id = %s FOR UPDATE",
        (decision.duplicate_person_id,),
    )
    duplicate = cur.fetchone()
    if not canonical or not duplicate:
        raise RuntimeError("CONTACT_PERSON_DEDUPE_PERSON_MISSING_AT_APPLY")
    if str(duplicate["status"] or "") == "MERGED":
        return {"already_merged": 1}
    if canonical["normalized_name"] != duplicate["normalized_name"]:
        raise RuntimeError("CONTACT_PERSON_DEDUPE_NAME_DRIFT_AT_APPLY")
    countries = {
        str(value or "").upper()
        for value in (canonical["country_code"], duplicate["country_code"])
        if str(value or "")
    }
    if len(countries) > 1:
        raise RuntimeError("CONTACT_PERSON_DEDUPE_COUNTRY_CONFLICT_AT_APPLY")

    cur.execute(
        """
        SELECT count(*) AS n
        FROM contact.entity_person_relation
        WHERE person_id = %s AND entity_id <> %s
        """,
        (decision.duplicate_person_id, decision.entity_id),
    )
    if int(cur.fetchone()["n"] or 0):
        raise RuntimeError("CONTACT_PERSON_DEDUPE_DUPLICATE_GAINED_OTHER_ENTITY_RELATION")

    relations = _merge_person_relations(
        cur, decision.canonical_person_id, decision.duplicate_person_id
    )
    channels = _merge_person_channels(
        cur, decision.canonical_person_id, decision.duplicate_person_id
    )
    cur.execute(
        """
        UPDATE contact.person
        SET status = 'MERGED', updated_at = now()
        WHERE person_id = %s
        """,
        (decision.duplicate_person_id,),
    )
    if int(cur.rowcount or 0) != 1:
        raise RuntimeError("CONTACT_PERSON_DEDUPE_TOMBSTONE_FAILED")
    cur.execute(
        "UPDATE contact.person SET updated_at = now() WHERE person_id = %s",
        (decision.canonical_person_id,),
    )
    return {
        "relations_rewired": relations,
        "person_channels_rewired": channels,
    }


def _create_run(cur, *, apply_mode: bool, country_code: str) -> str:
    cur.execute(
        """
        INSERT INTO contact.person_merge_run(rule_version, status, apply_mode, country_code)
        VALUES (%s, 'RUNNING', %s, NULLIF(%s, ''))
        RETURNING run_id::text
        """,
        (CONTACT_PERSON_DEDUPE_VERSION, apply_mode, country_code),
    )
    return str(cur.fetchone()["run_id"])


def _persist_decisions(cur, run_id: str, decisions: list[PersonMergeDecision]) -> None:
    for decision in decisions:
        cur.execute(
            """
            INSERT INTO contact.person_merge_decision (
                run_id, entity_id, canonical_person_id, duplicate_person_id,
                decision_status, reason_codes, evidence
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                run_id,
                decision.entity_id,
                decision.canonical_person_id,
                decision.duplicate_person_id,
                decision.status,
                _json(list(decision.reason_codes)),
                _json(decision.evidence),
            ),
        )


def execute_person_dedupe(*, country_code: str = "CN", apply: bool = False) -> dict[str, Any]:
    country_code = country_code.strip().upper()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            ensure_person_dedupe_schema(conn)
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (_ADVISORY_LOCK_KEY,))
            if not bool(cur.fetchone()["locked"]):
                return {
                    "status": "BUSY",
                    "rule_version": CONTACT_PERSON_DEDUPE_VERSION,
                    "country_code": country_code,
                    "apply_mode": apply,
                }
            conn.commit()

        run_id = ""
        try:
            with conn.cursor() as cur:
                snapshots = _load_person_snapshots(cur, country_code=country_code)
            conn.commit()
            decisions, metrics = plan_person_merges(snapshots)

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
                            UPDATE contact.person_merge_decision
                            SET decision_status = 'APPLIED', applied_at = now()
                            WHERE run_id = %s AND duplicate_person_id = %s
                            """,
                            (run_id, decision.duplicate_person_id),
                        )
                result_metrics = {
                    **metrics,
                    "applied_duplicates": sum(
                        1
                        for decision in decisions
                        if apply and decision.status == "CANDIDATE"
                    ),
                    **dict(apply_metrics),
                }
                cur.execute(
                    """
                    UPDATE contact.person_merge_run
                    SET status = 'SUCCESS', metrics = %s::jsonb, finished_at = now()
                    WHERE run_id = %s
                    """,
                    (_json(result_metrics), run_id),
                )
            conn.commit()
            return {
                "status": "SUCCESS",
                "run_id": run_id,
                "rule_version": CONTACT_PERSON_DEDUPE_VERSION,
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
                        UPDATE contact.person_merge_run
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
            "Audit and conservatively merge duplicate Contact persons within one entity. "
            "Same name alone never merges; a shared normalized personal channel is required."
        )
    )
    parser.add_argument("--country", default="CN", help="Effective entity country, default CN")
    parser.add_argument("--apply", action="store_true", help="Apply safe person merges")
    args = parser.parse_args()
    result = execute_person_dedupe(country_code=args.country, apply=args.apply)
    print(_json({"event": "CONTACT_PERSON_DEDUPE_COMPLETE", **result}))


if __name__ == "__main__":
    main()
