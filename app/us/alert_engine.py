from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
import re
import uuid

from app.db import clickhouse_client
from app.us.change_history import scan_change_feed_page
from app.us.deadline_portfolio import scan_deadline_candidate_page
from app.us.event_roles import load_active_event_role_map
from app.us.publisher import stable_hash


ALERT_ENGINE_VERSION = "US_ALERT_ENGINE_M1.0"
ALERT_ENGINE_SEMANTICS = (
    "SUBSCRIPTION_READY_READ_ONLY_EVENTS_PRESERVE_SOURCE_BOUNDARIES_NOT_LEGAL_CONCLUSIONS"
)
DELIVERY_SEMANTICS = "AT_LEAST_ONCE_POLL_CONSUMER_DEDUPE_BY_STABLE_EVENT_ID"
_SAFE_CURSOR_RE = re.compile(r"^[A-Za-z0-9_./:-]{0,160}$")


_EVENT_ROLE_TYPES = {
    "OFFICE_ACTION_NONFINAL_ISSUED": "OA_NONFINAL_ISSUED",
    "OFFICE_ACTION_FINAL_ISSUED": "OA_FINAL_ISSUED",
    "OFFICE_ACTION_RESPONSE_FILED": "OA_RESPONSE_OBSERVED",
    "NOTICE_OF_ALLOWANCE_ISSUED": "NOA_ISSUED",
    "STATEMENT_OF_USE_FILED": "SOU_FILED",
    "ITU_EXTENSION_GRANTED": "ITU_EXTENSION_GRANTED",
    "OPPOSITION_EXTENSION_30_GRANTED": "OPPOSITION_EXTENSION_GRANTED",
    "OPPOSITION_EXTENSION_90_GRANTED": "OPPOSITION_EXTENSION_GRANTED",
    "OPPOSITION_EXTENSION_150_GRANTED": "OPPOSITION_EXTENSION_GRANTED",
}


