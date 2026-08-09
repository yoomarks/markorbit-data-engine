from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any
import uuid

from app.db import clickhouse_client
from app.us.model import USCaseBundle, USOwnerRecord
from app.us.publisher import stable_hash


CASE_OBSERVATION_TABLE = "markorbit_facts.us_case_observation_history"
CASE_OBSERVATION_COLUMNS = [
    "observation_key",
    "serial_number",
    "registration_number",
    "transaction_date",
    "status_code",
    "status_date",
    "current_location",
    "location_date",
    "filing_date",
    "publication_date",
    "registration_date",
    "abandonment_date",
    "cancellation_date",
    "renewal_date",
    "mark_identification",
    "use_1a_current",
    "intent_to_use_1b_current",
    "foreign_registration_44e_current",
    "madrid_66a_current",
    "renewal_filed",
    "section_8_filed",
    "section_8_accepted",
    "section_15_filed",
    "section_15_acknowledged",
    "opposition_pending",
    "cancellation_pending",
    "owner_set_hash",
    "owner_record_set_hash",
    "owner_count",
    "owner_names",
    "case_record_hash",
    "observation_hash",
    "source_package_kind",
    "source_effective_date",
    "source_file",
    "source_package_id",
    "source_rank",
]


def _name_norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _owner_identity(owner: USOwnerRecord) -> dict[str, object]:
    return {
        "party_type": owner.party_type,
        "legal_entity_type_code": owner.legal_entity_type_code,
        "party_name_norm": _name_norm(owner.party_name),
        "nationality_country": owner.nationality_country,
        "nationality_state": owner.nationality_state,
    }


def owner_snapshot_fingerprints(
    owners: tuple[USOwnerRecord, ...],
) -> dict[str, object]:
    identities = sorted(
        (_owner_identity(owner) for owner in owners),
        key=lambda item: stable_hash(item),
    )
    records = sorted(
        (asdict(owner) for owner in owners),
        key=lambda item: (
            int(item.get("entry_number") or 0),
            str(item.get("party_type") or ""),
            _name_norm(str(item.get("party_name") or "")),
        ),
    )
    names = sorted(
        {
            " ".join(owner.party_name.split())
            for owner in owners
            if owner.party_name.strip()
        },
        key=str.casefold,
    )
    return {
        "owner_set_hash": stable_hash(identities),
        "owner_record_set_hash": stable_hash(records),
        "owner_count": len(owners),
        "owner_names": names,
    }


def build_case_observation_row(
    bundle: USCaseBundle,
    *,
    package_id: uuid.UUID,
    package_kind: str,
    source_effective_date: date | None,
    source_file: str,
    source_rank: int,
) -> list[Any]:
    case = bundle.case
    owners = owner_snapshot_fingerprints(bundle.owners)
    case_record_hash = stable_hash(asdict(case))
    observation_payload = {
        "case": asdict(case),
        "owner_set_hash": owners["owner_set_hash"],
        "owner_record_set_hash": owners["owner_record_set_hash"],
        "owner_count": owners["owner_count"],
        "owner_names": owners["owner_names"],
    }
    observation_hash = stable_hash(observation_payload)
    observation_key = stable_hash(
        {
            "serial_number": case.serial_number,
            "source_package_id": str(package_id),
            "source_rank": source_rank,
        }
    )
    return [
        observation_key,
        case.serial_number,
        case.registration_number,
        case.transaction_date,
        case.status_code,
        case.status_date,
        case.current_location,
        case.location_date,
        case.filing_date,
        case.publication_date,
        case.registration_date,
        case.abandonment_date,
        case.cancellation_date,
        case.renewal_date,
        case.mark_identification,
        int(case.use_1a_current or case.use_1a),
        int(case.intent_to_use_1b_current or case.intent_to_use_1b),
        int(case.foreign_registration_44e_current or case.foreign_registration_44e),
        int(case.madrid_66a_current or case.madrid_66a),
        int(case.renewal_filed),
        int(case.section_8_filed),
        int(case.section_8_accepted),
        int(case.section_15_filed),
        int(case.section_15_acknowledged),
        int(case.opposition_pending),
        int(case.cancellation_pending),
        owners["owner_set_hash"],
        owners["owner_record_set_hash"],
        owners["owner_count"],
        owners["owner_names"],
        case_record_hash,
        observation_hash,
        package_kind,
        source_effective_date,
        source_file,
        package_id,
        source_rank,
    ]


