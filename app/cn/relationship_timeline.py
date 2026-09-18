from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Mapping

from app.read_performance_baseline import DEFAULT_QUERY_BUDGET
from app.temporal_relationship_contract import (
    QUERY_SCOPES,
    RelationshipDerivationRef,
    RelationshipEvidenceRef,
    RelationshipResourceRef,
    TemporalRelationshipContractError,
    build_temporal_relationship_edge,
)


RELATIONSHIP_TIMELINE_SCHEMA_VERSION = "CN_RELATIONSHIP_TIMELINE_SCHEMA_V1"
RELATIONSHIP_TIMELINE_READY_VERSION = "CN_RELATIONSHIP_TIMELINE_READY_V2"
CN_RELATIONSHIP_TIMELINE_TABLE = "markorbit_facts.cn_trademark_relationship_event"
RELATIONSHIP_DERIVATION_VERSION = "CN_RELATIONSHIP_TIMELINE_V1"
MAX_RELATIONSHIP_EVENTS = 5_000

_EVENT_ROLE = {
    "OWNER_RELATION_OBSERVED": ("OWNER", False),
    "OWNER_RELATION_SUPERSEDED_OBSERVED": ("OWNER", True),
    "CO_OWNER_RELATION_OBSERVED": ("CO_OWNER", False),
    "CO_OWNER_RELATION_SUPERSEDED_OBSERVED": ("CO_OWNER", True),
    "AGENT_RELATION_OBSERVED": ("AGENT", False),
    "AGENT_RELATION_SUPERSEDED_OBSERVED": ("AGENT", True),
}


class RelationshipTimelineInvalid(ValueError):
    pass


class RelationshipTimelineScopeExceeded(RuntimeError):
    pass


class RelationshipTimelineUnavailable(RuntimeError):
    pass


def _text_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return str(value or "")


def _application_number(value: str) -> str:
    application = str(value or "").strip()
    if not application or len(application) > 128:
        raise RelationshipTimelineInvalid("CN application number must contain 1 to 128 characters")
    return application


def _scope(value: str) -> str:
    scope = str(value or "").strip().lower()
    if scope not in QUERY_SCOPES:
        raise RelationshipTimelineInvalid("scope must be current, historical, or all")
    return scope


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rows(client: Any, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        result = client.query(sql, settings=settings)
    except Exception as exc:
        raise RelationshipTimelineUnavailable(str(exc)) from exc
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _fact_value(value: Any, label: str) -> dict[str, str]:
    try:
        decoded = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        raise RelationshipTimelineUnavailable(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RelationshipTimelineUnavailable(f"{label} must be a JSON object")
    result = {str(key): str(item or "").strip() for key, item in decoded.items()}
    relation_key = result.get("relation_key", "")
    if len(relation_key) != 64 or any(
        character not in "0123456789abcdef" for character in relation_key.lower()
    ):
        raise RelationshipTimelineUnavailable(f"{label} has an invalid relation_key")
    result["relation_key"] = relation_key.lower()
    return result


def _iso_timestamp(value: Any) -> str:
    if not isinstance(value, datetime):
        text = str(value or "").strip().replace(" ", "T")
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RelationshipTimelineUnavailable("observed_at is not an ISO timestamp") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise RelationshipTimelineUnavailable("event_date is not an ISO date") from exc


def _rank(row: Mapping[str, Any]) -> tuple[int, str]:
    return int(row.get("source_rank") or 0), _text_value(row.get("event_hash"))


def _edge_item(
    *,
    application: str,
    role: str,
    fact: Mapping[str, str],
    observed: Mapping[str, Any],
    superseded: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current = superseded is None or _rank(observed) > _rank(superseded)
    evidence_row = observed if current else superseded
    relationship = {
        "OWNER": "CURRENT_OWNER" if current else "FORMER_OWNER",
        "CO_OWNER": "CURRENT_OWNER" if current else "FORMER_OWNER",
        "AGENT": "CURRENT_AGENT" if current else "FORMER_AGENT",
    }[role]
    resource_type = "AGENT" if role == "AGENT" else "OWNER"
    entity_id = str(fact.get("entity_id") or "").strip()
    resource_id = (
        f"cn:entity:{entity_id}" if entity_id else f"cn:party-relation:{fact['relation_key']}"
    )
    try:
        edge = build_temporal_relationship_edge(
            jurisdiction="CN",
            relationship_type=relationship,
            source=RelationshipResourceRef(resource_type, resource_id),
            target=RelationshipResourceRef("TRADEMARK", f"cn:trademark:{application}"),
            authority_level="DERIVED_FROM_OFFICIAL_HISTORY",
            evidence=RelationshipEvidenceRef(
                evidence_kind="STRUCTURED_SOURCE",
                source_authority="CNIPA",
                source_domain="CN",
                source_record_id=_text_value(evidence_row["event_hash"]),
                source_package_id=_text_value(evidence_row["source_package_id"]),
                source_uri=_text_value(evidence_row.get("source_file")) or None,
                source_hash=_text_value(evidence_row["source_row_hash"]),
            ),
            observed_at=_iso_timestamp(evidence_row["observed_at"]),
            event_at=_iso_date(evidence_row.get("event_date")),
            is_current=current,
            derivation=RelationshipDerivationRef(
                "HISTORY_DERIVATION", RELATIONSHIP_DERIVATION_VERSION, "1"
            ),
        )
    except TemporalRelationshipContractError as exc:
        raise RelationshipTimelineUnavailable(str(exc)) from exc
    return {
        "edge": edge,
        "source_fact": {
            "role": role,
            "relation_key": fact["relation_key"],
            "entity_id": entity_id or None,
            "name": str(fact.get("name") or "") or None,
            "address": str(fact.get("address") or "") or None,
        },
    }


def _relationship_items(application: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    states: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in sorted(rows, key=_rank):
        event_type = str(row.get("event_type") or "")
        role, is_superseded = _EVENT_ROLE[event_type]
        value_field = "old_value_compact" if is_superseded else "new_value_compact"
        fact = _fact_value(row.get(value_field), value_field)
        key = role, fact["relation_key"]
        lifecycles = states.setdefault(key, [])
        if is_superseded:
            if not lifecycles or lifecycles[-1]["superseded"] is not None:
                raise RelationshipTimelineUnavailable(
                    "relationship supersession has no retained observed event"
                )
            lifecycles[-1]["superseded"] = row
            continue
        if not lifecycles or lifecycles[-1]["superseded"] is not None:
            lifecycles.append({"observed": row, "superseded": None, "fact": fact})
        else:
            lifecycles[-1]["observed"] = row
            lifecycles[-1]["fact"] = fact

    items: list[dict[str, Any]] = []
    for (role, _relation_key), lifecycles in states.items():
        for state in lifecycles:
            items.append(
                _edge_item(
                    application=application,
                    role=role,
                    fact=state["fact"],
                    observed=state["observed"],
                    superseded=state["superseded"],
                )
            )
    return sorted(items, key=lambda item: str(item["edge"]["edge_id"]))


def relationships_for_trademark(
    client: Any, application_number: str, *, scope: str = "all"
) -> dict[str, Any]:
    application = _application_number(application_number)
    selected_scope = _scope(scope)
    budget = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_RELATIONSHIP_EVENTS + 1}
    readiness = _rows(
        client,
        """
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'CN_RELATIONSHIP_TIMELINE'
        LIMIT 1
        """,
        budget,
    )
    if not readiness or str(readiness[0]["version"]) != RELATIONSHIP_TIMELINE_READY_VERSION:
        raise RelationshipTimelineUnavailable(
            "CN relationship timeline is not backfilled and accepted"
        )
    rows = _rows(
        client,
        f"""
        SELECT event_id, application_number, event_type, event_date, observed_at,
               field_name, old_value_compact, new_value_compact, evidence_level,
               toString(source_package_id) AS source_package_id, source_package_kind,
               source_file, source_first_line, source_last_line, source_row_hash,
               source_rank, event_hash
        FROM {CN_RELATIONSHIP_TIMELINE_TABLE} FINAL
        WHERE application_number = {_sql_literal(application)}
        ORDER BY application_number, source_rank, event_hash
        LIMIT {MAX_RELATIONSHIP_EVENTS + 1}
        """,
        budget,
    )
    if len(rows) > MAX_RELATIONSHIP_EVENTS:
        raise RelationshipTimelineScopeExceeded(
            f"trademark has more than {MAX_RELATIONSHIP_EVENTS} relationship events"
        )
    items = _relationship_items(application, rows)
    if selected_scope != "all":
        current = selected_scope == "current"
        items = [item for item in items if item["edge"]["temporal"]["is_current"] is current]
    return {
        "application_number": application,
        "scope": selected_scope,
        "relationship_count": len(items),
        "relationships": items,
        "semantics": "CNIPA_RELATIONSHIP_OBSERVATION_HISTORY_NOT_LEGAL_EFFECTIVE_DATES",
    }
