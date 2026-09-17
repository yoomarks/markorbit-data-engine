from __future__ import annotations

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
from app.us_assignment.api import _assignments_for_serial
from app.us_ttab.read_model import proceedings_for_serial, validate_serial_number


MAX_RELATIONSHIP_EDGES = 5_000
MAX_SOURCE_RECORDS = 500
RELATIONSHIP_MAPPING_VERSION = "US_RECORDED_RELATIONSHIP_TIMELINE_V1"

_TTAB_PARTY_RELATIONSHIP = {
    ("OPP", "PLAINTIFF"): "OPPOSITION_PLAINTIFF",
    ("OPP", "DEFENDANT"): "OPPOSITION_DEFENDANT",
    ("CAN", "PLAINTIFF"): "CANCELLATION_PETITIONER",
    ("CAN", "DEFENDANT"): "CANCELLATION_RESPONDENT",
    ("EXA", "PLAINTIFF"): "EX_PARTE_APPEAL_PARTY",
    ("EXA", "DEFENDANT"): "EX_PARTE_APPEAL_PARTY",
}


class USRelationshipTimelineInvalid(ValueError):
    pass


class USRelationshipTimelineScopeExceeded(RuntimeError):
    pass


class USRelationshipTimelineUnavailable(RuntimeError):
    pass


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return str(value or "")


def _scope(value: str) -> str:
    selected = str(value or "").strip().lower()
    if selected not in QUERY_SCOPES:
        raise USRelationshipTimelineInvalid("scope must be current, historical, or all")
    return selected


