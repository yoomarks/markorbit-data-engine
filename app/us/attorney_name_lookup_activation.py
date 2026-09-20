from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from app.us.accepted_target_read import accepted_us_target_read_client
from app.us.applicant_candidate_backfill_control import (
    USApplicantServingEpoch,
    current_us_applicant_serving_epoch,
)
from app.us.attorney_name_lookup import (
    ATTORNEY_NAME_LOOKUP_READY_VERSION,
    US_ATTORNEY_NAME_LOOKUP_TABLE,
    attorneys_by_name,
)
from app.us.target_canary import (
    TARGET_DATABASE,
    TARGET_STORAGE_POLICY,
    WslNativeClickHouseClient,
)

PLAN_VERSION = "US_ATTORNEY_NAME_LOOKUP_PRODUCTION_GATE_PLAN_V1"
RECEIPT_VERSION = "US_ATTORNEY_NAME_LOOKUP_PRODUCTION_GATE_RECEIPT_V1"
MV_NAME = "us_attorney_name_candidates_from_correspondent_mv"
SOURCE_TABLE = "markorbit_facts.us_correspondent_current"
READY_COMPONENT = "US_ATTORNEY_NAME_LOOKUP"
EXPECTED_COLUMNS = [
    ("normalized_name", "String"),
    ("serial_number", "String"),
    ("correspondent_key", "FixedString(64)"),
    ("attorney_name", "String"),
    ("attorney_docket_number", "String"),
    ("source_package_kind", "LowCardinality(String)"),
    ("source_effective_date", "Nullable(Date32)"),
    ("source_file", "String"),
    ("source_row_hash", "FixedString(64)"),
    ("source_package_id", "UUID"),
    ("record_hash", "FixedString(64)"),
    ("source_rank", "UInt64"),
    ("ingested_at", "DateTime64(3, 'UTC')"),
    ("is_deleted", "UInt8"),
]
SLO_P95_MS = 300.0
BENCHMARK_RUNS = 7
MIN_FREE_RATIO_AFTER_ESTIMATE = 0.30
_HEX40 = frozenset("0123456789abcdef")
_PLAN_KEYS = {
    "version",
    "expected_main",
    "implementation_sha",
    "source_epoch",
    "source_stats",
    "target_precondition",
    "capacity_contract",
    "target_schema",
    "acceptance",
    "mutation_scope",
}

SOURCE_NORMALIZED = (
    "lowerUTF8(replaceRegexpAll(trimBoth(attorney_name), '\\\\s+', ' '))"
)
SOURCE_BINDING_HASH = (
    "cityHash64(concat("
    f"{SOURCE_NORMALIZED}, '\\\\x1f', serial_number, '\\\\x1f', "
    "toString(correspondent_key), '\\\\x1f', toString(record_hash), "
    "'\\\\x1f', toString(source_rank)))"
)
LOOKUP_BINDING_HASH = (
    "cityHash64(concat(normalized_name, '\\\\x1f', serial_number, '\\\\x1f', "
    "toString(correspondent_key), '\\\\x1f', toString(record_hash), "
    "'\\\\x1f', toString(source_rank)))"
)


@dataclass(frozen=True, slots=True)
class SourceStats:
    active_rows: int
    qualifying_rows: int
    max_source_rank: int
    binding_sum: str
    binding_xor: str

    def to_dict(self) -> dict[str, object]:
        return {
            "active_rows": self.active_rows,
            "qualifying_rows": self.qualifying_rows,
            "max_source_rank": self.max_source_rank,
            "binding_sum": self.binding_sum,
            "binding_xor": self.binding_xor,
        }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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
    dirty = _git("status", "--porcelain=v1")
    if head != origin:
        raise RuntimeError("production gate requires HEAD == origin/main")
    if dirty:
        raise RuntimeError("production gate requires a clean exact-main worktree")
    if len(head) != 40 or any(ch not in _HEX40 for ch in head):
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
            raise ValueError(f"unsupported target query settings: {sorted(unknown)}")
        statement = sql.rstrip().rstrip(";")
        if settings:
            statement += f" SETTINGS max_threads = {int(settings['max_threads'])}"
        return self._base.query(statement)

    def command(self, sql: str) -> str:
        return self._base.command(sql)


def _rows(client: Any, sql: str) -> list[list[Any]]:
    result = client.query(sql, settings={"max_threads": 1})
    return [list(row) for row in result.result_rows]


def source_stats(client: Any) -> SourceStats:
    row = _rows(
        client,
        f"""
        SELECT
            countIf(is_deleted=0),
            countIf(is_deleted=0 AND attorney_name!=''),
            maxIf(source_rank, is_deleted=0),
            toString(sumIf({SOURCE_BINDING_HASH}, is_deleted=0 AND attorney_name!='')),
            toString(groupBitXorIf({SOURCE_BINDING_HASH}, is_deleted=0 AND attorney_name!=''))
        FROM {SOURCE_TABLE} FINAL
        """,
    )
    if len(row) != 1:
        raise RuntimeError("expected one accepted-target correspondent stats row")
    values = row[0]
    return SourceStats(
        active_rows=int(values[0]),
        qualifying_rows=int(values[1]),
        max_source_rank=int(values[2]),
        binding_sum=str(values[3]),
        binding_xor=str(values[4]),
    )


def _table_metadata(client: Any) -> list[list[Any]]:
    return _rows(
        client,
        f"""
        SELECT engine, sorting_key, storage_policy
        FROM system.tables
        WHERE database='{TARGET_DATABASE}'
          AND name='us_attorney_name_candidate_lookup'
        """,
    )


def lookup_stats(client: Any) -> dict[str, object]:
    meta = _table_metadata(client)
    if not meta:
        return {
            "exists": False,
            "visible_rows": 0,
            "distinct_names": 0,
            "max_source_rank": 0,
            "binding_sum": "0",
            "binding_xor": "0",
        }
    if len(meta) != 1:
        raise RuntimeError("attorney lookup table metadata is not exact-one")
    engine, sorting_key, storage_policy = (str(value) for value in meta[0])
    normalized_sort = (
        sorting_key.replace(chr(96), "").replace("(", "").replace(")", "").strip()
    )
    if engine != "ReplacingMergeTree":
        raise RuntimeError("attorney lookup engine drifted")
    if normalized_sort != "normalized_name, serial_number, correspondent_key":
        raise RuntimeError("attorney lookup sorting key drifted")
    if storage_policy != TARGET_STORAGE_POLICY:
        raise RuntimeError("attorney lookup storage policy drifted")
    columns = _rows(
        client,
        f"""
        SELECT name, type
        FROM system.columns
        WHERE database='{TARGET_DATABASE}'
          AND table='us_attorney_name_candidate_lookup'
        ORDER BY position
        """,
    )
    observed_columns = [(str(row[0]), str(row[1])) for row in columns]
    if observed_columns != EXPECTED_COLUMNS:
        raise RuntimeError("attorney lookup column contract drifted")
    row = _rows(
        client,
        f"""
        SELECT countIf(is_deleted=0), uniqExactIf(normalized_name, is_deleted=0),
               maxIf(source_rank, is_deleted=0),
               toString(sumIf({LOOKUP_BINDING_HASH}, is_deleted=0)),
               toString(groupBitXorIf({LOOKUP_BINDING_HASH}, is_deleted=0))
        FROM {US_ATTORNEY_NAME_LOOKUP_TABLE} FINAL
        """,
    )[0]
    return {
        "exists": True,
        "visible_rows": int(row[0]),
        "distinct_names": int(row[1]),
        "max_source_rank": int(row[2]),
        "binding_sum": str(row[3]),
        "binding_xor": str(row[4]),
    }


def ready_marker(client: Any) -> str | None:
    rows = _rows(
        client,
        f"""
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component='{READY_COMPONENT}'
        LIMIT 1
        """,
    )
    if not rows:
        return None
    return str(rows[0][0])


