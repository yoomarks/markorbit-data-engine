from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Mapping

from app.cn.entity_portfolio_activation_operator import _query_rows, _sha256
from app.us.recorded_party_history import READY_VERSION, READINESS_TABLE, SOURCE_TABLE

ASSIGNMENT_TABLES = (
    "us_assignment_record_history",
    "us_assignment_assignor_history",
    "us_assignment_assignee_history",
    "us_assignment_property_history",
)
TTAB_TABLES = (
    "us_ttab_proceeding_history",
    "us_ttab_party_history",
    "us_ttab_property_history",
)


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8").rstrip("\x00")
    return str(value or "")


def _source_table_stats(client: Any, table: str) -> dict[str, int]:
    rows = _query_rows(
        client,
        f"""
        SELECT count(), max(source_rank),
               groupBitXor(cityHash64(observation_key))
        FROM markorbit_facts.{table}
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError(f"source statistics unavailable for {table}")
    return {
        "row_count": int(rows[0][0] or 0),
        "max_source_rank": int(rows[0][1] or 0),
        "observation_hash_xor": int(rows[0][2] or 0),
    }


def source_stats(client: Any) -> dict[str, dict[str, int]]:
    result = {
        table: _source_table_stats(client, table)
        for table in (*ASSIGNMENT_TABLES, *TTAB_TABLES)
    }
    for table, stats in result.items():
        if stats["row_count"] <= 0 or stats["max_source_rank"] <= 0:
            raise RuntimeError(f"source table is empty or unranked: {table}")
    return result


def readiness_rows(client: Any) -> list[tuple[Any, ...]]:
    return _query_rows(
        client,
        f"""
        SELECT ready_version, assignment_max_rank, ttab_max_rank,
               implementation_sha, acceptance_hash
        FROM {READINESS_TABLE} FINAL
        """,
        settings={"max_threads": 1},
    )


def _target_fingerprint_expr() -> str:
    return """
        cityHash64(concat(
            normalized_name, '|', source_domain, '|', relationship_type, '|',
            toString(party_key), '|', party_name, '|', party_side, '|',
            source_role, '|', serial_number, '|', registration_number, '|',
            resource_type, '|', resource_id, '|',
            ifNull(toString(event_date), ''), '|',
            toString(source_package_id), '|', toString(source_rank), '|',
            relationship_observation_hash
        ))
    """.strip()


def target_stats(client: Any) -> dict[str, int]:
    rows = _query_rows(
        client,
        f"""
        SELECT count(), groupBitXor({_target_fingerprint_expr()})
        FROM {SOURCE_TABLE} FINAL
        """,
        settings={"max_threads": 4},
    )
    if len(rows) != 1:
        raise RuntimeError("US recorded party target statistics are unavailable")
    return {
        "row_count": int(rows[0][0] or 0),
        "row_hash_xor": int(rows[0][1] or 0),
    }


def _normalized_name_expr(name: str) -> str:
    return f"lowerUTF8(replaceRegexpAll(trimBoth({name}), '\\\\s+', ' '))"


def _batch_action(
    expected: Mapping[str, int], actual: Mapping[str, int]
) -> str:
    if dict(expected) == dict(actual):
        return "RECOVER"
    if (
        int(actual.get("row_count") or 0) == 0
        and int(actual.get("row_hash_xor") or 0) == 0
    ):
        return "INSERT"
    return "MISMATCH"


def benchmark_exact_name(client: Any) -> dict[str, Any]:
    rows = _query_rows(
        client,
        f"""
        SELECT normalized_name
        FROM {SOURCE_TABLE} FINAL
        ORDER BY normalized_name
        LIMIT 1
        """,
        settings={"max_threads": 1},
    )
    if not rows:
        return {"status": "EMPTY", "elapsed_ms": 0.0, "rows": 0}
    name = _text(rows[0][0])
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    started = time.perf_counter()
    probe = _query_rows(
        client,
        f"""
        SELECT source_domain, relationship_type, serial_number,
               resource_id, party_key
        FROM {SOURCE_TABLE} FINAL
        WHERE normalized_name = '{escaped}'
        ORDER BY normalized_name, source_domain, relationship_type,
                 serial_number, resource_id, party_key
        LIMIT 101
        """,
        settings={
            "max_threads": 1,
            "max_result_rows": 101,
            "result_overflow_mode": "throw",
        },
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    if elapsed_ms > 5_000:
        raise RuntimeError(
            f"US recorded party exact-name benchmark exceeded 5000 ms: {elapsed_ms}"
        )
    return {
        "status": "PASS",
        "normalized_name": name,
        "elapsed_ms": elapsed_ms,
        "rows": len(probe),
    }


def _write_readiness(
    client: Any,
    *,
    plan_sha256: str,
    implementation_sha: str,
    assignment_max_rank: int,
    ttab_max_rank: int,
) -> None:
    accepted = datetime.now(timezone.utc).isoformat()
    acceptance_hash = _sha256(
        {
            "plan_sha256": plan_sha256,
            "implementation_sha": implementation_sha,
            "assignment_max_rank": assignment_max_rank,
            "ttab_max_rank": ttab_max_rank,
        }
    )
    client.command(
        f"""
        INSERT INTO {READINESS_TABLE}
        (
            ready_version, assignment_max_rank, ttab_max_rank,
            implementation_sha, accepted_at, acceptance_hash
        )
        VALUES
        (
            '{READY_VERSION}',
            {int(assignment_max_rank)},
            {int(ttab_max_rank)},
            '{implementation_sha}',
            parseDateTime64BestEffort('{accepted}', 3),
            '{acceptance_hash}'
        )
        """
    )
