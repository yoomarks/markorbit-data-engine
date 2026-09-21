from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from app.us.accepted_target_read import accepted_us_target_read_client
from app.us.target_canary import TARGET_STORAGE_POLICY, WslNativeClickHouseClient
from app.us.ttab_correspondent_history import (
    READINESS_TABLE,
    READY_VERSION,
    TARGET_TABLE,
    WATERMARK_TABLE,
    TTABCorrespondentHistoryRequest,
    execute_page,
)

PLAN_VERSION = "US_TTAB_CORRESPONDENT_HISTORY_PRODUCTION_GATE_PLAN_V1"
RECEIPT_VERSION = "US_TTAB_CORRESPONDENT_HISTORY_PRODUCTION_GATE_RECEIPT_V1"
SOURCE_PARTY_TABLE = "markorbit_facts.us_ttab_party_history"
SOURCE_PROPERTY_TABLE = "markorbit_facts.us_ttab_property_history"
BENCHMARK_RUNS = 7
SLO_P95_MS = 400.0
MIN_FREE_RATIO_AFTER_ESTIMATE = 0.30
_HEX = frozenset("0123456789abcdef")
_PLAN_KEYS = {
    "version",
    "expected_main",
    "implementation_sha",
    "source_stats",
    "target_precondition",
    "capacity_contract",
    "target_schema",
    "acceptance",
    "mutation_scope",
}
NORMALIZED_NAME_SQL = (
    "lowerUTF8(replaceRegexpAll(trimBoth(p.correspondent_name), '\\\\s+', ' '))"
)
MARK_IDENTITY_SQL = (
    "if(pr.serial_number != '', concat('SERIAL:', pr.serial_number), "
    "concat('REG:', pr.registration_number))"
)
RELATIONSHIP_KEY_SQL = (
    "lower(hex(SHA256(concat("
    f"{NORMALIZED_NAME_SQL}, '\\x1f', p.proceeding_number, '\\x1f', p.side, "
    "'\\x1f', toString(p.ordinal), '\\x1f', "
    f"{MARK_IDENTITY_SQL}))))"
)
OBSERVATION_KEY_SQL = (
    "lower(hex(SHA256(concat("
    "toString(p.source_package_id), '\\x1f', p.proceeding_number, '\\x1f', "
    "toString(p.party_key), '\\x1f', toString(pr.property_key), '\\x1f', "
    f"{NORMALIZED_NAME_SQL}))))"
)
SOURCE_PROJECTION_SQL = f"""
SELECT
    {OBSERVATION_KEY_SQL} AS observation_key,
    {RELATIONSHIP_KEY_SQL} AS relationship_key,
    {NORMALIZED_NAME_SQL} AS normalized_name,
    trimBoth(p.correspondent_name) AS correspondent_name,
    p.correspondent_organization AS correspondent_organization,
    p.proceeding_number AS proceeding_number,
    p.side AS party_side,
    p.ordinal AS party_ordinal,
    p.party_name AS party_name,
    p.role AS party_role,
    p.party_key AS party_key,
    pr.property_key AS property_key,
    {MARK_IDENTITY_SQL} AS mark_identity,
    pr.serial_number AS serial_number,
    pr.registration_number AS registration_number,
    pr.mark_text AS mark_text,
    p.source_kind AS source_kind,
    p.source_snapshot_at AS source_snapshot_at,
    p.source_file AS source_file,
    p.source_package_id AS source_package_id,
    p.source_rank AS source_rank
FROM {SOURCE_PARTY_TABLE} AS p
INNER JOIN {SOURCE_PROPERTY_TABLE} AS pr
    ON p.source_package_id = pr.source_package_id
   AND p.source_rank = pr.source_rank
   AND p.proceeding_number = pr.proceeding_number
   AND p.side = pr.party_side
   AND p.ordinal = pr.party_ordinal
WHERE trimBoth(p.correspondent_name) != ''
  AND (pr.serial_number != '' OR pr.registration_number != '')
""".strip()
BINDING_HASH_SQL = (
    "cityHash64(concat("
    "toString(observation_key), '\\x1f', toString(relationship_key), '\\x1f', "
    "normalized_name, '\\x1f', correspondent_name, '\\x1f', "
    "correspondent_organization, '\\x1f', proceeding_number, '\\x1f', "
    "party_side, '\\x1f', toString(party_ordinal), '\\x1f', "
    "party_name, '\\x1f', party_role, '\\x1f', "
    "toString(party_key), '\\x1f', toString(property_key), '\\x1f', "
    "mark_identity, '\\x1f', serial_number, '\\x1f', registration_number, "
    "'\\x1f', mark_text, '\\x1f', source_kind, '\\x1f', "
    "toString(source_snapshot_at), '\\x1f', source_file, '\\x1f', "
    "toString(source_package_id), '\\x1f', toString(source_rank)))"
)