def disk_state(client: Any) -> dict[str, object]:
    rows = _rows(
        client,
        """
        SELECT name, free_space, total_space
        FROM system.disks
        WHERE name='hot_us'
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("hot_us disk metadata is not exact-one")
    return {
        "name": str(rows[0][0]),
        "free_bytes": int(rows[0][1]),
        "total_bytes": int(rows[0][2]),
    }


def source_table_bytes(client: Any) -> int:
    rows = _rows(
        client,
        f"""
        SELECT total_bytes, storage_policy
        FROM system.tables
        WHERE database='{TARGET_DATABASE}'
          AND name='us_correspondent_current'
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("accepted correspondent table metadata is not exact-one")
    if str(rows[0][1]) != TARGET_STORAGE_POLICY:
        raise RuntimeError("accepted correspondent storage policy drifted")
    return int(rows[0][0])


def _capacity_contract(client: Any) -> dict[str, object]:
    disk = disk_state(client)
    source_bytes = source_table_bytes(client)
    estimated_lookup_bytes = max(source_bytes * 2, 1)
    projected_free = int(disk["free_bytes"]) - estimated_lookup_bytes
    minimum_free = int(int(disk["total_bytes"]) * MIN_FREE_RATIO_AFTER_ESTIMATE)
    if projected_free < minimum_free:
        raise RuntimeError("hot_us reserve would fall below the frozen 30% floor")
    return {
        "hot_us": disk,
        "source_table_bytes": source_bytes,
        "estimated_lookup_bytes_ceiling": estimated_lookup_bytes,
        "minimum_free_ratio_after_estimate": MIN_FREE_RATIO_AFTER_ESTIMATE,
        "projected_free_bytes": projected_free,
    }


def _epoch_dict(epoch: USApplicantServingEpoch) -> dict[str, object]:
    return dict(epoch.to_dict())


def target_table_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {US_ATTORNEY_NAME_LOOKUP_TABLE}
(
    normalized_name String,
    serial_number String,
    correspondent_key FixedString(64),
    attorney_name String,
    attorney_docket_number String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    source_package_id UUID,
    record_hash FixedString(64),
    source_rank UInt64,
    ingested_at DateTime64(3, 'UTC'),
    is_deleted UInt8 DEFAULT 0
)
ENGINE = ReplacingMergeTree(source_rank, is_deleted)
ORDER BY (normalized_name, serial_number, correspondent_key)
SETTINGS storage_policy = '{TARGET_STORAGE_POLICY}'
""".strip()


def target_mv_ddl() -> str:
    return f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS markorbit_facts.{MV_NAME}
TO {US_ATTORNEY_NAME_LOOKUP_TABLE}
AS SELECT
    {SOURCE_NORMALIZED} AS normalized_name,
    serial_number, correspondent_key, attorney_name, attorney_docket_number,
    source_package_kind, source_effective_date, source_file, source_row_hash,
    last_source_package_id AS source_package_id, record_hash, source_rank,
    ingested_at, is_deleted
FROM {SOURCE_TABLE}
WHERE attorney_name != ''
""".strip()


def prepare_plan(
    output_path: Path,
    *,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = exact_main_sha,
) -> dict[str, object]:
    target = client or accepted_us_target_read_client()
    main_sha = main_sha_getter()
    epoch = epoch_getter()
    source = source_stats(target)
    lookup = lookup_stats(target)
    marker = ready_marker(target)
    if lookup["exists"] or marker is not None:
        raise RuntimeError(
            "attorney production gate prepare requires absent lookup and READY marker"
        )
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha,
        "implementation_sha": main_sha,
        "source_epoch": _epoch_dict(epoch),
        "source_stats": source.to_dict(),
        "target_precondition": {
            "lookup": lookup,
            "ready_marker": marker,
        },
        "capacity_contract": _capacity_contract(target),
        "target_schema": {
            "storage_policy": TARGET_STORAGE_POLICY,
            "table_ddl_sha256": hashlib.sha256(
                target_table_ddl().encode("utf-8")
            ).hexdigest(),
            "mv_ddl_sha256": hashlib.sha256(
                target_mv_ddl().encode("utf-8")
            ).hexdigest(),
        },
        "acceptance": {
            "benchmark_runs": BENCHMARK_RUNS,
            "entity_name_resolve_p95_ms": SLO_P95_MS,
            "ready_version": ATTORNEY_NAME_LOOKUP_READY_VERSION,
        },
        "mutation_scope": {
            "create_table": US_ATTORNEY_NAME_LOOKUP_TABLE,
            "create_mv": f"markorbit_facts.{MV_NAME}",
            "backfill_source": SOURCE_TABLE,
            "final_marker_component": READY_COMPONENT,
            "operation": "CREATE_INSERT_ONLY",
        },
    }

    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return envelope


