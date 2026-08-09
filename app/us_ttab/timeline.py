from __future__ import annotations

from typing import Any

from app.us_ttab import TTAB_SEMANTICS
from app.us_ttab.read_model import _rows, snapshot_children, validate_proceeding_number


def _party_set(children: dict[str, list[dict[str, Any]]]) -> set[tuple[str, str, str]]:
    return {
        (
            str(item.get("side") or ""),
            str(item.get("party_id") or ""),
            " ".join(str(item.get("party_name") or "").split()),
        )
        for item in children["parties"]
    }


def _property_set(
    children: dict[str, list[dict[str, Any]]],
) -> set[tuple[str, str, str, str, str, str]]:
    return {
        (
            str(item.get("party_side") or ""),
            str(item.get("serial_number") or ""),
            str(item.get("registration_number") or ""),
            " ".join(
                str(item.get("mark_text") or item.get("mark_explanation") or "").split()
            ),
            str(item.get("application_status_code") or ""),
            str(item.get("trademark_gid") or ""),
        )
        for item in children["properties"]
    }


def _docket_map(children: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {str(item["docket_key"]): item for item in children["docket"]}


def _change(
    change_type: str,
    before: object,
    after: object,
    **extra: object,
) -> dict[str, Any]:
    return {"change_type": change_type, "before": before, "after": after, **extra}


def build_ttab_timeline(proceeding_number: str) -> dict[str, Any]:
    number = validate_proceeding_number(proceeding_number)
    observations = _rows(
        f"""
        SELECT proceeding_number, proceeding_type, proceeding_type_code,
               filing_date, filing_date_raw, status_text, status_code, status_date,
               status_date_raw, general_contact_number, interlocutory_attorney,
               paralegal_name, record_hash, source_kind, source_snapshot_at, source_file,
               toString(source_package_id) AS source_package_id, source_rank
        FROM markorbit_facts.us_ttab_proceeding_history
        WHERE proceeding_number = '{number}'
        ORDER BY source_rank, source_package_id
        """
    )
    snapshots: list[dict[str, Any]] = []
    for record in observations:
        snapshots.append({"record": record, "children": snapshot_children(record)})

    changes: list[dict[str, Any]] = []
    for previous, current in zip(snapshots, snapshots[1:]):
        before = previous["record"]
        after = current["record"]
        snapshot_changes: list[dict[str, Any]] = []
        if before.get("proceeding_type_code") != after.get("proceeding_type_code"):
            snapshot_changes.append(
                _change(
                    "PROCEEDING_TYPE_CODE_CHANGED",
                    before.get("proceeding_type_code"),
                    after.get("proceeding_type_code"),
                )
            )
        if before.get("status_code") != after.get("status_code"):
            snapshot_changes.append(
                _change(
                    "STATUS_CODE_CHANGED",
                    before.get("status_code"),
                    after.get("status_code"),
                )
            )
        if before.get("status_text") != after.get("status_text"):
            snapshot_changes.append(
                _change(
                    "STATUS_TEXT_CHANGED",
                    before.get("status_text"),
                    after.get("status_text"),
                )
            )
        if before.get("status_date_raw") != after.get("status_date_raw"):
            snapshot_changes.append(
                _change(
                    "STATUS_DATE_CHANGED",
                    before.get("status_date_raw"),
                    after.get("status_date_raw"),
                )
            )
        before_staff = (
            before.get("interlocutory_attorney"),
            before.get("paralegal_name"),
        )
        after_staff = (
            after.get("interlocutory_attorney"),
            after.get("paralegal_name"),
        )
        if before_staff != after_staff:
            snapshot_changes.append(_change("BOARD_STAFF_CHANGED", before_staff, after_staff))

        previous_children = previous["children"]
        current_children = current["children"]
        before_parties = _party_set(previous_children)
        after_parties = _party_set(current_children)
        if before_parties != after_parties:
            snapshot_changes.append(
                _change("PARTY_SET_CHANGED", sorted(before_parties), sorted(after_parties))
            )
        before_properties = _property_set(previous_children)
        after_properties = _property_set(current_children)
        if before_properties != after_properties:
            snapshot_changes.append(
                _change("PROPERTY_SET_CHANGED", sorted(before_properties), sorted(after_properties))
            )

        before_docket = _docket_map(previous_children)
        after_docket = _docket_map(current_children)
        added = sorted(set(after_docket) - set(before_docket))
        removed = sorted(set(before_docket) - set(after_docket))
        for key in added:
            item = after_docket[key]
            snapshot_changes.append(
                _change(
                    "DOCKET_ENTRY_ADDED",
                    None,
                    {
                        "entry_number": item.get("entry_number"),
                        "entry_code": item.get("entry_code"),
                        "filing_date_raw": item.get("filing_date_raw"),
                        "history_text": item.get("history_text"),
                        "due_date_raw": item.get("due_date_raw"),
                    },
                    docket_key=key,
                )
            )
        for key in removed:
            item = before_docket[key]
            snapshot_changes.append(
                _change(
                    "DOCKET_ENTRY_REMOVED_FROM_SNAPSHOT",
                    {
                        "entry_number": item.get("entry_number"),
                        "entry_code": item.get("entry_code"),
                        "filing_date_raw": item.get("filing_date_raw"),
                        "history_text": item.get("history_text"),
                        "due_date_raw": item.get("due_date_raw"),
                    },
                    None,
                    docket_key=key,
                )
            )
        for key in sorted(set(before_docket) & set(after_docket)):
            old = before_docket[key]
            new = after_docket[key]
            if old.get("due_date_raw") != new.get("due_date_raw"):
                snapshot_changes.append(
                    _change(
                        "DOCKET_DUE_DATE_OBSERVATION_CHANGED",
                        old.get("due_date_raw"),
                        new.get("due_date_raw"),
                        docket_key=key,
                        entry_number=new.get("entry_number"),
                    )
                )
            if (
                old.get("record_hash") != new.get("record_hash")
                and old.get("due_date_raw") == new.get("due_date_raw")
            ):
                snapshot_changes.append(
                    _change(
                        "DOCKET_ENTRY_CONTENT_CHANGED",
                        old.get("record_hash"),
                        new.get("record_hash"),
                        docket_key=key,
                        entry_number=new.get("entry_number"),
                    )
                )

        if snapshot_changes:
            changes.append(
                {
                    "from_source_rank": before["source_rank"],
                    "to_source_rank": after["source_rank"],
                    "from_snapshot_at": before["source_snapshot_at"],
                    "to_snapshot_at": after["source_snapshot_at"],
                    "changes": snapshot_changes,
                }
            )

    return {
        "proceeding_number": number,
        "observation_count": len(observations),
        "observations": observations,
        "changes": changes,
        "semantics": TTAB_SEMANTICS,
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }
