from __future__ import annotations

from typing import Any

from app.db import clickhouse_client
from app.read_performance_baseline import DEFAULT_QUERY_BUDGET
from app.us_ttab import TTAB_SCHEMA_VERSION, TTAB_SEMANTICS


MAX_SERIAL_CANDIDATES = 500


class TTABQueryScopeExceeded(RuntimeError):
    pass


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


def _rows(
    sql: str, *, settings: dict[str, Any] | None = None, client: Any | None = None
) -> list[dict[str, Any]]:
    client = client or clickhouse_client()
    result = client.query(sql, settings=settings) if settings is not None else client.query(sql)
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


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def latest_proceeding_record(proceeding_number: str) -> dict[str, Any] | None:
    number = validate_proceeding_number(proceeding_number)
    rows = _rows(
        f"""
        SELECT proceeding_number, proceeding_type, proceeding_type_code,
               filing_date, filing_date_raw, status_text, status_code, status_date,
               status_date_raw, general_contact_number, interlocutory_attorney,
               paralegal_name, record_hash, source_kind, source_snapshot_at, source_file,
               toString(source_package_id) AS source_package_id, source_rank
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
        SELECT side, ordinal, party_name, party_id, role, company, organization,
               granted_to_date_raw, correspondent_name, correspondent_organization,
               correspondent_address, correspondent_email_text, correspondent_phone,
               party_key, record_hash
        FROM markorbit_facts.us_ttab_party_history
        WHERE proceeding_number = '{number}' AND source_package_id = toUUID('{package_id}')
        ORDER BY side, ordinal, party_name
        """
    )
    properties = _rows(
        f"""
        SELECT party_side, party_ordinal, ordinal, serial_number, registration_number,
               mark_text, mark_explanation, property_filing, property_filing_code,
               common_law_indicator, application_status, application_status_code,
               trademark_gid, property_key, record_hash
        FROM markorbit_facts.us_ttab_property_history
        WHERE proceeding_number = '{number}' AND source_package_id = toUUID('{package_id}')
        ORDER BY party_side, party_ordinal, ordinal
        """
    )
    docket = _rows(
        f"""
        SELECT ordinal, entry_number, identifier, object_id, entry_code, confidential,
               filing_date, filing_date_raw, history_text, due_date, due_date_raw,
               document_url, docket_key, record_hash
        FROM markorbit_facts.us_ttab_docket_history
        WHERE proceeding_number = '{number}' AND source_package_id = toUUID('{package_id}')
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
            "entry_code": item["entry_code"],
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


def proceedings_for_serial(
    serial_number: str, limit: int = 100, *, client: Any | None = None
) -> list[dict[str, Any]]:
    serial = validate_serial_number(serial_number)
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    budget = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_SERIAL_CANDIDATES + 1}
    candidates = _rows(
        f"""
        SELECT proceeding_number,
               groupArray(tuple(
                   toString(source_package_id), party_side, registration_number,
                   application_status, application_status_code, mark_explanation
               )) AS property_versions
        FROM markorbit_facts.us_ttab_property_history
        WHERE serial_number = '{serial}'
        GROUP BY proceeding_number
        ORDER BY proceeding_number
        LIMIT {MAX_SERIAL_CANDIDATES + 1}
        """,
        settings=budget,
        client=client,
    )
    if len(candidates) > MAX_SERIAL_CANDIDATES:
        raise TTABQueryScopeExceeded(
            f"serial resolves to more than {MAX_SERIAL_CANDIDATES} TTAB proceedings"
        )
    if not candidates:
        return []

    candidates_by_number = {str(row["proceeding_number"]): row for row in candidates}
    proceeding_numbers = ", ".join(_sql_literal(value) for value in candidates_by_number)
    proceedings = _rows(
        f"""
        SELECT proceeding_number, proceeding_type, proceeding_type_code,
               filing_date, status_text, status_code, status_date,
               source_snapshot_at, source_rank,
               toString(source_package_id) AS source_package_id
        FROM markorbit_facts.us_ttab_proceeding_history
        WHERE proceeding_number IN ({proceeding_numbers})
        ORDER BY source_rank DESC, source_package_id DESC
        LIMIT 1 BY proceeding_number
        """,
        settings=budget,
        client=client,
    )
    result: list[dict[str, Any]] = []
    for proceeding in proceedings:
        candidate = candidates_by_number[str(proceeding["proceeding_number"])]
        current_package = str(proceeding["source_package_id"])
        current_properties = [
            version
            for version in candidate["property_versions"]
            if str(version[0]) == current_package
        ]
        if not current_properties:
            continue
        property_version = current_properties[0]
        result.append(
            {
                **proceeding,
                "party_side": property_version[1],
                "registration_number": property_version[2],
                "application_status": property_version[3],
                "application_status_code": property_version[4],
                "mark_explanation": property_version[5],
            }
        )
    result.sort(
        key=lambda row: (
            row.get("filing_date") is not None,
            str(row.get("filing_date") or ""),
            int(row["source_rank"]),
            str(row["proceeding_number"]),
        ),
        reverse=True,
    )
    return result[:limit]