def _validate_plan_contract(plan: Mapping[str, Any]) -> None:
    if set(plan) != _PLAN_KEYS:
        raise RuntimeError("attorney production-gate plan keys drifted")
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("unsupported attorney production-gate plan version")
    acceptance = dict(plan.get("acceptance") or {})
    if acceptance != {
        "benchmark_runs": BENCHMARK_RUNS,
        "entity_name_resolve_p95_ms": SLO_P95_MS,
        "ready_version": ATTORNEY_NAME_LOOKUP_READY_VERSION,
    }:
        raise RuntimeError("attorney production-gate acceptance contract drifted")
    mutation_scope = dict(plan.get("mutation_scope") or {})
    if mutation_scope != {
        "create_table": US_ATTORNEY_NAME_LOOKUP_TABLE,
        "create_mv": f"markorbit_facts.{MV_NAME}",
        "backfill_source": SOURCE_TABLE,
        "final_marker_component": READY_COMPONENT,
        "operation": "CREATE_INSERT_ONLY",
    }:
        raise RuntimeError("attorney production-gate mutation scope drifted")


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    expected = expected_sha.strip().lower()
    if len(expected) != 64 or any(ch not in _HEX40 for ch in expected):
        raise ValueError("plan SHA must be exact 64-character lowercase hex")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"plan", "plan_sha256"}:
        raise RuntimeError("malformed attorney production-gate plan envelope")
    plan = dict(envelope["plan"])
    actual = _sha256(plan)
    if envelope["plan_sha256"] != actual or actual != expected:
        raise RuntimeError("attorney production-gate plan SHA mismatch")
    _validate_plan_contract(plan)
    return plan


def _assert_plan_live(
    plan: Mapping[str, Any],
    *,
    client: Any,
    epoch_getter: Callable[[], USApplicantServingEpoch],
    main_sha_getter: Callable[[], str],
) -> SourceStats:
    main_sha = main_sha_getter()
    if main_sha != str(plan.get("expected_main") or ""):
        raise RuntimeError("attorney production-gate main SHA drifted")
    if main_sha != str(plan.get("implementation_sha") or ""):
        raise RuntimeError("attorney production-gate implementation SHA drifted")
    epoch = epoch_getter()
    if _epoch_dict(epoch) != dict(plan.get("source_epoch") or {}):
        raise RuntimeError("accepted US serving epoch drifted")
    current_source = source_stats(client)
    if current_source.to_dict() != dict(plan.get("source_stats") or {}):
        raise RuntimeError("accepted current correspondent truth drifted")
    frozen_capacity = dict(plan.get("capacity_contract") or {})
    live_capacity = _capacity_contract(client)
    if (
        live_capacity["hot_us"]["total_bytes"]
        != frozen_capacity["hot_us"]["total_bytes"]
    ):
        raise RuntimeError("hot_us total capacity drifted")
    if live_capacity["projected_free_bytes"] < int(
        int(live_capacity["hot_us"]["total_bytes"]) * MIN_FREE_RATIO_AFTER_ESTIMATE
    ):
        raise RuntimeError("hot_us live free-space reserve fell below the 30% floor")
    schema = dict(plan.get("target_schema") or {})
    if schema.get("storage_policy") != TARGET_STORAGE_POLICY:
        raise RuntimeError("attorney target storage policy plan drifted")
    if schema.get("table_ddl_sha256") != hashlib.sha256(
        target_table_ddl().encode("utf-8")
    ).hexdigest():
        raise RuntimeError("attorney target table DDL hash drifted")
    if schema.get("mv_ddl_sha256") != hashlib.sha256(
        target_mv_ddl().encode("utf-8")
    ).hexdigest():
        raise RuntimeError("attorney target MV DDL hash drifted")
    return current_source