@dataclass(frozen=True, slots=True)
class SourceStats:
    joined_rows: int
    relationship_count: int
    normalized_name_count: int
    serial_count: int
    registration_count: int
    max_source_rank: int
    binding_sum: str
    binding_xor: str


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=_repo_root(),
        text=True,
        encoding="utf-8",
    ).strip()


def exact_main_sha() -> str:
    head = _git("rev-parse", "HEAD").lower()
    origin = _git("rev-parse", "origin/main").lower()
    if head != origin:
        raise RuntimeError("production gate requires HEAD == origin/main")
    if _git("status", "--porcelain=v1"):
        raise RuntimeError("production gate requires a clean exact-main worktree")
    if len(head) != 40 or any(ch not in _HEX for ch in head):
        raise RuntimeError("production gate could not resolve exact main SHA")
    return head
class MutableTargetClient:
    def __init__(self, base: WslNativeClickHouseClient | None = None) -> None:
        self._base = base or WslNativeClickHouseClient()

    def query(
        self, sql: str, *, settings: Mapping[str, Any] | None = None
    ) -> Any:
        settings = dict(settings or {})
        unknown = set(settings) - {"max_threads"}
        if unknown:
            raise ValueError(f"unsupported query settings: {sorted(unknown)}")
        statement = sql.rstrip().rstrip(";")
        if settings:
            statement += f" SETTINGS max_threads = {int(settings['max_threads'])}"
        return self._base.query(statement)

    def command(self, sql: str) -> str:
        return self._base.command(sql)


def _rows(client: Any, sql: str) -> list[list[Any]]:
    result = client.query(sql, settings={"max_threads": 1})
    return [list(row) for row in result.result_rows]


def _sql_text(value: object) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
def source_stats(client: Any) -> SourceStats:
    row = _rows(
        client,
        f"""
        SELECT
            count(),
            uniqExact(relationship_key),
            uniqExact(normalized_name),
            uniqExactIf(serial_number, serial_number != ''),
            uniqExactIf(registration_number, registration_number != ''),
            max(source_rank),
            toString(sum({BINDING_HASH_SQL})),
            toString(groupBitXor({BINDING_HASH_SQL}))
        FROM
        (
            {SOURCE_PROJECTION_SQL}
        )
        """,
    )[0]
    return SourceStats(
        joined_rows=int(row[0]),
        relationship_count=int(row[1]),
        normalized_name_count=int(row[2]),
        serial_count=int(row[3]),
        registration_count=int(row[4]),
        max_source_rank=int(row[5]),
        binding_sum=str(row[6]),
        binding_xor=str(row[7]),
    )
def _table_exists(client: Any, name: str) -> bool:
    rows = _rows(
        client,
        f"""
        SELECT count()
        FROM system.tables
        WHERE database='markorbit_facts' AND name={_sql_text(name)}
        """,
    )
    return bool(rows and int(rows[0][0]) == 1)


def _current_watermark(client: Any) -> dict[str, object] | None:
    if not _table_exists(
        client, "us_ttab_correspondent_mark_history_watermark"
    ):
        return None
    rows = _rows(
        client,
        f"""
        SELECT serving_generation, source_max_rank,
               toString(source_package_id), updated_at
        FROM {WATERMARK_TABLE}
        WHERE ready_version={_sql_text(READY_VERSION)}
        ORDER BY serving_generation DESC, updated_at DESC
        LIMIT 1
        """,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "serving_generation": int(row[0]),
        "source_max_rank": int(row[1]),
        "source_package_id": str(row[2]),
        "updated_at": row[3],
    }


