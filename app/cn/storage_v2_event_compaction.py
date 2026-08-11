from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import clickhouse_client


DATABASE = "markorbit_facts"
SOURCE_TABLE = "cn_observed_event"
SHADOW_TABLE = "cn_observed_event_storage_v2_shadow"

BASELINE_ONLY_EVENT_TYPES = {
    "APPLICATION_OBSERVED",
    "GOODS_SCOPE_OBSERVED",
    "DERIVED_CASE_OBSERVED",
}
BASELINE_WHEN_OLD_EMPTY_EVENT_TYPES = {
    "PRELIMINARY_PUBLICATION_OBSERVED",
    "REGISTRATION_PUBLICATION_OBSERVED",
    "EXCLUSIVE_TERM_OBSERVED",
}
KNOWN_EVENT_TYPES = BASELINE_ONLY_EVENT_TYPES | BASELINE_WHEN_OLD_EMPTY_EVENT_TYPES | {
    "CASE_FACTS_CHANGED_OBSERVED",
    "GOODS_SCOPE_CHANGED_OBSERVED",
    "TERM_EXTENDED_OBSERVED",
    "MARK_NAME_CHANGED_OBSERVED",
    "AGENT_CODE_CHANGED_OBSERVED",
    "OWNER_RELATION_OBSERVED",
    "CO_OWNER_RELATION_OBSERVED",
    "AGENT_RELATION_OBSERVED",
    "OWNER_RELATION_SUPERSEDED_OBSERVED",
    "CO_OWNER_RELATION_SUPERSEDED_OBSERVED",
    "AGENT_RELATION_SUPERSEDED_OBSERVED",
}


def _sql_values(values: set[str]) -> str:
    return ", ".join("'" + value.replace("'", "\\'") + "'" for value in sorted(values))