def _sql_text(value: object) -> str:
    text = str(value)
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sample_mismatches(client: Any, sample_limit: int = 200) -> dict[str, int]:
    sample = _rows(
        client,
        f"""
        SELECT normalized_name, serial_number, correspondent_key, record_hash, source_rank
        FROM {US_ATTORNEY_NAME_LOOKUP_TABLE} FINAL
        WHERE is_deleted=0
        ORDER BY normalized_name, serial_number, correspondent_key
        LIMIT {int(sample_limit)}
        """,
    )
    if not sample:
        return {"checked": 0, "mismatches": 0}
    keys = ", ".join(
        f"({_sql_text(row[1])}, {_sql_text(row[2])})" for row in sample
    )
    source_rows = _rows(
        client,
        f"""
        SELECT serial_number, correspondent_key, {SOURCE_NORMALIZED} AS normalized_name,
               record_hash, source_rank
        FROM {SOURCE_TABLE} FINAL
        WHERE is_deleted=0 AND attorney_name!=''
          AND (serial_number, correspondent_key) IN ({keys})
        """,
    )
    source_map = {
        (str(row[0]), str(row[1])): (
            str(row[2]), str(row[3]), int(row[4])
        )
        for row in source_rows
    }
    mismatches = 0
    for row in sample:
        observed = source_map.get((str(row[1]), str(row[2])))
        expected = (str(row[0]), str(row[3]), int(row[4]))
        if observed != expected:
            mismatches += 1
    return {"checked": len(sample), "mismatches": mismatches}


def verify_completeness(client: Any, expected_source: SourceStats) -> dict[str, object]:
    live_source = source_stats(client)
    lookup = lookup_stats(client)
    sample = _sample_mismatches(client)
    complete = (
        live_source == expected_source
        and lookup["exists"] is True
        and int(lookup["visible_rows"]) == expected_source.qualifying_rows
        and str(lookup["binding_sum"]) == expected_source.binding_sum
        and str(lookup["binding_xor"]) == expected_source.binding_xor
        and int(sample["mismatches"]) == 0
    )
    return {
        "complete": complete,
        "source": live_source.to_dict(),
        "lookup": lookup,
        "sample": sample,
        "count_match": int(lookup["visible_rows"]) == expected_source.qualifying_rows,
        "binding_sum_match": str(lookup["binding_sum"]) == expected_source.binding_sum,
        "binding_xor_match": str(lookup["binding_xor"]) == expected_source.binding_xor,
    }


def _backfill_sql() -> str:
    return f"""
INSERT INTO {US_ATTORNEY_NAME_LOOKUP_TABLE}
(
    normalized_name, serial_number, correspondent_key, attorney_name,
    attorney_docket_number, source_package_kind, source_effective_date,
    source_file, source_row_hash, source_package_id, record_hash,
    source_rank, ingested_at, is_deleted
)
SELECT
    {SOURCE_NORMALIZED} AS normalized_name,
    serial_number, correspondent_key, attorney_name, attorney_docket_number,
    source_package_kind, source_effective_date, source_file, source_row_hash,
    last_source_package_id, record_hash, source_rank, ingested_at, is_deleted
FROM {SOURCE_TABLE} FINAL
WHERE is_deleted=0 AND attorney_name!=''
""".strip()