def ready_marker(client: Any) -> str | None:
    if not _table_exists(client, "us_ttab_correspondent_mark_history_readiness"):
        return None
    rows = _rows(
        client,
        f"""
        SELECT ready_version
        FROM {READINESS_TABLE} FINAL
        WHERE ready_version={_sql_text(READY_VERSION)}
        ORDER BY accepted_at DESC
        LIMIT 1
        """,
    )
    return str(rows[0][0]) if rows else None


def lookup_stats(client: Any) -> dict[str, object]:
    if not _table_exists(client, "us_ttab_correspondent_mark_history"):
        return {
            "exists": False,
            "visible_rows": 0,
            "relationship_count": 0,
            "normalized_name_count": 0,
            "max_source_rank": 0,
            "binding_sum": "0",
            "binding_xor": "0",
        }
    row = _rows(
        client,
        f"""
        SELECT count(), uniqExact(relationship_key), uniqExact(normalized_name),
               max(source_rank), toString(sum({BINDING_HASH_SQL})),
               toString(groupBitXor({BINDING_HASH_SQL}))
        FROM {TARGET_TABLE} FINAL
        """,
    )[0]
    return {
        "exists": True,
        "visible_rows": int(row[0]),
        "relationship_count": int(row[1]),
        "normalized_name_count": int(row[2]),
        "max_source_rank": int(row[3]),
        "binding_sum": str(row[4]),
        "binding_xor": str(row[5]),
    }


def disk_state(client: Any) -> dict[str, int]:
    row = _rows(
        client,
        "SELECT free_space,total_space FROM system.disks WHERE name='hot_us'",
    )[0]
    return {"free_bytes": int(row[0]), "total_bytes": int(row[1])}
def source_table_bytes(client: Any) -> int:
    row = _rows(
        client,
        """
        SELECT sum(total_bytes)
        FROM system.tables
        WHERE database='markorbit_facts'
          AND name IN ('us_ttab_party_history','us_ttab_property_history')
        """,
    )[0]
    return int(row[0] or 0)


def capacity_contract(client: Any, source: SourceStats) -> dict[str, object]:
    disk = disk_state(client)
    source_bytes = source_table_bytes(client)
    estimated = max(source_bytes * 3, source.joined_rows * 768, 1)
    projected = int(disk["free_bytes"]) - estimated
    floor = int(int(disk["total_bytes"]) * MIN_FREE_RATIO_AFTER_ESTIMATE)
    if projected < floor:
        raise RuntimeError("hot_us reserve would fall below the frozen 30% floor")
    return {
        "hot_us": disk,
        "source_table_bytes": source_bytes,
        "estimated_lookup_bytes_ceiling": estimated,
        "minimum_free_ratio_after_estimate": MIN_FREE_RATIO_AFTER_ESTIMATE,
        "projected_free_bytes": projected,
    }