def _baseline_predicate(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    baseline_only = _sql_values(BASELINE_ONLY_EVENT_TYPES)
    old_empty = _sql_values(BASELINE_WHEN_OLD_EMPTY_EVENT_TYPES)
    return (
        f"({prefix}event_type IN ({baseline_only}) OR "
        f"({prefix}event_type IN ({old_empty}) AND {prefix}old_value_compact = ''))"
    )


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


def _event_profile(client: Any, table: str) -> list[dict[str, Any]]:
    rows = client.query(
        f"""
        SELECT
            event_type,
            count() AS rows,
            countIf(old_value_compact = '') AS empty_old_value_rows,
            countIf(old_value_compact != '') AS prior_value_rows
        FROM {DATABASE}.{table}
        GROUP BY event_type
        ORDER BY rows DESC, event_type
        """
    ).result_rows
    return [
        {
            "event_type": str(event_type),
            "rows": int(row_count or 0),
            "empty_old_value_rows": int(empty_old or 0),
            "prior_value_rows": int(prior_value or 0),
        }
        for event_type, row_count, empty_old, prior_value in rows
    ]


def _unknown_event_counts(client: Any, table: str) -> dict[str, int]:
    known = _sql_values(KNOWN_EVENT_TYPES)
    rows = client.query(
        f"""
        SELECT event_type, count()
        FROM {DATABASE}.{table}
        WHERE event_type NOT IN ({known})
        GROUP BY event_type
        ORDER BY event_type
        """
    ).result_rows
    return {str(event_type): int(row_count or 0) for event_type, row_count in rows}


def _baseline_rows(client: Any, table: str) -> int:
    return _scalar(
        client,
        f"SELECT count() FROM {DATABASE}.{table} WHERE {_baseline_predicate()}",
    )


def _fingerprint(client: Any, table: str, *, keep_only: bool) -> dict[str, Any]:
    where = f"WHERE NOT {_baseline_predicate()}" if keep_only else ""
    rows = client.query(
        f"""
        SELECT
            count(),
            toString(sum(toUInt128(cityHash64(event_hash))))
        FROM {DATABASE}.{table}
        {where}
        """
    ).result_rows
    row_count, hash_sum = rows[0] if rows else (0, "0")
    return {"rows": int(row_count or 0), "hash_sum": str(hash_sum or "0")}


def build_plan(*, client: Any | None = None) -> dict[str, Any]:
    client = client or clickhouse_client()
    source_rows = _scalar(client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}")
    baseline_rows = _baseline_rows(client, SOURCE_TABLE)
    keep_rows = source_rows - baseline_rows
    source_bytes = _active_bytes(client, SOURCE_TABLE)
    unknown = _unknown_event_counts(client, SOURCE_TABLE)
    shadow_exists = _table_exists(client, SHADOW_TABLE)
    ratio = (baseline_rows / source_rows) if source_rows else 0.0
    return {
        "plan_version": "CN_STORAGE_V2_EVENT_COMPACTION_V1",
        "policy": "RAW_AUTHORITY_PLUS_CURRENT_FACTS_PLUS_TRUE_DELTA_AND_PARTY_EVENTS",
        "read_only": True,
        "source_rows": source_rows,
        "source_active_bytes": source_bytes,
        "event_profile": _event_profile(client, SOURCE_TABLE),
        "reconstructible_baseline_candidate_rows": baseline_rows,
        "keep_rows": keep_rows,
        "candidate_row_share": ratio,
        "proportional_reclaim_estimate_bytes": int(source_bytes * ratio),
        "unknown_event_counts": unknown,
        "unknown_event_rows": sum(unknown.values()),
        "shadow_exists": shadow_exists,
        "safe_to_commit": bool(
            source_rows > 0 and baseline_rows > 0 and not unknown and not shadow_exists
        ),
        "evidence_note": (
            "Only APPLICATION/GOODS_SCOPE/DERIVED_CASE baseline observations and "
            "publication/registration/term observations with an empty old value are "
            "removable. Party relation events and every event carrying prior-state "
            "evidence remain hot history. Removed baseline facts remain reproducible "
            "from retained raw authority plus current fact tables."
        ),
    }


def build_status(*, client: Any | None = None) -> dict[str, Any]:
    client = client or clickhouse_client()
    source_baseline = _baseline_rows(client, SOURCE_TABLE)
    source_unknown = _unknown_event_counts(client, SOURCE_TABLE)
    result: dict[str, Any] = {
        "status_version": "CN_STORAGE_V2_EVENT_STATUS_V1",
        "read_only": True,
        "source_rows": _scalar(client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}"),
        "source_active_bytes": _active_bytes(client, SOURCE_TABLE),
        "source_baseline_candidate_rows": source_baseline,
        "source_unknown_event_counts": source_unknown,
        "shadow_exists": _table_exists(client, SHADOW_TABLE),
    }
    if result["shadow_exists"]:
        shadow_baseline = _baseline_rows(client, SHADOW_TABLE)
        shadow_unknown = _unknown_event_counts(client, SHADOW_TABLE)
        result.update(
            {
                "shadow_rows": _scalar(
                    client, f"SELECT count() FROM {DATABASE}.{SHADOW_TABLE}"
                ),
                "shadow_active_bytes": _active_bytes(client, SHADOW_TABLE),
                "shadow_baseline_candidate_rows": shadow_baseline,
                "shadow_unknown_event_counts": shadow_unknown,
            }
        )
        if source_baseline > 0 and shadow_baseline == 0:
            result["state"] = "PRE_EXCHANGE_SHADOW"
        elif source_baseline == 0 and shadow_baseline > 0:
            result["state"] = "POST_EXCHANGE_PENDING_DROP"
        else:
            result["state"] = "AMBIGUOUS_TEMP_STATE"
    elif source_baseline == 0:
        result["state"] = "COMPACT_OR_EMPTY"
    else:
        result["state"] = "LEGACY_BASELINE_PRESENT"
    return result


def _validate_shadow_pair(client: Any, *, pre_exchange: bool) -> dict[str, Any]:
    source_unknown = _unknown_event_counts(client, SOURCE_TABLE)
    shadow_unknown = _unknown_event_counts(client, SHADOW_TABLE)
    if source_unknown or shadow_unknown:
        raise RuntimeError(
            "CN event compaction refuses unknown event types: "
            + json.dumps(
                {"source": source_unknown, "shadow": shadow_unknown}, sort_keys=True
            )
        )

    source_baseline = _baseline_rows(client, SOURCE_TABLE)
    shadow_baseline = _baseline_rows(client, SHADOW_TABLE)
    if pre_exchange:
        if source_baseline <= 0 or shadow_baseline != 0:
            raise RuntimeError(
                "CN event pre-exchange resume state is not structurally valid. Status: "
                + json.dumps(build_status(client=client), default=str, sort_keys=True)
            )
        source_keep = _fingerprint(client, SOURCE_TABLE, keep_only=True)
        shadow_keep = _fingerprint(client, SHADOW_TABLE, keep_only=True)
        if source_keep != shadow_keep:
            raise RuntimeError(
                "CN event pre-exchange shadow does not exactly match the source keep-set "
                "fingerprint; refusing EXCHANGE."
            )
    else:
        if source_baseline != 0 or shadow_baseline <= 0:
            raise RuntimeError(
                "CN event pending-drop state is not structurally valid. Status: "
                + json.dumps(build_status(client=client), default=str, sort_keys=True)
            )
        source_keep = _fingerprint(client, SOURCE_TABLE, keep_only=True)
        shadow_keep = _fingerprint(client, SHADOW_TABLE, keep_only=True)
        if source_keep != shadow_keep:
            raise RuntimeError(
                "CN event active compact table does not preserve the legacy keep-set "
                "fingerprint; refusing DROP."
            )
    return {
        "source_baseline_rows": source_baseline,
        "shadow_baseline_rows": shadow_baseline,
        "keep_fingerprint": source_keep,
    }


def _drop_validated_shadow(client: Any) -> tuple[int, int]:
    old_rows = _scalar(client, f"SELECT count() FROM {DATABASE}.{SHADOW_TABLE}")
    old_bytes = _active_bytes(client, SHADOW_TABLE)
    client.command(
        f"DROP TABLE {DATABASE}.{SHADOW_TABLE} SYNC",
        settings={"max_table_size_to_drop": 0},
    )
    if _table_exists(client, SHADOW_TABLE):
        raise RuntimeError("CN event shadow DROP returned but the table still exists.")
    return old_rows, old_bytes


def _resume_existing_shadow(client: Any) -> dict[str, Any]:
    status = build_status(client=client)
    state = status["state"]
    resumed_pre_exchange = False
    resumed_pending_drop = False

    if state == "PRE_EXCHANGE_SHADOW":
        _validate_shadow_pair(client, pre_exchange=True)
        client.command(
            f"EXCHANGE TABLES {DATABASE}.{SOURCE_TABLE} AND {DATABASE}.{SHADOW_TABLE}"
        )
        resumed_pre_exchange = True
    elif state == "POST_EXCHANGE_PENDING_DROP":
        resumed_pending_drop = True
    else:
        raise RuntimeError(
            "CN event compaction found an ambiguous temporary-table state; refusing to "
            "guess. Status: " + json.dumps(status, default=str, sort_keys=True)
        )

    validation = _validate_shadow_pair(client, pre_exchange=False)
    old_rows, old_bytes = _drop_validated_shadow(client)
    return {
        "status": "COMMITTED_FINAL",
        "resumed_pre_exchange": resumed_pre_exchange,
        "resumed_pending_drop": resumed_pending_drop,
        "active_keep_fingerprint": validation["keep_fingerprint"],
        "dropped_legacy_rows": old_rows,
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
            "CN observed-event compaction preflight failed: "
            + json.dumps(plan, default=str, sort_keys=True)
        )

    source_rows_before = int(plan["source_rows"])
    keep_rows_expected = int(plan["keep_rows"])
    keep_fingerprint_before = _fingerprint(client, SOURCE_TABLE, keep_only=True)

    client.command(
        f"CREATE TABLE {DATABASE}.{SHADOW_TABLE} AS {DATABASE}.{SOURCE_TABLE}"
    )
    exchanged = False
    try:
        client.command(
            f"""
            INSERT INTO {DATABASE}.{SHADOW_TABLE}
            SELECT *
            FROM {DATABASE}.{SOURCE_TABLE}
            WHERE NOT {_baseline_predicate()}
            """
        )
        source_rows_now = _scalar(
            client, f"SELECT count() FROM {DATABASE}.{SOURCE_TABLE}"
        )
        shadow_fingerprint = _fingerprint(client, SHADOW_TABLE, keep_only=True)
        if source_rows_now != source_rows_before:
            raise RuntimeError(
                "CN observed-event source row count changed during pre-exchange copy."
            )
        if shadow_fingerprint != keep_fingerprint_before:
            raise RuntimeError(
                "CN observed-event compact shadow fingerprint does not match the source "
                "keep-set; source remains unchanged."
            )
        if int(shadow_fingerprint["rows"]) != keep_rows_expected:
            raise RuntimeError(
                "CN observed-event compact shadow row count does not match the plan."
            )

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

    validation = _validate_shadow_pair(client, pre_exchange=False)
    old_rows, old_bytes = _drop_validated_shadow(client)
    return {
        "status": "COMMITTED_FINAL",
        "plan": plan,
        "resumed_pre_exchange": False,
        "resumed_pending_drop": False,
        "active_keep_fingerprint": validation["keep_fingerprint"],
        "dropped_legacy_rows": old_rows,
        "released_clickhouse_bytes_before_filesystem_reuse": old_bytes,
        "source_active_bytes_before": int(plan["source_active_bytes"]),
        "state_after": build_status(client=client)["state"],
        "note": (
            "Party relation events and every event carrying prior-state evidence were "
            "preserved. ClickHouse can reuse released filesystem space immediately; "
            "the outer Docker/WSL VHDX can remain physically large."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guarded Storage V2 compaction for CN observed events."
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
