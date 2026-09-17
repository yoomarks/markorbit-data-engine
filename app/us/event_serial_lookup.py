from __future__ import annotations

from typing import Any

from app.read_performance_baseline import DEFAULT_QUERY_BUDGET


EVENT_SERIAL_LOOKUP_SCHEMA_VERSION = "US_EVENT_SERIAL_LOOKUP_SCHEMA_V1"
EVENT_SERIAL_LOOKUP_READY_VERSION = "US_EVENT_SERIAL_LOOKUP_READY_V1"
US_EVENT_SERIAL_LOOKUP_TABLE = "markorbit_facts.us_event_serial_history"
MAX_SERIAL_EVENTS = 5_000
US_EVENT_SERIAL_LOOKUP_COLUMNS: tuple[str, ...] = (
    "event_key",
    "serial_number",
    "event_code",
    "event_date",
    "event_sequence",
    "event_type_code",
    "description_text",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_row_hash",
    "source_package_id",
    "source_rank",
    "observed_at",
)
US_EVENT_SERIAL_LOOKUP_WRITE_COLUMNS: tuple[str, ...] = tuple(
    column for column in US_EVENT_SERIAL_LOOKUP_COLUMNS if column != "observed_at"
)


class EventSerialLookupInvalid(ValueError):
    pass


class EventSerialLookupScopeExceeded(RuntimeError):
    pass


class EventSerialLookupUnavailable(RuntimeError):
    pass


def _serial_number(value: str) -> str:
    serial = value.strip()
    if len(serial) != 8 or not serial.isdigit():
        raise EventSerialLookupInvalid("USPTO serial number must contain exactly 8 digits")
    return serial


def _rows(client: Any, sql: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        result = client.query(sql, settings=settings)
    except Exception as exc:
        raise EventSerialLookupUnavailable(str(exc)) from exc
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def events_for_serial(client: Any, serial_number: str, *, limit: int = 500) -> dict[str, Any]:
    serial = _serial_number(serial_number)
    if not 1 <= limit <= MAX_SERIAL_EVENTS:
        raise EventSerialLookupInvalid(f"limit must be between 1 and {MAX_SERIAL_EVENTS}")
    budget = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_SERIAL_EVENTS + 1}
    readiness = _rows(
        client,
        """
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'US_EVENT_SERIAL_LOOKUP'
        LIMIT 1
        """,
        budget,
    )
    if not readiness or str(readiness[0]["version"]) != EVENT_SERIAL_LOOKUP_READY_VERSION:
        raise EventSerialLookupUnavailable("US serial event lookup is not backfilled and accepted")
    events = _rows(
        client,
        f"""
        SELECT event_key, serial_number, event_code, event_date, event_sequence,
               event_type_code, description_text, source_package_kind,
               source_effective_date, source_file, source_row_hash,
               toString(source_package_id) AS source_package_id, source_rank, observed_at
        FROM {US_EVENT_SERIAL_LOOKUP_TABLE} FINAL
        WHERE serial_number = '{serial}'
        ORDER BY event_date, event_sequence, event_code, event_key
        LIMIT {limit + 1}
        """,
        budget,
    )
    if len(events) > limit:
        raise EventSerialLookupScopeExceeded(f"serial resolves to more than {limit} events")
    return {
        "serial_number": serial,
        "event_count": len(events),
        "events": events,
        "semantics": "OFFICIAL_USPTO_EVENT_FACTS_NOT_LEGAL_STATUS_CONCLUSION",
    }