def target_table_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {TARGET_TABLE}
(
    observation_key FixedString(64),
    relationship_key FixedString(64),
    normalized_name String,
    correspondent_name String,
    correspondent_organization String,
    proceeding_number String,
    party_side LowCardinality(String),
    party_ordinal UInt16,
    party_name String,
    party_role String,
    party_key FixedString(64),
    property_key FixedString(64),
    mark_identity String,
    serial_number String,
    registration_number String,
    mark_text String,
    source_kind LowCardinality(String),
    source_snapshot_at DateTime64(3, 'UTC'),
    source_file String,
    source_package_id UUID,
    source_rank UInt64,
    serving_generation UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (normalized_name, serving_generation, observation_key)
SETTINGS storage_policy = '{TARGET_STORAGE_POLICY}'
""".strip()
def readiness_table_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {READINESS_TABLE}
(
    ready_version LowCardinality(String),
    accepted_source_max_rank UInt64,
    accepted_serving_generation UInt64,
    source_joined_rows UInt64,
    target_rows UInt64,
    target_relationship_count UInt64,
    implementation_sha FixedString(40),
    accepted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(accepted_at)
ORDER BY ready_version
SETTINGS storage_policy = '{TARGET_STORAGE_POLICY}'
""".strip()


def watermark_table_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {WATERMARK_TABLE}
(
    ready_version LowCardinality(String),
    serving_generation UInt64,
    source_max_rank UInt64,
    source_package_id UUID,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (ready_version, serving_generation)
SETTINGS storage_policy = '{TARGET_STORAGE_POLICY}'
""".strip()


def _backfill_sql() -> str:
    return f"""
INSERT INTO {TARGET_TABLE}
(
    observation_key, relationship_key, normalized_name,
    correspondent_name, correspondent_organization,
    proceeding_number, party_side, party_ordinal, party_name, party_role,
    party_key, property_key, mark_identity, serial_number, registration_number, mark_text,
    source_kind, source_snapshot_at, source_file, source_package_id, source_rank,
    serving_generation
)
SELECT source.*, 1 AS serving_generation
FROM
(
{SOURCE_PROJECTION_SQL}
) AS source
SETTINGS max_threads=1, max_insert_threads=1, join_algorithm='grace_hash'
""".strip()
def prepare_plan(
    output_path: Path,
    *,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = exact_main_sha,
) -> dict[str, object]:
    target = client or accepted_us_target_read_client()
    main_sha = main_sha_getter()
    source = source_stats(target)
    lookup = lookup_stats(target)
    marker = ready_marker(target)
    watermark = _current_watermark(target)
    if marker is not None:
        raise RuntimeError("prepare requires TTAB correspondent READY absent")
    visible = int(lookup["visible_rows"])
    if visible not in {0, source.joined_rows}:
        raise RuntimeError("partial TTAB correspondent target; prepare refused")
    if visible == source.joined_rows and (
        str(lookup["binding_sum"]) != source.binding_sum
        or str(lookup["binding_xor"]) != source.binding_xor
    ):
        raise RuntimeError("populated TTAB correspondent target digest mismatch")
    if watermark is not None and (
        visible != source.joined_rows
        or int(watermark["serving_generation"]) != 1
        or int(watermark["source_max_rank"]) != source.max_source_rank
    ):
        raise RuntimeError(
            "TTAB correspondent pre-READY watermark does not match complete generation 1"
        )
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha,
        "implementation_sha": main_sha,
        "source_stats": asdict(source),
        "target_precondition": {
            "lookup": lookup,
            "ready_marker": marker,
            "watermark": watermark,
        },
        "capacity_contract": capacity_contract(target, source),
        "target_schema": {
            "storage_policy": TARGET_STORAGE_POLICY,
            "table_ddl_sha256": hashlib.sha256(
                target_table_ddl().encode("utf-8")
            ).hexdigest(),
            "readiness_ddl_sha256": hashlib.sha256(
                readiness_table_ddl().encode("utf-8")
            ).hexdigest(),
            "watermark_ddl_sha256": hashlib.sha256(
                watermark_table_ddl().encode("utf-8")
            ).hexdigest(),
        },
        "acceptance": {
            "benchmark_runs": BENCHMARK_RUNS,
            "indexed_historical_relationship_p95_ms": SLO_P95_MS,
            "ready_version": READY_VERSION,
            "sample_rows": 200,
        },
        "mutation_scope": {
            "create_table": TARGET_TABLE,
            "create_readiness_table": READINESS_TABLE,
            "create_watermark_table": WATERMARK_TABLE,
            "backfill_sources": [SOURCE_PARTY_TABLE, SOURCE_PROPERTY_TABLE],
            "final_ready_version": READY_VERSION,
            "operation": "CREATE_INSERT_READY_ONLY",
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return envelope
def _validate_plan(plan: Mapping[str, Any]) -> None:
    if set(plan) != _PLAN_KEYS:
        raise RuntimeError("TTAB correspondent production-gate plan keys drifted")
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("unsupported TTAB correspondent plan version")
    if dict(plan.get("acceptance") or {}) != {
        "benchmark_runs": BENCHMARK_RUNS,
        "indexed_historical_relationship_p95_ms": SLO_P95_MS,
        "ready_version": READY_VERSION,
        "sample_rows": 200,
    }:
        raise RuntimeError("TTAB correspondent acceptance contract drifted")
    if dict(plan.get("mutation_scope") or {}) != {
        "create_table": TARGET_TABLE,
        "create_readiness_table": READINESS_TABLE,
        "create_watermark_table": WATERMARK_TABLE,
        "backfill_sources": [SOURCE_PARTY_TABLE, SOURCE_PROPERTY_TABLE],
        "final_ready_version": READY_VERSION,
        "operation": "CREATE_INSERT_READY_ONLY",
    }:
        raise RuntimeError("TTAB correspondent mutation scope drifted")


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    expected = expected_sha.strip().lower()
    if len(expected) != 64 or any(ch not in _HEX for ch in expected):
        raise ValueError("plan SHA must be exact 64-character lowercase hex")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"plan", "plan_sha256"}:
        raise RuntimeError("malformed TTAB correspondent plan envelope")
    plan = dict(envelope["plan"])
    actual = _sha256(plan)
    if envelope["plan_sha256"] != actual or actual != expected:
        raise RuntimeError("TTAB correspondent plan SHA mismatch")
    _validate_plan(plan)
    return plan


def _assert_plan_live(
    plan: Mapping[str, Any],
    *,
    client: Any,
    main_sha_getter: Callable[[], str],
) -> SourceStats:
    main_sha = main_sha_getter()
    if main_sha != str(plan["expected_main"]):
        raise RuntimeError("TTAB correspondent production-gate main SHA drifted")
    if main_sha != str(plan["implementation_sha"]):
        raise RuntimeError("TTAB correspondent implementation SHA drifted")
    observed = source_stats(client)
    frozen = SourceStats(**dict(plan["source_stats"]))
    if observed != frozen:
        raise RuntimeError("accepted TTAB correspondent source truth drifted")
    live_capacity = capacity_contract(client, observed)
    frozen_capacity = dict(plan["capacity_contract"])
    if int(live_capacity["hot_us"]["total_bytes"]) != int(
        frozen_capacity["hot_us"]["total_bytes"]
    ):
        raise RuntimeError("hot_us total capacity drifted")
    schema = dict(plan["target_schema"])
    if schema["storage_policy"] != TARGET_STORAGE_POLICY:
        raise RuntimeError("TTAB correspondent target storage policy drifted")
    if schema["table_ddl_sha256"] != hashlib.sha256(
        target_table_ddl().encode("utf-8")
    ).hexdigest():
        raise RuntimeError("TTAB correspondent target DDL hash drifted")
    if schema["readiness_ddl_sha256"] != hashlib.sha256(
        readiness_table_ddl().encode("utf-8")
    ).hexdigest():
        raise RuntimeError("TTAB correspondent readiness DDL hash drifted")
    if schema["watermark_ddl_sha256"] != hashlib.sha256(
        watermark_table_ddl().encode("utf-8")
    ).hexdigest():
        raise RuntimeError("TTAB correspondent watermark DDL hash drifted")
    return observed


def _sample_mismatches(client: Any, limit: int = 200) -> dict[str, int]:
    sample = _rows(
        client,
        f"""
        SELECT toString(observation_key), toString({BINDING_HASH_SQL})
        FROM {TARGET_TABLE} FINAL
        ORDER BY normalized_name, observation_key
        LIMIT {int(limit)}
        """,
    )
    if not sample:
        return {"checked": 0, "mismatches": 0}
    keys = ", ".join(_sql_text(row[0]) for row in sample)
    source = _rows(
        client,
        f"""
        SELECT observation_key, toString({BINDING_HASH_SQL})
        FROM
        (
            {SOURCE_PROJECTION_SQL}
        )
        WHERE observation_key IN ({keys})
        """,
    )
    source_map = {str(row[0]): str(row[1]) for row in source}
    mismatches = sum(
        1 for key, binding in sample if source_map.get(str(key)) != str(binding)
    )
    return {"checked": len(sample), "mismatches": mismatches}


def verify_completeness(
    client: Any, expected_source: SourceStats
) -> dict[str, object]:
    live = source_stats(client)
    lookup = lookup_stats(client)
    sample = _sample_mismatches(client)
    complete = (
        live == expected_source
        and lookup["exists"] is True
        and int(lookup["visible_rows"]) == expected_source.joined_rows
        and int(lookup["relationship_count"]) == expected_source.relationship_count
        and int(lookup["normalized_name_count"])
        == expected_source.normalized_name_count
        and str(lookup["binding_sum"]) == expected_source.binding_sum
        and str(lookup["binding_xor"]) == expected_source.binding_xor
        and int(sample["checked"]) == 200
        and int(sample["mismatches"]) == 0
    )
    return {
        "complete": complete,
        "source": asdict(live),
        "lookup": lookup,
        "sample": sample,
        "count_match": int(lookup["visible_rows"]) == expected_source.joined_rows,
        "relationship_count_match": int(lookup["relationship_count"])
        == expected_source.relationship_count,
        "normalized_name_count_match": int(lookup["normalized_name_count"])
        == expected_source.normalized_name_count,
        "binding_sum_match": str(lookup["binding_sum"])
        == expected_source.binding_sum,
        "binding_xor_match": str(lookup["binding_xor"])
        == expected_source.binding_xor,
    }


def _pick_benchmark_name(client: Any) -> str:
    rows = _rows(
        client,
        f"""
        SELECT normalized_name, uniqExact(relationship_key) AS n
        FROM {TARGET_TABLE} FINAL
        GROUP BY normalized_name
        HAVING n BETWEEN 51 AND 100
        ORDER BY normalized_name
        LIMIT 1
        """,
    )
    if not rows:
        rows = _rows(
            client,
            f"""
            SELECT normalized_name, uniqExact(relationship_key) AS n
            FROM {TARGET_TABLE} FINAL
            GROUP BY normalized_name
            HAVING n BETWEEN 2 AND 50
            ORDER BY normalized_name
            LIMIT 1
            """,
        )
    if len(rows) != 1:
        raise RuntimeError("could not select bounded TTAB correspondent benchmark name")
    return str(rows[0][0])


def _page_benchmark(
    client: Any,
    normalized_name: str,
    serving_generation: int,
    *,
    position: tuple[str, str, str] | None = None,
) -> tuple[float, list[list[Any]]]:
    cursor = ""
    if position is not None:
        proceeding, identity, relation = position
        cursor = (
            "AND tuple(proceeding_number, mark_identity, "
            "toString(relationship_key)) > "
            f"tuple({_sql_text(proceeding)}, {_sql_text(identity)}, "
            f"{_sql_text(relation)})"
        )
    sql = f"""
    SELECT proceeding_number, mark_identity, toString(relationship_key)
    FROM
    (
        SELECT relationship_key,
               argMax(mark_identity, tuple(source_rank, observation_key))
                   AS mark_identity,
               argMax(proceeding_number, tuple(source_rank, observation_key))
                   AS proceeding_number
        FROM {TARGET_TABLE} FINAL
        WHERE normalized_name={_sql_text(normalized_name)}
          AND serving_generation <= {int(serving_generation)}
        GROUP BY relationship_key
    ) AS grouped
    WHERE 1 {cursor}
    ORDER BY proceeding_number, mark_identity, relationship_key
    LIMIT 51
    """
    started = time.perf_counter()
    rows = _rows(client, sql)
    return (time.perf_counter() - started) * 1000.0, rows


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            int((0.95 * len(ordered)) + 0.999999) - 1,
        ),
    )
    return ordered[index]


def benchmark_lookup(
    client: Any, serving_generation: int
) -> dict[str, object]:
    name = _pick_benchmark_name(client)
    first: list[float] = []
    second: list[float] = []
    first_count = 0
    for _ in range(BENCHMARK_RUNS):
        ms1, rows1 = _page_benchmark(client, name, serving_generation)
        if not rows1:
            raise RuntimeError("TTAB correspondent benchmark returned no rows")
        first.append(ms1)
        first_count = len(rows1)
        if len(rows1) > 50:
            last = rows1[49]
            ms2, _ = _page_benchmark(
                client,
                name,
                serving_generation,
                position=(str(last[0]), str(last[1]), str(last[2])),
            )
            second.append(ms2)
    first_p95 = _p95(first)
    second_p95 = _p95(second) if second else 0.0
    passed = first_p95 <= SLO_P95_MS and (
        not second or second_p95 <= SLO_P95_MS
    )
    return {
        "normalized_name": name,
        "runs": BENCHMARK_RUNS,
        "first_page_elapsed_ms": [round(v, 3) for v in first],
        "first_page_p95_ms": round(first_p95, 3),
        "subsequent_page_elapsed_ms": [round(v, 3) for v in second],
        "subsequent_page_p95_ms": round(second_p95, 3),
        "sample_first_page_rows": first_count,
        "slo_p95_ms": SLO_P95_MS,
        "passed": passed,
    }


def authority_token(plan_sha: str) -> str:
    return f"GO #788 US-TTAB-CORRESPONDENT-HISTORY {plan_sha} ACTIVATE"


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            dict(payload),
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def _readiness_insert_sql(
    source: SourceStats,
    lookup: Mapping[str, Any],
    implementation_sha: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
    return f"""
INSERT INTO {READINESS_TABLE}
(
    ready_version, accepted_source_max_rank, accepted_serving_generation,
    source_joined_rows, target_rows, target_relationship_count,
    implementation_sha, accepted_at
)
VALUES
(
    '{READY_VERSION}',
    {source.max_source_rank},
    1,
    {source.joined_rows},
    {int(lookup['visible_rows'])},
    {int(lookup['relationship_count'])},
    '{implementation_sha}',
    toDateTime64('{now}', 3, 'UTC')
)
""".strip()


def _initial_watermark_sql(source: SourceStats) -> str:
    return f"""
INSERT INTO {WATERMARK_TABLE}
(ready_version, serving_generation, source_max_rank, source_package_id)
VALUES
('{READY_VERSION}', 1, {source.max_source_rank},
 toUUID('00000000-0000-0000-0000-000000000000'))
""".strip()


def execute_plan(
    plan_path: Path,
    *,
    plan_sha: str,
    authority: str,
    receipt_path: Path,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = exact_main_sha,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    if authority != authority_token(plan_sha):
        raise PermissionError("exact #788 authority token is required")
    target = client or MutableTargetClient()
    stage = "PRECHECK"
    try:
        source = _assert_plan_live(
            plan,
            client=target,
            main_sha_getter=main_sha_getter,
        )
        marker = ready_marker(target)
        if marker == READY_VERSION:
            watermark = _current_watermark(target)
            if (
                watermark is None
                or int(watermark["serving_generation"]) < 1
                or int(watermark["source_max_rank"]) < source.max_source_rank
            ):
                raise RuntimeError("READY TTAB correspondent watermark is invalid")
            completeness = verify_completeness(target, source)
            if completeness["complete"] is not True:
                raise RuntimeError(
                    "READY TTAB correspondent history is not complete"
                )
            receipt = {
                "version": RECEIPT_VERSION,
                "status": "SUCCESS",
                "replayed": True,
                "plan_sha256": plan_sha,
                "implementation_sha": str(plan["implementation_sha"]),
                "completeness": completeness,
            }
            _write_receipt(receipt_path, receipt)
            return receipt
        if marker is not None:
            raise RuntimeError(
                f"unexpected TTAB correspondent READY marker: {marker}"
            )

        stage = "SCHEMA"
        if not _table_exists(
            target, "us_ttab_correspondent_mark_history"
        ):
            target.command(target_table_ddl())
        if not _table_exists(
            target, "us_ttab_correspondent_mark_history_readiness"
        ):
            target.command(readiness_table_ddl())
        if not _table_exists(
            target, "us_ttab_correspondent_mark_history_watermark"
        ):
            target.command(watermark_table_ddl())
        _assert_plan_live(
            plan,
            client=target,
            main_sha_getter=main_sha_getter,
        )

        lookup = lookup_stats(target)
        visible = int(lookup["visible_rows"])
        if visible not in {0, source.joined_rows}:
            raise RuntimeError(
                "TTAB correspondent history is partially populated; "
                "automatic resume refused"
            )
        if visible == source.joined_rows and (
            str(lookup["binding_sum"]) != source.binding_sum
            or str(lookup["binding_xor"]) != source.binding_xor
        ):
            raise RuntimeError(
                "TTAB correspondent populated target digest mismatch"
            )

        if visible == 0:
            stage = "BACKFILL"
            target.command(_backfill_sql())

        stage = "COMPLETENESS"
        _assert_plan_live(
            plan,
            client=target,
            main_sha_getter=main_sha_getter,
        )
        completeness = verify_completeness(target, source)
        if completeness["complete"] is not True:
            raise RuntimeError(
                "TTAB correspondent completeness verification failed"
            )

        stage = "BENCHMARK"
        benchmark = benchmark_lookup(target, 1)
        _assert_plan_live(
            plan,
            client=target,
            main_sha_getter=main_sha_getter,
        )
        if benchmark["passed"] is not True:
            raise RuntimeError(
                "TTAB correspondent production benchmark failed"
            )

        stage = "WATERMARK"
        watermark = _current_watermark(target)
        if watermark is None:
            target.command(_initial_watermark_sql(source))
            watermark = _current_watermark(target)
        if (
            watermark is None
            or int(watermark["serving_generation"]) != 1
            or int(watermark["source_max_rank"]) != source.max_source_rank
        ):
            raise RuntimeError(
                "TTAB correspondent generation-1 watermark did not become visible"
            )

        stage = "READY"
        lookup = lookup_stats(target)
        target.command(
            _readiness_insert_sql(
                source,
                lookup,
                str(plan["implementation_sha"]),
            )
        )
        if ready_marker(target) != READY_VERSION:
            raise RuntimeError(
                "TTAB correspondent READY did not become visible"
            )

        stage = "RUNTIME_SMOKE"
        smoke = execute_page(
            TTABCorrespondentHistoryRequest(
                name=str(benchmark["normalized_name"]),
                page_size=10,
            ),
            client=accepted_us_target_read_client(),
        )
        if int(smoke.get("result_count") or 0) < 1:
            raise RuntimeError(
                "TTAB correspondent runtime smoke returned no historical fact"
            )

        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "replayed": False,
            "plan_sha256": plan_sha,
            "implementation_sha": str(plan["implementation_sha"]),
            "source_stats": asdict(source),
            "completeness": completeness,
            "benchmark": benchmark,
            "runtime_smoke": {
                "normalized_name": smoke["normalized_name"],
                "result_count": smoke["result_count"],
                "semantics": smoke["semantics"],
            },
            "ready_marker": READY_VERSION,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_receipt(receipt_path, receipt)
        return receipt
    except Exception as exc:
        failure = {
            "version": RECEIPT_VERSION,
            "status": "FAILED",
            "stage": stage,
            "plan_sha256": plan_sha,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_receipt(receipt_path, failure)
        raise


def _default_output(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _repo_root() / "reports" / f"{prefix}_{stamp}.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Governed US TTAB correspondent-history production gate"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path)
    execute = sub.add_parser("execute")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--plan-sha", required=True)
    execute.add_argument("--authority-token", required=True)
    execute.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)

    if args.command == "prepare":
        path = args.output or _default_output(
            "production_us_ttab_correspondent_history_plan"
        )
        envelope = prepare_plan(path)
        print(
            json.dumps(
                {
                    "plan_path": str(path),
                    **envelope,
                    "required_authority_token": authority_token(
                        str(envelope["plan_sha256"])
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    receipt = args.receipt or _default_output(
        "production_us_ttab_correspondent_history_receipt"
    )
    result = execute_plan(
        args.plan,
        plan_sha=args.plan_sha,
        authority=args.authority_token,
        receipt_path=receipt,
    )
    print(
        json.dumps(
            {"receipt_path": str(receipt), **result},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
