from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.db import clickhouse_client


RECONCILIATION_VERSION = "US_ASSIGNMENT_CASE_OWNER_RECONCILIATION_V1"
SEMANTICS = "SOURCE_NAME_EVIDENCE_CONSISTENCY_NOT_LEGAL_OWNERSHIP"


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _rows(sql: str) -> list[dict[str, Any]]:
    result = clickhouse_client().query(sql)
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _serial_page(after_serial: str, limit: int) -> list[str]:
    safe_after = after_serial or "00000000"
    rows = _rows(
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
        WHERE p.serial_number > '{safe_after}'
          AND length(p.serial_number) = 8
          AND match(p.serial_number, '^[0-9]{{8}}$')
        ORDER BY p.serial_number
        LIMIT {int(limit)}
        """
    )
    return [str(row["serial_number"]) for row in rows]


def _latest_assignment_rows(serials: list[str]) -> dict[str, dict[str, Any]]:
    if not serials:
        return {}
    literals = ",".join(f"'{serial}'" for serial in serials)
    rows = _rows(
        f"""
        WITH latest_record AS
        (
            SELECT reel_frame_id,
                   argMax(toString(source_package_id), tuple(source_rank, toString(source_package_id))) AS package_id
            FROM markorbit_facts.us_assignment_record_history
            GROUP BY reel_frame_id
        )
        SELECT p.serial_number, r.reel_frame_id, toString(r.source_package_id) AS package_id,
               r.recorded_date, r.source_effective_date, r.source_rank, r.conveyance_text
        FROM markorbit_facts.us_assignment_property_history AS p
        INNER JOIN latest_record AS lr
          ON p.reel_frame_id = lr.reel_frame_id
         AND toString(p.source_package_id) = lr.package_id
        INNER JOIN markorbit_facts.us_assignment_record_history AS r
          ON p.reel_frame_id = r.reel_frame_id
         AND p.source_package_id = r.source_package_id
        WHERE p.serial_number IN ({literals})
        ORDER BY p.serial_number, r.recorded_date DESC NULLS LAST,
                 r.source_rank DESC, r.reel_frame_id DESC
        """
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        serial = str(row["serial_number"])
        latest.setdefault(serial, row)
    return latest


def _assignee_names(assignments: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    if not assignments:
        return {}
    conditions = " OR ".join(
        "(reel_frame_id = '{rf}' AND source_package_id = toUUID('{package}'))".format(
            rf=str(row["reel_frame_id"]), package=str(row["package_id"])
        )
        for row in assignments.values()
    )
    rows = _rows(
        f"""
        SELECT reel_frame_id, toString(source_package_id) AS package_id,
               groupArray((ordinal, party_name)) AS party_pairs
        FROM markorbit_facts.us_assignment_assignee_history
        WHERE {conditions}
        GROUP BY reel_frame_id, source_package_id
        """
    )
    by_key: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        pairs = sorted(
            ((int(pair[0]), str(pair[1])) for pair in row["party_pairs"]),
            key=lambda pair: (pair[0], pair[1].casefold()),
        )
        by_key[(str(row["reel_frame_id"]), str(row["package_id"]))] = [
            name for _ordinal, name in pairs if name.strip()
        ]
    return {
        serial: by_key.get((str(item["reel_frame_id"]), str(item["package_id"])), [])
        for serial, item in assignments.items()
    }


def _current_owner_names(serials: list[str]) -> dict[str, list[str]]:
    if not serials:
        return {}
    literals = ",".join(f"'{serial}'" for serial in serials)
    rows = _rows(
        f"""
        SELECT serial_number, groupArray((entry_number, party_name)) AS party_pairs
        FROM markorbit_facts.us_owner_current FINAL
        WHERE is_deleted = 0 AND serial_number IN ({literals}) AND party_name != ''
        GROUP BY serial_number
        """
    )
    result: dict[str, list[str]] = {}
    for row in rows:
        pairs = sorted(
            ((int(pair[0]), str(pair[1])) for pair in row["party_pairs"]),
            key=lambda pair: (pair[0], pair[1].casefold()),
        )
        result[str(row["serial_number"])] = [name for _ordinal, name in pairs]
    return result


def _case_presence(serials: list[str]) -> set[str]:
    if not serials:
        return set()
    literals = ",".join(f"'{serial}'" for serial in serials)
    return {
        str(row["serial_number"])
        for row in _rows(
            f"""
            SELECT serial_number FROM markorbit_facts.us_case_current FINAL
            WHERE is_deleted = 0 AND serial_number IN ({literals})
            """
        )
    }


def _owner_observation(serials: list[str]) -> dict[str, dict[str, Any]]:
    if not serials:
        return {}
    literals = ",".join(f"'{serial}'" for serial in serials)
    rows = _rows(
        f"""
        SELECT serial_number,
               argMax(owner_names, tuple(source_rank, toString(source_package_id))) AS owner_names,
               argMax(source_effective_date, tuple(source_rank, toString(source_package_id))) AS source_effective_date,
               max(source_rank) AS source_rank
        FROM markorbit_facts.us_case_observation_history
        WHERE serial_number IN ({literals})
        GROUP BY serial_number
        """
    )
    return {str(row["serial_number"]): row for row in rows}


def classify_name_evidence(
    *,
    case_exists: bool,
    current_owner_names: list[str],
    recorded_assignee_names: list[str],
) -> str:
    if not case_exists:
        return "RECORDED_ASSIGNMENT_WITHOUT_CASE_RECORD"
    if not recorded_assignee_names:
        return "RECORDED_ASSIGNEE_NAMES_MISSING"
    if not current_owner_names:
        return "CASE_OWNER_NAMES_MISSING"
    owner_set = {_normalize_name(name) for name in current_owner_names if name.strip()}
    assignee_set = {_normalize_name(name) for name in recorded_assignee_names if name.strip()}
    if not owner_set or not assignee_set:
        return "NOT_COMPARABLE"
    return "NAME_SET_MATCH" if owner_set == assignee_set else "NAME_SET_DIFFER"


def scan_reconciliation_page(
    *,
    after_serial: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    if after_serial and (len(after_serial) != 8 or not after_serial.isdigit()):
        raise ValueError("after_serial must be empty or exactly 8 digits")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    serials = _serial_page(after_serial, limit)
    assignments = _latest_assignment_rows(serials)
    assignees = _assignee_names(assignments)
    owners = _current_owner_names(serials)
    cases = _case_presence(serials)
    observations = _owner_observation(serials)
    items: list[dict[str, Any]] = []
    summary: dict[str, int] = defaultdict(int)

    for serial in serials:
        assignment = assignments.get(serial) or {}
        recorded_names = assignees.get(serial, [])
        current_names = owners.get(serial, [])
        classification = classify_name_evidence(
            case_exists=serial in cases,
            current_owner_names=current_names,
            recorded_assignee_names=recorded_names,
        )
        summary[classification] += 1
        observation = observations.get(serial)
        items.append(
            {
                "serial_number": serial,
                "classification": classification,
                "current_case_owner_names": current_names,
                "latest_recorded_assignee_names": recorded_names,
                "latest_recorded_assignment": {
                    "reel_frame_id": assignment.get("reel_frame_id"),
                    "recorded_date": assignment.get("recorded_date"),
                    "source_effective_date": assignment.get("source_effective_date"),
                    "source_rank": assignment.get("source_rank"),
                    "conveyance_text": assignment.get("conveyance_text"),
                },
                "latest_case_owner_observation": (
                    {
                        "owner_names": list(observation.get("owner_names") or []),
                        "source_effective_date": observation.get("source_effective_date"),
                        "source_rank": observation.get("source_rank"),
                    }
                    if observation
                    else None
                ),
                "comparison_method": "WHITESPACE_AND_CASE_NORMALIZED_EXACT_NAME_SET_ONLY",
                "legal_ownership_conclusion": False,
            }
        )

    return {
        "version": RECONCILIATION_VERSION,
        "after_serial": after_serial,
        "scanned_serial_count": len(serials),
        "last_scanned_serial": serials[-1] if serials else after_serial,
        "has_more": len(serials) == limit,
        "summary": dict(sorted(summary.items())),
        "items": items,
        "semantics": SEMANTICS,
        "legal_ownership_conclusion": False,
    }