def _normalize_value(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    if isinstance(value, tuple):
        return tuple(_normalize_value(item) for item in value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def _rows(sql: str) -> list[dict[str, Any]]:
    result = clickhouse_client().query(sql)
    return [
        {
            name: _normalize_value(value)
            for name, value in zip(result.column_names, row, strict=True)
        }
        for row in result.result_rows
    ]


def _cursor_text(value: str, label: str) -> str:
    cleaned = value.strip()
    if not _SAFE_CURSOR_RE.fullmatch(cleaned):
        raise ValueError(f"{label} contains unsupported cursor characters")
    return cleaned


def _cursor_uuid(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    try:
        return str(uuid.UUID(cleaned))
    except ValueError as exc:
        raise ValueError(f"{label} must be empty or a UUID") from exc


def _event(
    *,
    event_type: str,
    source_domain: str,
    subject_key: str,
    payload: dict[str, Any],
    serial_number: str = "",
    registration_number: str = "",
    proceeding_number: str = "",
    source_rank: int | None = None,
    source_package_id: str = "",
    source_effective_date: object = None,
    event_date: object = None,
    due_date: object = None,
    urgency: str = "",
    label: str = "",
) -> dict[str, Any]:
    event_id = stable_hash(
        {
            "version": ALERT_ENGINE_VERSION,
            "event_type": event_type,
            "source_domain": source_domain,
            "subject_key": subject_key,
            "serial_number": serial_number,
            "proceeding_number": proceeding_number,
        }
    )
    return {
        "event_id": event_id,
        "event_type": event_type,
        "source_domain": source_domain,
        "serial_number": serial_number,
        "registration_number": registration_number,
        "proceeding_number": proceeding_number,
        "subject_key": subject_key,
        "source_rank": source_rank,
        "source_package_id": source_package_id,
        "source_effective_date": source_effective_date,
        "event_date": event_date,
        "due_date": due_date,
        "urgency": urgency,
        "label": label,
        "actionability": "REVIEW_REQUIRED",
        "payload": payload,
        "delivery_semantics": DELIVERY_SEMANTICS,
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
        "ttab_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def alert_engine_schema() -> dict[str, Any]:
    return {
        "version": ALERT_ENGINE_VERSION,
        "semantics": ALERT_ENGINE_SEMANTICS,
        "delivery_semantics": DELIVERY_SEMANTICS,
        "feeds": {
            "case_changes": {
                "cursor": ["source_rank", "serial_number"],
                "source_domain": "US_M1.4_DURABLE_CHANGE_HISTORY",
            },
            "assignments": {
                "cursor": ["source_rank", "reel_frame_id", "source_package_id"],
                "source_domain": "US_ASSIGNMENT_M1.0",
            },
            "ttab": {
                "cursor": ["source_rank", "proceeding_number", "source_package_id"],
                "source_domain": "US_TTAB_M1.1",
            },
            "reviewed_events": {
                "cursor": ["source_rank", "event_key"],
                "source_domain": "US_EVENT_HISTORY_PLUS_REVIEWED_EVENT_ROLE",
            },
            "deadlines": {
                "cursor": ["serial_number"],
                "source_domain": "US_DEADLINE_PORTFOLIO",
                "mode": "SNAPSHOT_CANDIDATE_SCAN",
            },
        },
        "global_source_rank_ordering": False,
        "reason": "US application, Assignment, TTAB and derived deadline domains retain independent source precedence.",
        "consumer_dedupe_key": "event_id",
        "source_boundary_preserved": True,
        "webhook_delivery_included": False,
        "subscription_storage_included": False,
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
        "ttab_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def _change_event_types(change: dict[str, Any]) -> list[str]:
    raw = set(change.get("change_types") or [])
    result: list[str] = []
    if "OWNER_IDENTITY_SET_CHANGED" in raw:
        result.append("CASE_OWNER_CHANGED")
    if "OWNER_DETAILS_CHANGED" in raw:
        result.append("CASE_OWNER_DETAILS_CHANGED")
    if raw & {"STATUS_CODE_CHANGED", "STATUS_DATE_CHANGED"}:
        result.append("CASE_STATUS_CHANGED")
    if "MAINTENANCE_FLAG_CHANGED" in raw:
        result.append("CASE_MAINTENANCE_FACT_CHANGED")
    if "PROCEEDING_FLAG_CHANGED" in raw:
        result.append("CASE_PROCEEDING_FLAG_CHANGED")
    covered = {
        "OWNER_IDENTITY_SET_CHANGED",
        "OWNER_DETAILS_CHANGED",
        "STATUS_CODE_CHANGED",
        "STATUS_DATE_CHANGED",
        "MAINTENANCE_FLAG_CHANGED",
        "PROCEEDING_FLAG_CHANGED",
    }
    if raw - covered:
        result.append("CASE_FACT_CHANGED")
    return result or ["CASE_FACT_CHANGED"]


def normalize_case_change_page(page: dict[str, Any]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for change in page.get("changes") or []:
        for event_type in _change_event_types(change):
            events.append(
                _event(
                    event_type=event_type,
                    source_domain="US_M1.4_DURABLE_CHANGE_HISTORY",
                    subject_key=f"{change['change_id']}:{event_type}",
                    serial_number=str(change.get("serial_number") or ""),
                    source_rank=int(change.get("source_rank") or 0),
                    source_package_id=str(change.get("source_package_id") or ""),
                    source_effective_date=change.get("source_effective_date"),
                    label=event_type.replace("_", " ").title(),
                    payload={
                        "change_id": change.get("change_id"),
                        "change_types": change.get("change_types") or [],
                        "field_changes": change.get("field_changes") or {},
                        "source_file": change.get("source_file"),
                        "source_semantics": change.get("semantics"),
                    },
                )
            )
    return {
        "version": ALERT_ENGINE_VERSION,
        "feed": "case_changes",
        "delivery_semantics": DELIVERY_SEMANTICS,
        "source_cursor": {
            "after_source_rank": page.get("after_source_rank", 0),
            "after_serial": page.get("after_serial", ""),
        },
        "next_cursor": page.get("next_cursor") or {},
        "has_more": bool(page.get("has_more_observations")),
        "event_count": len(events),
        "events": events,
        "source_semantics": page.get("semantics"),
    }


def scan_case_change_alerts(
    *,
    after_source_rank: int = 0,
    after_serial: str = "",
    scan_limit: int = 200,
) -> dict[str, Any]:
    return normalize_case_change_page(
        scan_change_feed_page(
            after_source_rank=after_source_rank,
            after_serial=after_serial,
            scan_limit=scan_limit,
        )
    )


def _assignment_first_rows(
    *,
    after_source_rank: int,
    after_reel_frame: str,
    after_package_id: str,
    scan_limit: int,
) -> list[dict[str, Any]]:
    reel = _cursor_text(after_reel_frame, "after_reel_frame")
    package_id = _cursor_uuid(after_package_id, "after_package_id")
    if after_source_rank < 0:
        raise ValueError("after_source_rank must be non-negative")
    if not 1 <= scan_limit <= 1000:
        raise ValueError("scan_limit must be between 1 and 1000")
    cursor_package = package_id or "00000000-0000-0000-0000-000000000000"
    return _rows(
        f"""
        WITH first_observation AS
        (
            SELECT reel_frame_id,
                   min(source_rank) AS first_source_rank,
                   argMin(
                       toString(source_package_id),
                       tuple(source_rank, toString(source_package_id))
                   ) AS first_package_id
            FROM markorbit_facts.us_assignment_record_history
            GROUP BY reel_frame_id
        )
        SELECT r.reel_frame_id AS reel_frame_id,
               r.reel_no AS reel_no,
               r.frame_no AS frame_no,
               r.recorded_date AS recorded_date,
               r.recorded_date_raw AS recorded_date_raw,
               r.conveyance_text AS conveyance_text,
               r.source_kind AS source_kind,
               r.source_effective_date AS source_effective_date,
               r.source_file AS source_file,
               toString(r.source_package_id) AS source_package_id,
               r.source_rank AS source_rank,
               r.observed_at AS observed_at
        FROM markorbit_facts.us_assignment_record_history AS r
        INNER JOIN first_observation AS f
          ON r.reel_frame_id = f.reel_frame_id
         AND r.source_rank = f.first_source_rank
         AND toString(r.source_package_id) = f.first_package_id
        WHERE r.source_rank > {int(after_source_rank)}
           OR (r.source_rank = {int(after_source_rank)} AND r.reel_frame_id > '{reel}')
           OR (
                r.source_rank = {int(after_source_rank)}
                AND r.reel_frame_id = '{reel}'
                AND toString(r.source_package_id) > '{cursor_package}'
           )
        ORDER BY r.source_rank, r.reel_frame_id, source_package_id
        LIMIT {int(scan_limit)}
        """
    )


def _exact_source_conditions(rows: list[dict[str, Any]]) -> str:
    return " OR ".join(
        "(reel_frame_id = '"
        + str(row["reel_frame_id"]).replace("'", "''")
        + "' AND toString(source_package_id) = '"
        + str(row["source_package_id"])
        + "')"
        for row in rows
    )


def _assignment_children(
    records: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
    if not records:
        return {}, {}
    conditions = _exact_source_conditions(records)
    properties = _rows(
        f"""
        SELECT reel_frame_id, toString(source_package_id) AS source_package_id,
               serial_number, registration_number, international_registration_number
        FROM markorbit_facts.us_assignment_property_history
        WHERE {conditions}
        ORDER BY reel_frame_id, ordinal, property_key
        """
    )
    assignees = _rows(
        f"""
        SELECT reel_frame_id, toString(source_package_id) AS source_package_id, party_name
        FROM markorbit_facts.us_assignment_assignee_history
        WHERE {conditions} AND party_name != ''
        ORDER BY reel_frame_id, ordinal, party_name
        """
    )
    props_by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names_by_record: dict[str, list[str]] = defaultdict(list)
    for row in properties:
        key = f"{row['reel_frame_id']}|{row['source_package_id']}"
        props_by_record[key].append(row)
    for row in assignees:
        key = f"{row['reel_frame_id']}|{row['source_package_id']}"
        name = str(row.get("party_name") or "")
        if name and name not in names_by_record[key]:
            names_by_record[key].append(name)
    return dict(props_by_record), dict(names_by_record)


def scan_assignment_alerts(
    *,
    after_source_rank: int = 0,
    after_reel_frame: str = "",
    after_package_id: str = "",
    scan_limit: int = 200,
) -> dict[str, Any]:
    records = _assignment_first_rows(
        after_source_rank=after_source_rank,
        after_reel_frame=after_reel_frame,
        after_package_id=after_package_id,
        scan_limit=scan_limit,
    )
    properties, assignees = _assignment_children(records)
    events: list[dict[str, Any]] = []
    for record in records:
        record_key = f"{record['reel_frame_id']}|{record['source_package_id']}"
        linked = properties.get(record_key, [])
        serials = sorted(
            {
                str(item.get("serial_number") or "")
                for item in linked
                if len(str(item.get("serial_number") or "")) == 8
                and str(item.get("serial_number") or "").isdigit()
            }
        )
        registrations = sorted(
            {str(item.get("registration_number") or "") for item in linked if item.get("registration_number")}
        )
        subjects = serials or [""]
        for serial in subjects:
            events.append(
                _event(
                    event_type="NEW_RECORDED_ASSIGNMENT",
                    source_domain="US_ASSIGNMENT_M1.0",
                    subject_key=f"{record['reel_frame_id']}:{serial or 'UNLINKED'}",
                    serial_number=serial,
                    registration_number=registrations[0] if len(registrations) == 1 else "",
                    source_rank=int(record.get("source_rank") or 0),
                    source_package_id=str(record.get("source_package_id") or ""),
                    source_effective_date=record.get("source_effective_date"),
                    event_date=record.get("recorded_date"),
                    label="New USPTO recorded assignment observation",
                    payload={
                        "reel_frame_id": record.get("reel_frame_id"),
                        "reel_no": record.get("reel_no"),
                        "frame_no": record.get("frame_no"),
                        "recorded_date_raw": record.get("recorded_date_raw"),
                        "conveyance_text": record.get("conveyance_text"),
                        "assignee_names": assignees.get(record_key, []),
                        "linked_serial_numbers": serials,
                        "linked_registration_numbers": registrations,
                        "source_kind": record.get("source_kind"),
                        "source_file": record.get("source_file"),
                        "semantics": "RECORDED_ASSIGNMENT_OBSERVATION_NOT_LEGAL_TITLE_CONCLUSION",
                    },
                )
            )
    if records:
        last = records[-1]
        next_cursor = {
            "source_rank": int(last["source_rank"]),
            "reel_frame_id": str(last["reel_frame_id"]),
            "source_package_id": str(last["source_package_id"]),
        }
    else:
        next_cursor = {
            "source_rank": int(after_source_rank),
            "reel_frame_id": after_reel_frame,
            "source_package_id": after_package_id,
        }
    return {
        "version": ALERT_ENGINE_VERSION,
        "feed": "assignments",
        "delivery_semantics": DELIVERY_SEMANTICS,
        "record_count": len(records),
        "event_count": len(events),
        "has_more": len(records) == scan_limit,
        "next_cursor": next_cursor,
        "events": events,
        "source_semantics": "FIRST_RECORDED_REEL_FRAME_OBSERVATION_NOT_LEGAL_OWNERSHIP_CONCLUSION",
    }


def _ttab_first_rows(
    *,
    after_source_rank: int,
    after_proceeding: str,
    after_package_id: str,
    scan_limit: int,
) -> list[dict[str, Any]]:
    proceeding = _cursor_text(after_proceeding, "after_proceeding")
    package_id = _cursor_uuid(after_package_id, "after_package_id")
    if after_source_rank < 0:
        raise ValueError("after_source_rank must be non-negative")
    if not 1 <= scan_limit <= 1000:
        raise ValueError("scan_limit must be between 1 and 1000")
    cursor_package = package_id or "00000000-0000-0000-0000-000000000000"
    return _rows(
        f"""
        WITH first_observation AS
        (
            SELECT proceeding_number,
                   min(source_rank) AS first_source_rank,
                   argMin(
                       toString(source_package_id),
                       tuple(source_rank, toString(source_package_id))
                   ) AS first_package_id
            FROM markorbit_facts.us_ttab_proceeding_history
            GROUP BY proceeding_number
        )
        SELECT r.proceeding_number AS proceeding_number,
               r.proceeding_type AS proceeding_type,
               r.proceeding_type_code AS proceeding_type_code,
               r.filing_date AS filing_date,
               r.filing_date_raw AS filing_date_raw,
               r.status_text AS status_text,
               r.status_code AS status_code,
               r.status_date AS status_date,
               r.source_kind AS source_kind,
               r.source_snapshot_at AS source_snapshot_at,
               r.source_file AS source_file,
               toString(r.source_package_id) AS source_package_id,
               r.source_rank AS source_rank
        FROM markorbit_facts.us_ttab_proceeding_history AS r
        INNER JOIN first_observation AS f
          ON r.proceeding_number = f.proceeding_number
         AND r.source_rank = f.first_source_rank
         AND toString(r.source_package_id) = f.first_package_id
        WHERE r.source_rank > {int(after_source_rank)}
           OR (r.source_rank = {int(after_source_rank)} AND r.proceeding_number > '{proceeding}')
           OR (
                r.source_rank = {int(after_source_rank)}
                AND r.proceeding_number = '{proceeding}'
                AND toString(r.source_package_id) > '{cursor_package}'
           )
        ORDER BY r.source_rank, r.proceeding_number, source_package_id
        LIMIT {int(scan_limit)}
        """
    )


def _ttab_properties(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not records:
        return {}
    conditions = " OR ".join(
        "(proceeding_number = '"
        + str(row["proceeding_number"]).replace("'", "''")
        + "' AND toString(source_package_id) = '"
        + str(row["source_package_id"])
        + "')"
        for row in records
    )
    rows = _rows(
        f"""
        SELECT proceeding_number, toString(source_package_id) AS source_package_id,
               serial_number, registration_number, party_side
        FROM markorbit_facts.us_ttab_property_history
        WHERE {conditions}
        ORDER BY proceeding_number, party_side, party_ordinal, ordinal
        """
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[f"{row['proceeding_number']}|{row['source_package_id']}"] .append(row)
    return dict(grouped)


def scan_ttab_alerts(
    *,
    after_source_rank: int = 0,
    after_proceeding: str = "",
    after_package_id: str = "",
    scan_limit: int = 200,
) -> dict[str, Any]:
    records = _ttab_first_rows(
        after_source_rank=after_source_rank,
        after_proceeding=after_proceeding,
        after_package_id=after_package_id,
        scan_limit=scan_limit,
    )
    properties = _ttab_properties(records)
    events: list[dict[str, Any]] = []
    for record in records:
        record_key = f"{record['proceeding_number']}|{record['source_package_id']}"
        linked = properties.get(record_key, [])
        serials = sorted(
            {
                str(item.get("serial_number") or "")
                for item in linked
                if len(str(item.get("serial_number") or "")) == 8
                and str(item.get("serial_number") or "").isdigit()
            }
        )
        registrations = sorted(
            {str(item.get("registration_number") or "") for item in linked if item.get("registration_number")}
        )
        subjects = serials or [""]
        for serial in subjects:
            events.append(
                _event(
                    event_type="TTAB_NEW_PROCEEDING",
                    source_domain="US_TTAB_M1.1",
                    subject_key=f"{record['proceeding_number']}:{serial or 'UNLINKED'}",
                    serial_number=serial,
                    registration_number=registrations[0] if len(registrations) == 1 else "",
                    proceeding_number=str(record.get("proceeding_number") or ""),
                    source_rank=int(record.get("source_rank") or 0),
                    source_package_id=str(record.get("source_package_id") or ""),
                    source_effective_date=record.get("source_snapshot_at"),
                    event_date=record.get("filing_date"),
                    label="New TTAB proceeding observation",
                    payload={
                        "proceeding_type": record.get("proceeding_type"),
                        "proceeding_type_code": record.get("proceeding_type_code"),
                        "filing_date_raw": record.get("filing_date_raw"),
                        "status_text": record.get("status_text"),
                        "status_code": record.get("status_code"),
                        "status_date": record.get("status_date"),
                        "linked_serial_numbers": serials,
                        "linked_registration_numbers": registrations,
                        "source_kind": record.get("source_kind"),
                        "source_file": record.get("source_file"),
                        "semantics": "TTAB_PROCEDURAL_FACT_OBSERVATION_NOT_OUTCOME_CONCLUSION",
                    },
                )
            )
    if records:
        last = records[-1]
        next_cursor = {
            "source_rank": int(last["source_rank"]),
            "proceeding_number": str(last["proceeding_number"]),
            "source_package_id": str(last["source_package_id"]),
        }
    else:
        next_cursor = {
            "source_rank": int(after_source_rank),
            "proceeding_number": after_proceeding,
            "source_package_id": after_package_id,
        }
    return {
        "version": ALERT_ENGINE_VERSION,
        "feed": "ttab",
        "delivery_semantics": DELIVERY_SEMANTICS,
        "record_count": len(records),
        "event_count": len(events),
        "has_more": len(records) == scan_limit,
        "next_cursor": next_cursor,
        "events": events,
        "source_semantics": "FIRST_TTAB_PROCEEDING_OBSERVATION_NOT_BOARD_OUTCOME_CONCLUSION",
    }


def _reviewed_event_rows(
    *,
    event_codes: list[str],
    after_source_rank: int,
    after_event_key: str,
    scan_limit: int,
) -> list[dict[str, Any]]:
    if after_source_rank < 0:
        raise ValueError("after_source_rank must be non-negative")
    if not 1 <= scan_limit <= 1000:
        raise ValueError("scan_limit must be between 1 and 1000")
    event_key = _cursor_text(after_event_key, "after_event_key")
    if event_key and (len(event_key) != 64 or not all(ch in "0123456789abcdefABCDEF" for ch in event_key)):
        raise ValueError("after_event_key must be empty or a 64-character hexadecimal key")
    if not event_codes:
        return []
    code_literals = ",".join("'" + code.replace("'", "''") + "'" for code in event_codes)
    return _rows(
        f"""
        SELECT toString(event_key) AS event_key,
               serial_number, event_code, event_date, event_sequence,
               event_type_code, description_text,
               source_package_kind, source_effective_date, source_file,
               toString(source_package_id) AS source_package_id,
               source_rank, observed_at
        FROM markorbit_facts.us_event_history FINAL
        WHERE event_code IN ({code_literals})
          AND (
               source_rank > {int(after_source_rank)}
               OR (source_rank = {int(after_source_rank)} AND toString(event_key) > '{event_key}')
          )
        ORDER BY source_rank, event_key
        LIMIT {int(scan_limit)}
        """
    )


def scan_reviewed_event_alerts(
    *,
    raw_root: Path,
    after_source_rank: int = 0,
    after_event_key: str = "",
    scan_limit: int = 200,
) -> dict[str, Any]:
    role_state = load_active_event_role_map(raw_root)
    roles = role_state.get("roles") or {}
    if role_state.get("status") != "PASS":
        return {
            "version": ALERT_ENGINE_VERSION,
            "feed": "reviewed_events",
            "delivery_semantics": DELIVERY_SEMANTICS,
            "event_role_state": {
                "status": role_state.get("status"),
                "reason": role_state.get("reason"),
            },
            "record_count": 0,
            "event_count": 0,
            "has_more": False,
            "next_cursor": {
                "source_rank": int(after_source_rank),
                "event_key": after_event_key,
            },
            "events": [],
            "source_semantics": "NO_UNREVIEWED_EVENT_CODE_INFERENCE",
        }
    rows = _reviewed_event_rows(
        event_codes=sorted(roles),
        after_source_rank=after_source_rank,
        after_event_key=after_event_key,
        scan_limit=scan_limit,
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        mapping = roles.get(str(row.get("event_code") or "")) or {}
        role = str(mapping.get("role") or "")
        event_type = _EVENT_ROLE_TYPES.get(role, "REVIEWED_PROCEDURAL_EVENT")
        events.append(
            _event(
                event_type=event_type,
                source_domain="US_EVENT_HISTORY_PLUS_REVIEWED_EVENT_ROLE",
                subject_key=f"{row['event_key']}:{role}",
                serial_number=str(row.get("serial_number") or ""),
                source_rank=int(row.get("source_rank") or 0),
                source_package_id=str(row.get("source_package_id") or ""),
                source_effective_date=row.get("source_effective_date"),
                event_date=row.get("event_date"),
                label=event_type.replace("_", " ").title(),
                payload={
                    "event_key": row.get("event_key"),
                    "event_code": row.get("event_code"),
                    "event_sequence": row.get("event_sequence"),
                    "event_type_code": row.get("event_type_code"),
                    "description_text": row.get("description_text"),
                    "reviewed_role": role,
                    "rule_id": mapping.get("rule_id"),
                    "rationale": mapping.get("rationale"),
                    "source_refs": mapping.get("source_refs"),
                    "ruleset_version": (role_state.get("ruleset") or {}).get("ruleset_version"),
                    "source_file": row.get("source_file"),
                    "semantics": "REVIEWED_EVENT_ROLE_MAPPING_NOT_USPTO_RAW_FACT",
                },
            )
        )
    if rows:
        last = rows[-1]
        next_cursor = {
            "source_rank": int(last["source_rank"]),
            "event_key": str(last["event_key"]),
        }
    else:
        next_cursor = {
            "source_rank": int(after_source_rank),
            "event_key": after_event_key,
        }
    return {
        "version": ALERT_ENGINE_VERSION,
        "feed": "reviewed_events",
        "delivery_semantics": DELIVERY_SEMANTICS,
        "event_role_state": {
            "status": role_state.get("status"),
            "reason": role_state.get("reason"),
            "ruleset_version": (role_state.get("ruleset") or {}).get("ruleset_version"),
        },
        "record_count": len(rows),
        "event_count": len(events),
        "has_more": len(rows) == scan_limit,
        "next_cursor": next_cursor,
        "events": events,
        "source_semantics": "OFFICIAL_EVENT_FACT_PLUS_EVIDENCE_BOUND_REVIEWED_ROLE",
    }


def _deadline_event_type(candidate: dict[str, Any]) -> str:
    family = str(candidate.get("family") or "")
    code = str(candidate.get("code") or "")
    if family == "MAINTENANCE":
        return "MAINTENANCE_WINDOW"
    if code in {"NONFINAL_OFFICE_ACTION_RESPONSE", "FINAL_OFFICE_ACTION_RESPONSE"}:
        return "OA_DEADLINE_CANDIDATE"
    if code == "ITU_SOU_OR_EXTENSION":
        return "NOA_SOU_DEADLINE_CANDIDATE"
    if family == "PUBLICATION":
        return "PUBLICATION_DEADLINE_CANDIDATE"
    return "DEADLINE_CANDIDATE"


def normalize_deadline_page(page: dict[str, Any]) -> dict[str, Any]:
    events = [
        _event(
            event_type=_deadline_event_type(candidate),
            source_domain="US_DEADLINE_PORTFOLIO",
            subject_key=str(candidate["candidate_id"]),
            serial_number=str(candidate.get("serial_number") or ""),
            registration_number=str(candidate.get("registration_number") or ""),
            due_date=candidate.get("due_date"),
            urgency=str(candidate.get("urgency") or ""),
            label=str(candidate.get("label") or ""),
            payload={
                "candidate_id": candidate.get("candidate_id"),
                "family": candidate.get("family"),
                "code": candidate.get("code"),
                "state": candidate.get("state"),
                "source": candidate.get("source"),
                "details": candidate.get("details") or {},
                "semantics": "DEADLINE_CANDIDATE_NOT_FINAL_DOCKET_OR_LEGAL_STATUS",
            },
        )
        for candidate in page.get("candidates") or []
    ]
    return {
        "version": ALERT_ENGINE_VERSION,
        "feed": "deadlines",
        "mode": "SNAPSHOT_CANDIDATE_SCAN",
        "delivery_semantics": DELIVERY_SEMANTICS,
        "as_of": page.get("as_of"),
        "after_serial": page.get("after_serial", ""),
        "last_scanned_serial": page.get("last_scanned_serial", ""),
        "has_more": bool(page.get("has_more_cases")),
        "event_count": len(events),
        "events": events,
        "event_role_state": page.get("event_role_state") or {},
        "source_semantics": page.get("semantics"),
    }


def scan_deadline_alerts(
    *,
    raw_root: Path,
    as_of: date,
    after_serial: str = "",
    scan_limit: int = 200,
    horizon_days: int = 90,
    recent_past_days: int = 30,
) -> dict[str, Any]:
    page = scan_deadline_candidate_page(
        raw_root=raw_root,
        as_of=as_of,
        after_serial=after_serial,
        scan_limit=scan_limit,
        result_limit=5000,
        horizon_days=horizon_days,
        recent_past_days=recent_past_days,
    )
    if page.get("result_truncated"):
        raise RuntimeError(
            "Deadline candidate buffer exceeded; reduce scan_limit to preserve lossless pagination"
        )
    return normalize_deadline_page(page)
