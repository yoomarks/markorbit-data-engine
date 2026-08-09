from __future__ import annotations

from typing import Any

from app.db import clickhouse_client


OWNER_CHANGE_GAP_VERSION = "US_ASSIGNMENT_OWNER_CHANGE_GAP_V1"
SEMANTICS = "CASE_OWNER_OBSERVATION_CHANGE_VS_RECORDED_ASSIGNMENT_EVIDENCE_GAP"


def _rows(sql: str) -> list[dict[str, Any]]:
    result = clickhouse_client().query(sql)
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def scan_owner_change_gap_page(
    *,
    after_serial: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    if after_serial and (len(after_serial) != 8 or not after_serial.isdigit()):
        raise ValueError("after_serial must be empty or exactly 8 digits")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    safe_after = after_serial or "00000000"

    changes = _rows(
        f"""
        SELECT serial_number,
               uniqExact(owner_set_hash) AS owner_identity_versions,
               min(source_effective_date) AS first_observed_source_date,
               max(source_effective_date) AS latest_observed_source_date,
               argMin(owner_names, tuple(source_rank, toString(source_package_id))) AS first_owner_names,
               argMax(owner_names, tuple(source_rank, toString(source_package_id))) AS latest_owner_names,
               min(source_rank) AS first_source_rank,
               max(source_rank) AS latest_source_rank
        FROM markorbit_facts.us_case_observation_history
        WHERE serial_number > '{safe_after}'
        GROUP BY serial_number
        HAVING uniqExact(owner_set_hash) > 1
        ORDER BY serial_number
        LIMIT {int(limit)}
        """
    )
    serials = [str(row["serial_number"]) for row in changes]
    recorded: set[str] = set()
    if serials:
        literals = ",".join(f"'{serial}'" for serial in serials)
        recorded = {
            str(row["serial_number"])
            for row in _rows(
                f"""
                WITH latest_record AS
                (
                    SELECT reel_frame_id,
                           argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
                    FROM markorbit_facts.us_assignment_record_history
                    GROUP BY reel_frame_id
                )
                SELECT DISTINCT p.serial_number
                FROM markorbit_facts.us_assignment_property_history AS p
                INNER JOIN latest_record AS lr
                  ON p.reel_frame_id = lr.reel_frame_id
                 AND toString(p.source_package_id) = lr.package_id
                WHERE p.serial_number IN ({literals})
                """
            )
        }

    items: list[dict[str, Any]] = []
    missing_count = 0
    for row in changes:
        serial = str(row["serial_number"])
        has_recorded = serial in recorded
        classification = (
            "CASE_OWNER_CHANGE_WITH_RECORDED_ASSIGNMENT_EVIDENCE"
            if has_recorded
            else "CASE_OWNER_CHANGE_WITHOUT_RECORDED_ASSIGNMENT_EVIDENCE"
        )
        if not has_recorded:
            missing_count += 1
        items.append(
            {
                "serial_number": serial,
                "classification": classification,
                "owner_identity_versions": int(row["owner_identity_versions"]),
                "first_owner_names": list(row["first_owner_names"] or []),
                "latest_owner_names": list(row["latest_owner_names"] or []),
                "first_observed_source_date": row["first_observed_source_date"],
                "latest_observed_source_date": row["latest_observed_source_date"],
                "first_source_rank": int(row["first_source_rank"]),
                "latest_source_rank": int(row["latest_source_rank"]),
                "recorded_assignment_evidence_present": has_recorded,
                "legal_ownership_conclusion": False,
                "warning": (
                    "Absence of a matching recorded Assignment row is only an evidence gap; "
                    "it does not prove that no valid transfer/name change occurred."
                ),
            }
        )

    return {
        "version": OWNER_CHANGE_GAP_VERSION,
        "after_serial": after_serial,
        "scanned_owner_changed_case_count": len(changes),
        "recorded_evidence_present_count": len(changes) - missing_count,
        "recorded_evidence_gap_count": missing_count,
        "last_scanned_serial": serials[-1] if serials else after_serial,
        "has_more": len(changes) == limit,
        "items": items,
        "semantics": SEMANTICS,
        "legal_ownership_conclusion": False,
    }
