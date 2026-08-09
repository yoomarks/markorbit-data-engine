from __future__ import annotations

from typing import Any

from app.db import clickhouse_client
from app.us_ttab import TTAB_SCHEMA_VERSION, TTAB_SEMANTICS


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


def validate_proceeding_number(value: str) -> str:
    value = value.strip()
    if not (value.isdigit() and 6 <= len(value) <= 8):
        raise ValueError("proceeding_number must contain 6 to 8 digits")
    return value


def validate_serial_number(value: str) -> str:
    value = value.strip()
    if not (value.isdigit() and len(value) == 8):
        raise ValueError("serial_number must contain exactly 8 digits")
    return value


def latest_proceeding_record(proceeding_number: str) -> dict[str, Any] | None:
    number = validate_proceeding_number(proceeding_number)
    rows = _rows(
        f"""
        SELECT proceeding_number, proceeding_type, filing_date, filing_date_raw,
               status_text, status_date, status_date_raw, general_contact_number,
               interlocutory_attorney, paralegal_name, record_hash, source_kind,
               source_snapshot_at, source_file, toString(source_package_id) AS source_package_id,
               source_rank
        FROM markorbit_facts.us_ttab_proceeding_history
        WHERE proceeding_number = '{number}'
        ORDER BY source_rank DESC, source_package_id DESC
        LIMIT 1
        """
    )
    return rows[0] if rows else None


def snapshot_children(record: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    number = str(record["proceeding_number"])
    package_id = str(record["source_package_id"])
    parties = _rows(
        f"""
        SELECT side, ordinal, party_name, correspondent_name, correspondent_address,
               correspondent_email_text, correspondent_phone, party_key, record_hash
        FROM markorbit_facts.us_ttab_party_history
        WHERE proceeding_number = '{number}'
          AND source_package_id = toUUID('{package_id}')
        ORDER BY side, ordinal, party_name
        """
    )
    properties = _rows(
        f"""
        SELECT party_side, party_ordinal, ordinal, serial_number, registration_number,
               mark_text, application_status, property_key, record_hash
        FROM markorbit_facts.us_ttab_property_history
        WHERE proceeding_number = '{number}'
          AND source_package_id = toUUID('{package_id}')
        ORDER BY party_side, party_ordinal, ordinal
        """
    )
    docket = _rows(
        f"""
        SELECT ordinal, entry_number, filing_date, filing_date_raw, history_text,
               due_date, due_date_raw, document_url, docket_key, record_hash
        FROM markorbit_facts.us_ttab_docket_history
        WHERE proceeding_number = '{number}'
          AND source_package_id = toUUID('{package_id}')
        ORDER BY ordinal, entry_number
        """
    )
    return {"parties": parties, "properties": properties, "docket": docket}


def proceeding_snapshot(proceeding_number: str) -> dict[str, Any] | None:
    record = latest_proceeding_record(proceeding_number)
    if record is None:
        return None
    children = snapshot_children(record)
    due_date_observations = [
        {
            "entry_number": item["entry_number"],
            "history_text": item["history_text"],
            "due_date": item["due_date"],
            "due_date_raw": item["due_date_raw"],
        }
        for item in children["docket"]
        if item.get("due_date") is not None or str(item.get("due_date_raw") or "").strip()
    ]
    return {
        "schema_version": TTAB_SCHEMA_VERSION,
        "proceeding": record,
        **children,
        "due_date_observations": due_date_observations,
        "semantics": TTAB_SEMANTICS,
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


def proceedings_for_serial(serial_number: str, limit: int = 100) -> list[dict[str, Any]]:
    serial = validate_serial_number(serial_number)
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    rows = _rows(
        f"""
        WITH latest AS
        (
            SELECT proceeding_number,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
            FROM markorbit_facts.us_ttab_proceeding_history
            GROUP BY proceeding_number
        )
        SELECT p.proceeding_number AS proceeding_number,
               p.party_side AS party_side,
               p.mark_text AS mark_text,
               p.registration_number AS registration_number,
               p.application_status AS application_status,
               r.proceeding_type AS proceeding_type,
               r.filing_date AS filing_date,
               r.status_text AS status_text,
               r.status_date AS status_date,
               r.source_snapshot_at AS source_snapshot_at,
               r.source_rank AS source_rank,
               toString(r.source_package_id) AS source_package_id
        FROM markorbit_facts.us_ttab_property_history AS p
        INNER JOIN latest AS l
          ON p.proceeding_number = l.proceeding_number
         AND toString(p.source_package_id) = l.package_id
        INNER JOIN markorbit_facts.us_ttab_proceeding_history AS r
          ON r.proceeding_number = p.proceeding_number
         AND r.source_package_id = p.source_package_id
        WHERE p.serial_number = '{serial}'
        ORDER BY r.filing_date DESC NULLS LAST, r.source_rank DESC, p.proceeding_number DESC
        LIMIT {int(limit)}
        """
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        number = str(row["proceeding_number"])
        if number in seen:
            continue
        seen.add(number)
        result.append(row)
    return result
