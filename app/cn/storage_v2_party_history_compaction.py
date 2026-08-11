from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import clickhouse_client


DATABASE = "markorbit_facts"
SOURCE_TABLE = "cn_case_party_relation_history"
SHADOW_TABLE = "cn_case_party_relation_history_storage_v2_shadow"
EVENT_TABLE = "cn_observed_event"

ROLES = ("OWNER", "CO_OWNER", "AGENT")
ACTIONS = ("OBSERVED_CURRENT", "SUPERSEDED")


def _table_exists(client: Any, table: str) -> bool:
    rows = client.query(
        "SELECT count() FROM system.tables "
        f"WHERE database = '{DATABASE}' AND name = '{table}'"
    ).result_rows
    return bool(rows and int(rows[0][0] or 0))


def _scalar(client: Any, sql: str) -> int:
    rows = client.query(sql).result_rows
    return int(rows[0][0] or 0) if rows else 0


def _active_bytes(client: Any, table: str) -> int:
    return _scalar(
        client,
        "SELECT sum(bytes_on_disk) FROM system.parts "
        f"WHERE database = '{DATABASE}' AND table = '{table}' AND active",
    )


def _history_profile(client: Any, table: str) -> list[dict[str, Any]]:
    rows = client.query(
        f"""
        SELECT role, action, count() AS rows
        FROM {DATABASE}.{table}
        GROUP BY role, action
        ORDER BY role, action
        """
    ).result_rows
    return [
        {"role": str(role), "action": str(action), "rows": int(row_count or 0)}
        for role, action, row_count in rows
    ]


def _party_event_profile(client: Any) -> list[dict[str, Any]]:
    known = []
    for role in ROLES:
        known.append(f"'{role}_RELATION_OBSERVED'")
        known.append(f"'{role}_RELATION_SUPERSEDED_OBSERVED'")
    rows = client.query(
        f"""
        SELECT event_type, count() AS rows
        FROM {DATABASE}.{EVENT_TABLE}
        WHERE event_type IN ({', '.join(known)})
        GROUP BY event_type
        ORDER BY event_type
        """
    ).result_rows
    return [
        {"event_type": str(event_type), "rows": int(row_count or 0)}
        for event_type, row_count in rows
    ]