_TRACKED_FIELDS: tuple[tuple[str, str], ...] = (
    ("status_code", "STATUS_CODE_CHANGED"),
    ("status_date", "STATUS_DATE_CHANGED"),
    ("current_location", "CURRENT_LOCATION_CHANGED"),
    ("registration_number", "REGISTRATION_NUMBER_CHANGED"),
    ("registration_date", "REGISTRATION_DATE_CHANGED"),
    ("abandonment_date", "ABANDONMENT_DATE_CHANGED"),
    ("cancellation_date", "CANCELLATION_DATE_CHANGED"),
    ("renewal_date", "RENEWAL_DATE_CHANGED"),
    ("mark_identification", "MARK_IDENTIFICATION_CHANGED"),
    ("use_1a_current", "BASIS_FLAG_CHANGED"),
    ("intent_to_use_1b_current", "BASIS_FLAG_CHANGED"),
    ("foreign_registration_44e_current", "BASIS_FLAG_CHANGED"),
    ("madrid_66a_current", "BASIS_FLAG_CHANGED"),
    ("renewal_filed", "MAINTENANCE_FLAG_CHANGED"),
    ("section_8_filed", "MAINTENANCE_FLAG_CHANGED"),
    ("section_8_accepted", "MAINTENANCE_FLAG_CHANGED"),
    ("section_15_filed", "MAINTENANCE_FLAG_CHANGED"),
    ("section_15_acknowledged", "MAINTENANCE_FLAG_CHANGED"),
    ("opposition_pending", "PROCEEDING_FLAG_CHANGED"),
    ("cancellation_pending", "PROCEEDING_FLAG_CHANGED"),
)


def derive_change(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any] | None:
    change_types: list[str] = []
    field_changes: dict[str, dict[str, Any]] = {}

    for field, change_type in _TRACKED_FIELDS:
        before = previous.get(field)
        after = current.get(field)
        if before == after:
            continue
        if change_type not in change_types:
            change_types.append(change_type)
        field_changes[field] = {"before": before, "after": after}

    if previous.get("owner_set_hash") != current.get("owner_set_hash"):
        change_types.append("OWNER_IDENTITY_SET_CHANGED")
        field_changes["owners"] = {
            "before": list(previous.get("owner_names") or []),
            "after": list(current.get("owner_names") or []),
        }
    elif previous.get("owner_record_set_hash") != current.get("owner_record_set_hash"):
        change_types.append("OWNER_DETAILS_CHANGED")
        field_changes["owner_record_set_hash"] = {
            "before": previous.get("owner_record_set_hash"),
            "after": current.get("owner_record_set_hash"),
        }

    if not change_types:
        return None

    return {
        "change_id": stable_hash(
            {
                "serial_number": current.get("serial_number"),
                "previous_observation_key": previous.get("observation_key"),
                "observation_key": current.get("observation_key"),
                "change_types": change_types,
            }
        ),
        "serial_number": str(current.get("serial_number") or ""),
        "source_rank": int(current.get("source_rank") or 0),
        "source_effective_date": current.get("source_effective_date"),
        "source_package_id": current.get("source_package_id"),
        "source_file": str(current.get("source_file") or ""),
        "previous_observation_key": previous.get("observation_key"),
        "observation_key": current.get("observation_key"),
        "change_types": change_types,
        "field_changes": field_changes,
        "legal_status_inference": False,
        "semantics": "OBSERVED_SOURCE_CHANGE_NOT_LEGAL_OWNERSHIP_OR_STATUS_CONCLUSION",
    }


def derive_changes(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        observations,
        key=lambda item: (
            int(item.get("source_rank") or 0),
            str(item.get("source_package_id") or ""),
        ),
    )
    changes: list[dict[str, Any]] = []
    for previous, current in zip(ordered, ordered[1:]):
        change = derive_change(previous, current)
        if change is not None:
            changes.append(change)
    return changes


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(item) for item in value)
    return value


def _rows_to_dicts(result: Any) -> list[dict[str, Any]]:
    return [
        {
            name: _normalize_value(value)
            for name, value in zip(result.column_names, row, strict=True)
        }
        for row in result.result_rows
    ]


def load_case_observations(
    serial_number: str,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    if len(serial_number) != 8 or not serial_number.isdigit():
        raise ValueError("serial_number must contain exactly 8 digits")
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    columns = ", ".join(CASE_OBSERVATION_COLUMNS)
    result = clickhouse_client().query(
        f"""
        SELECT {columns}
        FROM {CASE_OBSERVATION_TABLE}
        WHERE serial_number = '{serial_number}'
        ORDER BY source_rank, source_package_id
        LIMIT {int(limit)}
        """
    )
    return _rows_to_dicts(result)


def build_case_timeline(
    serial_number: str,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    observations = load_case_observations(serial_number, limit=limit)
    return {
        "serial_number": serial_number,
        "observation_count": len(observations),
        "observations": observations,
        "changes": derive_changes(observations),
        "semantics": "DURABLE_SOURCE_OBSERVATIONS_WITH_DERIVED_CHANGE_DIFFS",
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
    }


def _change_feed_sql(
    *,
    after_source_rank: int,
    after_serial: str,
    scan_limit: int,
) -> str:
    columns = ", ".join(f"n.{column}" for column in CASE_OBSERVATION_COLUMNS)
    previous_columns = ", ".join(
        [
            "p.observation_key AS previous_observation_key",
            "p.source_rank AS previous_source_rank",
            "p.status_code AS previous_status_code",
            "p.status_date AS previous_status_date",
            "p.current_location AS previous_current_location",
            "p.registration_number AS previous_registration_number",
            "p.registration_date AS previous_registration_date",
            "p.abandonment_date AS previous_abandonment_date",
            "p.cancellation_date AS previous_cancellation_date",
            "p.renewal_date AS previous_renewal_date",
            "p.mark_identification AS previous_mark_identification",
            "p.use_1a_current AS previous_use_1a_current",
            "p.intent_to_use_1b_current AS previous_intent_to_use_1b_current",
            "p.foreign_registration_44e_current AS previous_foreign_registration_44e_current",
            "p.madrid_66a_current AS previous_madrid_66a_current",
            "p.renewal_filed AS previous_renewal_filed",
            "p.section_8_filed AS previous_section_8_filed",
            "p.section_8_accepted AS previous_section_8_accepted",
            "p.section_15_filed AS previous_section_15_filed",
            "p.section_15_acknowledged AS previous_section_15_acknowledged",
            "p.opposition_pending AS previous_opposition_pending",
            "p.cancellation_pending AS previous_cancellation_pending",
            "p.owner_set_hash AS previous_owner_set_hash",
            "p.owner_record_set_hash AS previous_owner_record_set_hash",
            "p.owner_names AS previous_owner_names",
        ]
    )
    return f"""
        SELECT {columns}, {previous_columns}
        FROM
        (
            SELECT *
            FROM {CASE_OBSERVATION_TABLE}
            WHERE source_rank > {int(after_source_rank)}
               OR (
                    source_rank = {int(after_source_rank)}
                    AND serial_number > '{after_serial}'
               )
            ORDER BY source_rank, serial_number
            LIMIT {int(scan_limit)}
        ) AS n
        ASOF LEFT JOIN
        (
            SELECT *
            FROM {CASE_OBSERVATION_TABLE}
            ORDER BY serial_number, source_rank
        ) AS p
        ON n.serial_number = p.serial_number
       AND n.source_rank > p.source_rank
        ORDER BY n.source_rank, n.serial_number
    """


def _previous_from_joined(row: dict[str, Any]) -> dict[str, Any] | None:
    previous_rank = int(row.get("previous_source_rank") or 0)
    if previous_rank <= 0:
        return None
    key = row.get("previous_observation_key")
    previous: dict[str, Any] = {
        "serial_number": row.get("serial_number"),
        "observation_key": key,
    }
    for field, _ in _TRACKED_FIELDS:
        previous[field] = row.get(f"previous_{field}")
    previous["owner_set_hash"] = row.get("previous_owner_set_hash")
    previous["owner_record_set_hash"] = row.get("previous_owner_record_set_hash")
    previous["owner_names"] = row.get("previous_owner_names") or []
    return previous


def scan_change_feed_page(
    *,
    after_source_rank: int = 0,
    after_serial: str = "",
    scan_limit: int = 200,
) -> dict[str, Any]:
    if after_source_rank < 0:
        raise ValueError("after_source_rank must be non-negative")
    if after_serial and (len(after_serial) != 8 or not after_serial.isdigit()):
        raise ValueError("after_serial must be empty or exactly 8 digits")
    if not 1 <= scan_limit <= 1000:
        raise ValueError("scan_limit must be between 1 and 1000")

    result = clickhouse_client().query(
        _change_feed_sql(
            after_source_rank=after_source_rank,
            after_serial=after_serial,
            scan_limit=scan_limit,
        )
    )
    scanned = _rows_to_dicts(result)
    changes: list[dict[str, Any]] = []
    for row in scanned:
        previous = _previous_from_joined(row)
        if previous is None:
            continue
        change = derive_change(previous, row)
        if change is not None:
            changes.append(change)

    if scanned:
        last = scanned[-1]
        next_cursor = {
            "source_rank": int(last["source_rank"]),
            "serial_number": str(last["serial_number"]),
        }
    else:
        next_cursor = {
            "source_rank": int(after_source_rank),
            "serial_number": after_serial,
        }

    return {
        "after_source_rank": int(after_source_rank),
        "after_serial": after_serial,
        "scanned_observation_count": len(scanned),
        "change_count": len(changes),
        "has_more_observations": len(scanned) == scan_limit,
        "next_cursor": next_cursor,
        "changes": changes,
        "semantics": "LOSSLESS_OBSERVATION_CURSOR_CHANGE_FEED_NOT_LEGAL_CONCLUSION",
        "legal_status_inference": False,
        "legal_ownership_conclusion": False,
    }