def _pick_benchmark_name(client: Any) -> str:
    rows = _rows(
        client,
        f"""
        SELECT normalized_name, count() AS n
        FROM {US_ATTORNEY_NAME_LOOKUP_TABLE} FINAL
        WHERE is_deleted=0
        GROUP BY normalized_name
        HAVING n BETWEEN 1 AND 50
        ORDER BY normalized_name
        LIMIT 1
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("could not select bounded attorney benchmark name")
    return str(rows[0][0])


def _benchmark_once(client: Any, normalized_name: str) -> tuple[float, int]:
    started = time.perf_counter()
    candidates = _rows(
        client,
        f"""
        SELECT serial_number, correspondent_key
        FROM {US_ATTORNEY_NAME_LOOKUP_TABLE} FINAL
        WHERE normalized_name={_sql_text(normalized_name)}
          AND is_deleted=0
        ORDER BY normalized_name, serial_number, correspondent_key
        LIMIT 501
        """,
    )
    if not candidates:
        raise RuntimeError("attorney benchmark candidate unexpectedly disappeared")
    if len(candidates) > 500:
        raise RuntimeError("attorney benchmark name exceeds bounded candidate ceiling")
    serials = ", ".join(_sql_text(row[0]) for row in candidates)
    matches = _rows(
        client,
        f"""
        SELECT serial_number, correspondent_key
        FROM {SOURCE_TABLE} FINAL
        WHERE serial_number IN ({serials})
          AND {SOURCE_NORMALIZED}={_sql_text(normalized_name)}
          AND is_deleted=0
        ORDER BY serial_number, correspondent_key
        LIMIT 500
        """,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, len(matches)


def benchmark_lookup(client: Any) -> dict[str, object]:
    normalized_name = _pick_benchmark_name(client)
    elapsed: list[float] = []
    counts: list[int] = []
    for _ in range(BENCHMARK_RUNS):
        ms, count = _benchmark_once(client, normalized_name)
        elapsed.append(ms)
        counts.append(count)
    ordered = sorted(elapsed)
    index = max(0, min(len(ordered) - 1, int((0.95 * len(ordered)) + 0.999999) - 1))
    p95 = ordered[index]
    passed = p95 <= SLO_P95_MS and min(counts) > 0 and len(set(counts)) == 1
    return {
        "normalized_name": normalized_name,
        "runs": BENCHMARK_RUNS,
        "elapsed_ms": [round(value, 3) for value in elapsed],
        "p50_ms": round(ordered[len(ordered) // 2], 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(elapsed), 3),
        "match_count": counts[0],
        "stable_match_count": len(set(counts)) == 1,
        "slo_p95_ms": SLO_P95_MS,
        "passed": passed,
    }


def authority_token(plan_sha: str) -> str:
    return f"GO #767 US-ATTORNEY-LOOKUP {plan_sha} ACTIVATE"


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _assert_source_unchanged(
    plan: Mapping[str, Any],
    *,
    client: Any,
    epoch_getter: Callable[[], USApplicantServingEpoch],
    main_sha_getter: Callable[[], str],
) -> SourceStats:
    if main_sha_getter() != str(plan["expected_main"]):
        raise RuntimeError("attorney production-gate main SHA drifted")
    if _epoch_dict(epoch_getter()) != dict(plan["source_epoch"]):
        raise RuntimeError("accepted US serving epoch drifted")
    observed = source_stats(client)
    expected = SourceStats(**dict(plan["source_stats"]))
    if observed != expected:
        raise RuntimeError("accepted current correspondent truth drifted")
    return observed


def _marker_insert_sql() -> str:
    return (
        "INSERT INTO markorbit_facts.schema_version (component, version) "
        f"VALUES ('{READY_COMPONENT}', '{ATTORNEY_NAME_LOOKUP_READY_VERSION}')"
    )


def _mv_exists(client: Any) -> bool:
    rows = _rows(
        client,
        f"""
        SELECT engine, create_table_query
        FROM system.tables
        WHERE database='{TARGET_DATABASE}' AND name='{MV_NAME}'
        """,
    )
    if not rows:
        return False
    if len(rows) != 1:
        raise RuntimeError("attorney lookup materialized view metadata is not exact-one")
    engine, create_query = str(rows[0][0]), str(rows[0][1])
    if engine != "MaterializedView":
        raise RuntimeError("attorney lookup materialized view engine drifted")
    normalized = " ".join(create_query.lower().replace(chr(96), "").split())
    required = [
        f"to {US_ATTORNEY_NAME_LOOKUP_TABLE}".lower(),
        f"from {SOURCE_TABLE}".lower(),
        "where attorney_name != ''",
    ]
    if any(token not in normalized for token in required):
        raise RuntimeError("attorney lookup materialized view contract drifted")
    return True


def execute_plan(
    plan_path: Path,
    *,
    plan_sha: str,
    authority: str,
    receipt_path: Path,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = exact_main_sha,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    if authority != authority_token(plan_sha):
        raise PermissionError("exact #767 authority token is required")
    target = client or MutableTargetClient()
    stage = "PRECHECK"
    try:
        source = _assert_plan_live(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )
        marker = ready_marker(target)
        lookup = lookup_stats(target)
        if marker == ATTORNEY_NAME_LOOKUP_READY_VERSION:
            completeness = verify_completeness(target, source)
            if completeness["complete"] is not True:
                raise RuntimeError("READY attorney lookup is not complete")
            receipt = {
                "version": RECEIPT_VERSION,
                "status": "SUCCESS",
                "replayed": True,
                "plan_sha256": plan_sha,
                "implementation_sha": str(plan["implementation_sha"]),
                "source_epoch": dict(plan["source_epoch"]),
                "completeness": completeness,
            }
            _write_receipt(receipt_path, receipt)
            return receipt
        if marker is not None:
            raise RuntimeError(f"unexpected attorney lookup marker before READY: {marker}")

        stage = "SCHEMA"
        if lookup["exists"] is not True:
            target.command(target_table_ddl())
            lookup = lookup_stats(target)
        if not _mv_exists(target):
            target.command(target_mv_ddl())
        _assert_source_unchanged(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )

        visible = int(lookup["visible_rows"])
        if visible not in {0, source.qualifying_rows}:
            raise RuntimeError(
                "attorney lookup is partially populated; bounded automatic resume refused"
            )

        if visible == 0:
            stage = "BACKFILL"
            target.command(_backfill_sql())

        stage = "COMPLETENESS"
        _assert_source_unchanged(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )
        completeness = verify_completeness(target, source)
        if completeness["complete"] is not True:
            raise RuntimeError("attorney lookup completeness verification failed")

        stage = "BENCHMARK"
        benchmark = benchmark_lookup(target)
        _assert_source_unchanged(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )
        if benchmark["passed"] is not True:
            raise RuntimeError("attorney lookup production benchmark failed")

        stage = "READY"
        target.command(_marker_insert_sql())
        if ready_marker(target) != ATTORNEY_NAME_LOOKUP_READY_VERSION:
            raise RuntimeError("attorney lookup READY marker did not become visible")

        stage = "RUNTIME_SMOKE"
        smoke = attorneys_by_name(
            accepted_us_target_read_client(),
            str(benchmark["normalized_name"]),
        )
        if int(smoke.get("match_count") or 0) < 1:
            raise RuntimeError("attorney lookup runtime smoke returned no current fact")

        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "replayed": False,
            "plan_sha256": plan_sha,
            "implementation_sha": str(plan["implementation_sha"]),
            "source_epoch": dict(plan["source_epoch"]),
            "source_stats": source.to_dict(),
            "completeness": completeness,
            "benchmark": benchmark,
            "runtime_smoke": {
                "normalized_name": smoke["normalized_name"],
                "candidate_count": smoke["candidate_count"],
                "match_count": smoke["match_count"],
                "semantics": smoke["semantics"],
            },
            "ready_marker": ATTORNEY_NAME_LOOKUP_READY_VERSION,
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
        description="Governed US attorney-name lookup production gate"
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
            "production_us_attorney_name_lookup_plan"
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
        "production_us_attorney_name_lookup_receipt"
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