def _history_counts(profile: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    return {
        (str(row["role"]), str(row["action"])): int(row["rows"])
        for row in profile
    }


def _event_counts(profile: list[dict[str, Any]]) -> dict[str, int]:
    return {str(row["event_type"]): int(row["rows"]) for row in profile}


def _coverage_report(
    history_profile: list[dict[str, Any]],
    event_profile: list[dict[str, Any]],
) -> dict[str, Any]:
    history = _history_counts(history_profile)
    events = _event_counts(event_profile)
    unknown_history = [
        row
        for row in history_profile
        if str(row["role"]) not in ROLES or str(row["action"]) not in ACTIONS
    ]

    role_rows: list[dict[str, Any]] = []
    coverage_ok = not unknown_history
    canonical_event_rows = 0
    extra_noop_rows = 0

    for role in ROLES:
        history_observed = history.get((role, "OBSERVED_CURRENT"), 0)
        history_superseded = history.get((role, "SUPERSEDED"), 0)
        event_observed = events.get(f"{role}_RELATION_OBSERVED", 0)
        event_superseded = events.get(f"{role}_RELATION_SUPERSEDED_OBSERVED", 0)

        observed_covered = history_observed >= event_observed
        superseded_covered = history_superseded == event_superseded
        coverage_ok = coverage_ok and observed_covered and superseded_covered
        canonical_event_rows += event_observed + event_superseded
        extra_noop_rows += max(0, history_observed - event_observed)

        role_rows.append(
            {
                "role": role,
                "history_observed_current_rows": history_observed,
                "event_relation_observed_rows": event_observed,
                "legacy_extra_noop_observed_rows": max(
                    0, history_observed - event_observed
                ),
                "history_superseded_rows": history_superseded,
                "event_relation_superseded_rows": event_superseded,
                "observed_event_coverage_ok": observed_covered,
                "superseded_event_coverage_ok": superseded_covered,
            }
        )

    return {
        "coverage_ok": coverage_ok,
        "unknown_history_groups": unknown_history,
        "roles": role_rows,
        "canonical_party_event_rows": canonical_event_rows,
        "legacy_extra_noop_history_rows": extra_noop_rows,
    }


def _plan_for_table(client: Any, table: str) -> dict[str, Any]:
    history_profile = _history_profile(client, table)
    event_profile = _party_event_profile(client)
    source_rows = _scalar(client, f"SELECT count() FROM {DATABASE}.{table}")
    coverage = _coverage_report(history_profile, event_profile)
    return {
        "source_rows": source_rows,
        "source_active_bytes": _active_bytes(client, table),
        "history_profile": history_profile,
        "party_event_profile": event_profile,
        "coverage": coverage,
    }


def build_plan(*, client: Any | None = None) -> dict[str, Any]:
    client = client or clickhouse_client()
    shadow_exists = _table_exists(client, SHADOW_TABLE)
    plan = _plan_for_table(client, SOURCE_TABLE)
    source_rows = int(plan["source_rows"])
    coverage = plan["coverage"]
    return {
        "plan_version": "CN_STORAGE_V2_PARTY_HISTORY_COMPACTION_V1",
        "policy": "CANONICAL_PARTY_HISTORY_IN_CN_OBSERVED_EVENT",
        "read_only": True,
        **plan,
        "shadow_exists": shadow_exists,
        "safe_to_commit": bool(
            source_rows > 0 and coverage["coverage_ok"] and not shadow_exists
        ),
        "estimated_reclaim_bytes": int(plan["source_active_bytes"]),
        "evidence_note": (
            "The legacy CN publisher writes PARTY relation events before the parallel "
            "relation-history rows. Failed/retried packages clean both tables by "
            "source_package_id. OBSERVED_CURRENT history historically contained extra "
            "no-op repeats, while SUPERSEDED history uses the same predicate as its "
            "canonical relation event. Storage V2 therefore keeps cn_observed_event as "
            "the durable PARTY history and removes the duplicate wide history copy."
        ),
    }


def build_status(*, client: Any | None = None) -> dict[str, Any]:
    client = client or clickhouse_client()
    source_rows = _scalar(client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}")
    shadow_exists = _table_exists(client, SHADOW_TABLE)
    result: dict[str, Any] = {
        "status_version": "CN_STORAGE_V2_PARTY_HISTORY_STATUS_V1",
        "read_only": True,
        "source_rows": source_rows,
        "source_active_bytes": _active_bytes(client, SOURCE_TABLE),
        "shadow_exists": shadow_exists,
        "party_event_profile": _party_event_profile(client),
    }
    if shadow_exists:
        shadow_rows = _scalar(client, f"SELECT count() FROM {DATABASE}.{SHADOW_TABLE}")
        result["shadow_rows"] = shadow_rows
        result["shadow_active_bytes"] = _active_bytes(client, SHADOW_TABLE)
        if source_rows > 0 and shadow_rows == 0:
            result["state"] = "PRE_EXCHANGE_EMPTY_SHADOW"
        elif source_rows == 0 and shadow_rows > 0:
            result["state"] = "POST_EXCHANGE_PENDING_DROP"
        else:
            result["state"] = "AMBIGUOUS_TEMP_STATE"
    elif source_rows == 0:
        result["state"] = "CANONICAL_EVENT_ONLY"
    else:
        result["state"] = "DUPLICATE_HISTORY_PRESENT"
    return result


def _validate_legacy_history(client: Any, table: str) -> dict[str, Any]:
    plan = _plan_for_table(client, table)
    if int(plan["source_rows"]) <= 0:
        raise RuntimeError("CN party-history validation expected a non-empty legacy table.")
    if not plan["coverage"]["coverage_ok"]:
        raise RuntimeError(
            "CN party-history event coverage failed; refusing to remove the duplicate "
            "history table: " + json.dumps(plan, default=str, sort_keys=True)
        )
    return plan


def _drop_validated_shadow(client: Any) -> tuple[int, int]:
    legacy = _validate_legacy_history(client, SHADOW_TABLE)
    old_rows = int(legacy["source_rows"])
    old_bytes = int(legacy["source_active_bytes"])
    client.command(
        f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC",
        settings={"max_table_size_to_drop": 0},
    )
    if _table_exists(client, SHADOW_TABLE):
        raise RuntimeError("CN party-history shadow DROP returned but the table still exists.")
    return old_rows, old_bytes


def _resume_existing_shadow(client: Any) -> dict[str, Any]:
    status = build_status(client=client)
    state = status["state"]
    event_profile_before = status["party_event_profile"]
    resumed_pre_exchange = False
    resumed_pending_drop = False

    if state == "PRE_EXCHANGE_EMPTY_SHADOW":
        _validate_legacy_history(client, SOURCE_TABLE)
        client.command(
            f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
        )
        resumed_pre_exchange = True
    elif state == "POST_EXCHANGE_PENDING_DROP":
        resumed_pending_drop = True
    else:
        raise RuntimeError(
            "CN party-history compaction found an ambiguous temporary-table state; "
            "refusing to guess. Status: "
            + json.dumps(status, default=str, sort_keys=True)
        )

    if _scalar(client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}") != 0:
        raise RuntimeError("CN party-history active table is not empty after EXCHANGE.")
    if _party_event_profile(client) != event_profile_before:
        raise RuntimeError("Canonical PARTY event profile changed during compaction.")

    old_rows, old_bytes = _drop_validated_shadow(client)
    return {
        "status": "COMMITTED_FINAL",
        "resumed_pre_exchange": resumed_pre_exchange,
        "resumed_pending_drop": resumed_pending_drop,
        "dropped_duplicate_history_rows": old_rows,
        "released_clickhouse_bytes_before_filesystem_reuse": old_bytes,
        "state_after": build_status(client=client)["state"],
    }


def commit_compaction(*, client: Any | None = None) -> dict[str, Any]:
    client = client or clickhouse_client()
    if _table_exists(client, SHADOW_TABLE):
        return _resume_existing_shadow(client)

    plan = build_plan(client=client)
    if not plan["safe_to_commit"]:
        raise RuntimeError(
            "CN party-history compaction preflight failed: "
            + json.dumps(plan, default=str, sort_keys=True)
        )

    source_rows_before = int(plan["source_rows"])
    event_profile_before = plan["party_event_profile"]
    client.command(
        f"CREATE TABLE {DATABASE}.{SHADOW_TABLE} AS {DATABASE}.{SOURCE_TABLE}"
    )
    exchanged = False
    try:
        if _scalar(client, f"SELECT count() FROM {DATABASE}.{SHADOW_TABLE}") != 0:
            raise RuntimeError("CN party-history empty shadow unexpectedly contains rows.")
        if _scalar(client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}") != source_rows_before:
            raise RuntimeError("CN party-history source row count changed before EXCHANGE.")
        _validate_legacy_history(client, SOURCE_TABLE)
        if _party_event_profile(client) != event_profile_before:
            raise RuntimeError("Canonical PARTY event profile changed before EXCHANGE.")

        client.command(
            f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
        )
        exchanged = True
    except Exception:
        if not exchanged and _table_exists(client, SHADOW_TABLE):
            client.command(
                f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC",
                settings={"max_table_size_to_drop": 0},
            )
        raise

    if _scalar(client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}") != 0:
        raise RuntimeError("CN party-history active table is not empty after EXCHANGE.")
    if _party_event_profile(client) != event_profile_before:
        raise RuntimeError("Canonical PARTY event profile changed after EXCHANGE.")

    old_rows, old_bytes = _drop_validated_shadow(client)
    return {
        "status": "COMMITTED_FINAL",
        "plan": plan,
        "resumed_pre_exchange": False,
        "resumed_pending_drop": False,
        "dropped_duplicate_history_rows": old_rows,
        "released_clickhouse_bytes_before_filesystem_reuse": old_bytes,
        "state_after": build_status(client=client)["state"],
        "note": (
            "Canonical PARTY relation history remains in cn_observed_event and current "
            "relation state remains in cn_case_party_current. The duplicate wide "
            "cn_case_party_relation_history table stays present but empty for schema "
            "compatibility."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guarded Storage V2 compaction for duplicate CN PARTY history."
    )
    parser.add_argument(
        "--mode", choices=("plan", "commit", "status"), default="plan"
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if args.mode == "plan":
        result = build_plan()
    elif args.mode == "status":
        result = build_status()
    else:
        result = commit_compaction()
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