def _sql_literal(value: Any) -> str:
    return "'" + _text(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _identity_filter(records: list[Mapping[str, Any]], identity: str) -> str:
    clauses = [
        "("
        f"{identity} = {_sql_literal(record[identity])} AND "
        f"source_package_id = toUUID({_sql_literal(record['source_package_id'])})"
        ")"
        for record in records
    ]
    return " OR ".join(clauses) or "0"


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    budget = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_RELATIONSHIP_EDGES + 1}
    try:
        result = client.query(sql, settings=budget)
    except Exception as exc:
        raise USRelationshipTimelineUnavailable(str(exc)) from exc
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _iso_timestamp(value: Any) -> str:
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(_text(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise USRelationshipTimelineUnavailable("observed_at is not an ISO timestamp") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(_text(value).strip()).isoformat()
    except ValueError as exc:
        raise USRelationshipTimelineUnavailable("event date is not an ISO date") from exc


def _edge(
    *,
    relationship_type: str,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    row: Mapping[str, Any],
    source_domain: str,
    event_at: Any,
) -> dict[str, Any]:
    try:
        return build_temporal_relationship_edge(
            jurisdiction="US",
            relationship_type=relationship_type,
            source=RelationshipResourceRef(source_type, source_id),
            target=RelationshipResourceRef(target_type, target_id),
            authority_level="DIRECT_OFFICIAL",
            evidence=RelationshipEvidenceRef(
                evidence_kind="STRUCTURED_SOURCE",
                source_authority="USPTO",
                source_domain=source_domain,
                source_record_id=_text(row["observation_key"]),
                source_package_id=_text(row["source_package_id"]),
                source_uri=_text(row.get("source_file")) or None,
                source_hash=_text(row["record_hash"]),
            ),
            observed_at=_iso_timestamp(row["observed_at"]),
            event_at=_iso_date(event_at),
            is_current=False,
            derivation=RelationshipDerivationRef(
                "DIRECT_MAPPING", RELATIONSHIP_MAPPING_VERSION, "1"
            ),
        )
    except TemporalRelationshipContractError as exc:
        raise USRelationshipTimelineUnavailable(str(exc)) from exc


def _assignment_items(client: Any, serial: str) -> list[dict[str, Any]]:
    records = _assignments_for_serial(serial, MAX_SOURCE_RECORDS, client=client)
    if not records:
        return []
    record_by_id = {_text(record["reel_frame_id"]): record for record in records}
    identity_filter = _identity_filter(records, "reel_frame_id")
    common_columns = (
        "observation_key, reel_frame_id, record_hash, source_file, source_package_id, observed_at"
    )
    properties = _rows(
        client,
        f"""
        SELECT {common_columns}, property_key, ordinal, serial_number,
               registration_number, international_registration_number
        FROM markorbit_facts.us_assignment_property_history
        WHERE serial_number = {_sql_literal(serial)} AND ({identity_filter})
        ORDER BY reel_frame_id, property_key
        LIMIT {MAX_RELATIONSHIP_EDGES + 1}
        """,
    )
    party_rows = _rows(
        client,
        f"""
        SELECT 'ASSIGNOR' AS relationship_type, {common_columns}, party_key, ordinal,
               party_name, execution_date
        FROM markorbit_facts.us_assignment_assignor_history
        WHERE {identity_filter}
        UNION ALL
        SELECT 'ASSIGNEE' AS relationship_type, {common_columns}, party_key, ordinal,
               party_name, execution_date
        FROM markorbit_facts.us_assignment_assignee_history
        WHERE {identity_filter}
        ORDER BY reel_frame_id, party_key, relationship_type
        LIMIT {MAX_RELATIONSHIP_EDGES + 1}
        """,
    )

    items: list[dict[str, Any]] = []
    for row in properties:
        record = record_by_id[_text(row["reel_frame_id"])]
        items.append(
            {
                "edge": _edge(
                    relationship_type="ASSIGNMENT_PROPERTY",
                    source_type="TRADEMARK",
                    source_id=f"us:trademark:{serial}",
                    target_type="ASSIGNMENT",
                    target_id=f"us:assignment:{_text(row['reel_frame_id'])}",
                    row=row,
                    source_domain="US_ASSIGNMENT",
                    event_at=record.get("recorded_date"),
                ),
                "source_fact": {
                    "source_domain": "US_ASSIGNMENT",
                    "reel_frame_id": _text(row["reel_frame_id"]),
                    "property_key": _text(row["property_key"]),
                    "serial_number": serial,
                    "registration_number": _text(row.get("registration_number")) or None,
                    "recorded_date": _iso_date(record.get("recorded_date")),
                    "conveyance_text": _text(record.get("conveyance_text")) or None,
                },
            }
        )
    for row in party_rows:
        relationship = _text(row["relationship_type"])
        record = record_by_id[_text(row["reel_frame_id"])]
        items.append(
            {
                "edge": _edge(
                    relationship_type=relationship,
                    source_type="ENTITY",
                    source_id=f"us:assignment-party:{_text(row['party_key'])}",
                    target_type="ASSIGNMENT",
                    target_id=f"us:assignment:{_text(row['reel_frame_id'])}",
                    row=row,
                    source_domain="US_ASSIGNMENT",
                    event_at=record.get("recorded_date"),
                ),
                "source_fact": {
                    "source_domain": "US_ASSIGNMENT",
                    "reel_frame_id": _text(row["reel_frame_id"]),
                    "party_key": _text(row["party_key"]),
                    "party_name": _text(row.get("party_name")) or None,
                    "role": relationship,
                    "execution_date": _iso_date(row.get("execution_date")),
                    "recorded_date": _iso_date(record.get("recorded_date")),
                },
            }
        )
    return items


def _ttab_items(client: Any, serial: str) -> tuple[list[dict[str, Any]], int]:
    proceedings = proceedings_for_serial(serial, MAX_SOURCE_RECORDS, client=client)
    if not proceedings:
        return [], 0
    proceeding_by_id = {_text(record["proceeding_number"]): record for record in proceedings}
    identity_filter = _identity_filter(proceedings, "proceeding_number")
    common_columns = (
        "observation_key, proceeding_number, record_hash, source_file, "
        "source_package_id, observed_at"
    )
    source_rows = _rows(
        client,
        f"""
        SELECT 'PROPERTY' AS fact_kind, {common_columns}, property_key AS fact_key,
               party_side AS side, '' AS party_name, '' AS party_id, '' AS role,
               registration_number
        FROM markorbit_facts.us_ttab_property_history
        WHERE serial_number = {_sql_literal(serial)} AND ({identity_filter})
        UNION ALL
        SELECT 'PARTY' AS fact_kind, {common_columns}, party_key AS fact_key,
               side, party_name, party_id, role, '' AS registration_number
        FROM markorbit_facts.us_ttab_party_history
        WHERE {identity_filter}
        ORDER BY proceeding_number, fact_kind, fact_key
        LIMIT {MAX_RELATIONSHIP_EDGES + 1}
        """,
    )
    properties = [row for row in source_rows if row["fact_kind"] == "PROPERTY"]
    parties = [row for row in source_rows if row["fact_kind"] == "PARTY"]

    items: list[dict[str, Any]] = []
    for row in properties:
        proceeding = proceeding_by_id[_text(row["proceeding_number"])]
        items.append(
            {
                "edge": _edge(
                    relationship_type="PROCEEDING_PROPERTY",
                    source_type="TRADEMARK",
                    source_id=f"us:trademark:{serial}",
                    target_type="PROCEEDING",
                    target_id=f"us:ttab:{_text(row['proceeding_number'])}",
                    row=row,
                    source_domain="US_TTAB",
                    event_at=proceeding.get("filing_date"),
                ),
                "source_fact": {
                    "source_domain": "US_TTAB",
                    "proceeding_number": _text(row["proceeding_number"]),
                    "proceeding_type_code": _text(proceeding.get("proceeding_type_code")),
                    "property_key": _text(row["fact_key"]),
                    "party_side": _text(row.get("side")) or None,
                    "serial_number": serial,
                    "registration_number": _text(row.get("registration_number")) or None,
                    "filing_date": _iso_date(proceeding.get("filing_date")),
                },
            }
        )

    unmapped = 0
    for row in parties:
        proceeding = proceeding_by_id[_text(row["proceeding_number"])]
        mapping_key = (
            _text(proceeding.get("proceeding_type_code")).upper(),
            _text(row.get("side")).upper(),
        )
        relationship = _TTAB_PARTY_RELATIONSHIP.get(mapping_key)
        if relationship is None:
            unmapped += 1
            continue
        items.append(
            {
                "edge": _edge(
                    relationship_type=relationship,
                    source_type="ENTITY",
                    source_id=f"us:ttab-party:{_text(row['fact_key'])}",
                    target_type="PROCEEDING",
                    target_id=f"us:ttab:{_text(row['proceeding_number'])}",
                    row=row,
                    source_domain="US_TTAB",
                    event_at=proceeding.get("filing_date"),
                ),
                "source_fact": {
                    "source_domain": "US_TTAB",
                    "proceeding_number": _text(row["proceeding_number"]),
                    "proceeding_type_code": mapping_key[0],
                    "party_key": _text(row["fact_key"]),
                    "party_name": _text(row.get("party_name")) or None,
                    "party_side": mapping_key[1],
                    "source_role": _text(row.get("role")) or None,
                    "mapped_role": relationship,
                    "filing_date": _iso_date(proceeding.get("filing_date")),
                },
            }
        )
    return items, unmapped


def relationships_for_trademark(
    client: Any, serial_number: str, *, scope: str = "all"
) -> dict[str, Any]:
    try:
        serial = validate_serial_number(serial_number)
    except ValueError as exc:
        raise USRelationshipTimelineInvalid(
            "USPTO serial number must contain exactly 8 digits"
        ) from exc
    selected_scope = _scope(scope)
    try:
        assignments = _assignment_items(client, serial)
        ttab, unmapped = _ttab_items(client, serial)
    except USRelationshipTimelineUnavailable:
        raise
    except Exception as exc:
        raise USRelationshipTimelineUnavailable(str(exc)) from exc
    items = assignments + ttab
    if len(items) > MAX_RELATIONSHIP_EDGES:
        raise USRelationshipTimelineScopeExceeded(
            f"trademark resolves to more than {MAX_RELATIONSHIP_EDGES} relationship edges"
        )
    if selected_scope == "current":
        items = []
    return {
        "serial_number": serial,
        "scope": selected_scope,
        "relationship_count": len(items),
        "relationships": sorted(items, key=lambda item: str(item["edge"]["edge_id"])),
        "unmapped_ttab_party_count": unmapped,
        "semantics": (
            "USPTO_RECORDED_ASSIGNMENT_AND_TTAB_PROCEDURAL_HISTORY; "
            "NOT_LEGAL_TITLE_OUTCOME_OR_SUBSTANTIVE_RIGHTS_CONCLUSION"
        ),
        "current_relationship_inference": False,
    }
