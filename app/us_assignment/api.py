from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.db import clickhouse_client


router = APIRouter(prefix="/api/us", tags=["US assignment facts"])
SEMANTICS = "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION"


def _query(sql: str) -> list[dict[str, Any]]:
    try:
        result = clickhouse_client().query(sql)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_ASSIGNMENT_DATASTORE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _strict_serial(serial_number: str) -> str:
    serial = serial_number.strip()
    if len(serial) != 8 or not serial.isdigit():
        raise HTTPException(status_code=400, detail="USPTO serial number must contain exactly 8 digits")
    return serial


def _safe_component(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 32 or not all(ch.isalnum() or ch in "-_" for ch in cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return cleaned


def _latest_record(reel_no: str, frame_no: str) -> dict[str, Any] | None:
    reel = _safe_component(reel_no, "reel number")
    frame = _safe_component(frame_no, "frame number")
    rows = _query(
        f"""
        SELECT *
        FROM markorbit_facts.us_assignment_record_history
        WHERE reel_no = '{reel}' AND frame_no = '{frame}'
        ORDER BY source_rank DESC, source_package_id DESC
        LIMIT 1
        """
    )
    return rows[0] if rows else None


def _bundle_for_record(record: dict[str, Any]) -> dict[str, Any]:
    reel_frame = str(record["reel_frame_id"])
    package_id = str(record["source_package_id"])
    assignors = _query(
        f"""
        SELECT * FROM markorbit_facts.us_assignment_assignor_history
        WHERE reel_frame_id = '{reel_frame}' AND source_package_id = toUUID('{package_id}')
        ORDER BY ordinal, party_key
        """
    )
    assignees = _query(
        f"""
        SELECT * FROM markorbit_facts.us_assignment_assignee_history
        WHERE reel_frame_id = '{reel_frame}' AND source_package_id = toUUID('{package_id}')
        ORDER BY ordinal, party_key
        """
    )
    properties = _query(
        f"""
        SELECT * FROM markorbit_facts.us_assignment_property_history
        WHERE reel_frame_id = '{reel_frame}' AND source_package_id = toUUID('{package_id}')
        ORDER BY ordinal, property_key
        """
    )
    return {
        "record": record,
        "assignors": assignors,
        "assignees": assignees,
        "properties": properties,
        "semantics": SEMANTICS,
        "legal_ownership_conclusion": False,
    }


def _assignments_for_serial(serial: str, limit: int) -> list[dict[str, Any]]:
    # Only properties from the latest observation of each reel/frame are eligible.
    # This prevents a property removed by a later source correction from surviving
    # merely because the historical property row is append-only.
    rows = _query(
        f"""
        WITH latest_record AS
        (
            SELECT reel_frame_id,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
            FROM markorbit_facts.us_assignment_record_history
            GROUP BY reel_frame_id
        ),
        linked AS
        (
            SELECT DISTINCT p.reel_frame_id
            FROM markorbit_facts.us_assignment_property_history AS p
            INNER JOIN latest_record AS lr
              ON p.reel_frame_id = lr.reel_frame_id
             AND toString(p.source_package_id) = lr.package_id
            WHERE p.serial_number = '{serial}'
        )
        SELECT r.*
        FROM markorbit_facts.us_assignment_record_history AS r
        INNER JOIN latest_record AS lr
          ON r.reel_frame_id = lr.reel_frame_id
         AND toString(r.source_package_id) = lr.package_id
        INNER JOIN linked AS l ON r.reel_frame_id = l.reel_frame_id
        ORDER BY r.recorded_date DESC NULLS LAST, r.source_rank DESC, r.reel_frame_id DESC
        LIMIT {int(limit)}
        """
    )
    return rows


@router.get("/assignments/reel-frame/{reel_no}/{frame_no}")
def us_assignment_reel_frame(reel_no: str, frame_no: str):
    record = _latest_record(reel_no, frame_no)
    if record is None:
        raise HTTPException(status_code=404, detail="USPTO assignment reel/frame not found")
    return _bundle_for_record(record)


@router.get("/assignments/{serial_number}")
def us_assignments_for_serial(
    serial_number: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    serial = _strict_serial(serial_number)
    records = _assignments_for_serial(serial, limit)
    return {
        "serial_number": serial,
        "assignment_count": len(records),
        "assignments": records,
        "semantics": SEMANTICS,
        "legal_ownership_conclusion": False,
    }


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


@router.get("/assignments/{serial_number}/reconciliation")
def us_assignment_owner_reconciliation(serial_number: str):
    serial = _strict_serial(serial_number)
    current_owner_rows = _query(
        f"""
        SELECT party_name
        FROM markorbit_facts.us_owner_current FINAL
        WHERE serial_number = '{serial}' AND is_deleted = 0 AND party_name != ''
        ORDER BY entry_number, party_name
        """
    )
    assignment_records = _assignments_for_serial(serial, 1)
    current_names = [str(row["party_name"]) for row in current_owner_rows]
    latest_record = assignment_records[0] if assignment_records else None
    latest_assignee_names: list[str] = []
    if latest_record is not None:
        package_id = str(latest_record["source_package_id"])
        reel_frame = str(latest_record["reel_frame_id"])
        latest_assignee_names = [
            str(row["party_name"])
            for row in _query(
                f"""
                SELECT party_name
                FROM markorbit_facts.us_assignment_assignee_history
                WHERE reel_frame_id = '{reel_frame}'
                  AND source_package_id = toUUID('{package_id}')
                  AND party_name != ''
                ORDER BY ordinal, party_name
                """
            )
        ]

    if not current_names or not latest_assignee_names:
        comparison = "NOT_COMPARABLE"
    else:
        current_set = {_normalize_name(name) for name in current_names}
        assignee_set = {_normalize_name(name) for name in latest_assignee_names}
        comparison = "MATCH" if current_set == assignee_set else "DIFFER"

    return {
        "serial_number": serial,
        "comparison": comparison,
        "current_case_owner_names": current_names,
        "latest_recorded_assignment": (
            {
                "reel_frame_id": latest_record["reel_frame_id"],
                "recorded_date": latest_record["recorded_date"],
                "conveyance_text": latest_record["conveyance_text"],
                "assignee_names": latest_assignee_names,
            }
            if latest_record is not None
            else None
        ),
        "comparison_method": "WHITESPACE_AND_CASE_NORMALIZED_EXACT_NAME_SET_ONLY",
        "semantics": SEMANTICS,
        "legal_ownership_conclusion": False,
        "warning": "MATCH or DIFFER compares recorded names only; it does not determine legal title.",
    }
