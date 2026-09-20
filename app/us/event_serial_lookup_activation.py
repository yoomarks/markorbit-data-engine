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
from app.us.event_serial_lookup import (
    EVENT_SERIAL_LOOKUP_READY_VERSION,
    US_EVENT_SERIAL_LOOKUP_TABLE,
    events_for_serial,
)
from app.us.target_canary import (
    TARGET_DATABASE,
    TARGET_STORAGE_POLICY,
    WslNativeClickHouseClient,
)

PLAN_VERSION = "US_EVENT_SERIAL_LOOKUP_PRODUCTION_GATE_PLAN_V1"
RECEIPT_VERSION = "US_EVENT_SERIAL_LOOKUP_PRODUCTION_GATE_RECEIPT_V1"
SOURCE_TABLE = "markorbit_facts.us_event_history"
READY_COMPONENT = "US_EVENT_SERIAL_LOOKUP"
BENCHMARK_RUNS = 7
SLO_P95_MS = 300.0
MIN_FREE_RATIO_AFTER_ESTIMATE = 0.30
EXPECTED_COLUMNS = [
    ("event_key", "FixedString(64)"),
    ("serial_number", "String"),
    ("event_code", "String"),
    ("event_date", "Nullable(Date32)"),
    ("event_sequence", "UInt32"),
    ("event_type_code", "String"),
    ("description_text", "String"),
    ("source_package_kind", "LowCardinality(String)"),
    ("source_effective_date", "Nullable(Date32)"),
    ("source_file", "String"),
    ("source_row_hash", "FixedString(64)"),
    ("source_package_id", "UUID"),
    ("source_rank", "UInt64"),
    ("observed_at", "DateTime64(3, 'UTC')"),
]
SERIAL_VALID = "length(serial_number)=8 AND match(serial_number, '^[0-9]{8}$')"
SOURCE_BINDING_HASH = (
    "cityHash64(concat(event_key,'\\x1f',serial_number,'\\x1f',"
    "source_row_hash,'\\x1f',toString(source_package_id),'\\x1f',"
    "toString(source_rank)))"
)
LOOKUP_BINDING_HASH = SOURCE_BINDING_HASH

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


@dataclass(frozen=True, slots=True)
class BatchStats:
    prefix: str
    rows: int
    max_source_rank: int
    binding_sum: str
    binding_xor: str

    def to_dict(self) -> dict[str, object]:
        return {
            "prefix": self.prefix,
            "rows": self.rows,
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
    if head != origin or dirty:
        raise RuntimeError("event serial production gate requires clean exact main")
    return head

class MutableTargetClient:
    def __init__(self, base: WslNativeClickHouseClient | None = None) -> None:
        self._base = base or WslNativeClickHouseClient()

    def query(self, sql: str, *, settings: Mapping[str, Any] | None = None) -> Any:
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
        SELECT count(),
               countIf({SERIAL_VALID}),
               maxIf(source_rank,{SERIAL_VALID}),
               toString(sumIf({SOURCE_BINDING_HASH},{SERIAL_VALID})),
               toString(groupBitXorIf({SOURCE_BINDING_HASH},{SERIAL_VALID}))
        FROM {SOURCE_TABLE} FINAL
        """,
    )[0]
    return SourceStats(
        active_rows=int(row[0]),
        qualifying_rows=int(row[1]),
        max_source_rank=int(row[2]),
        binding_sum=str(row[3]),
        binding_xor=str(row[4]),
    )


def source_batches(client: Any) -> list[BatchStats]:
    rows = _rows(
        client,
        f"""
        SELECT left(serial_number,2) AS prefix,
               count(),
               max(source_rank),
               toString(sum({SOURCE_BINDING_HASH})),
               toString(groupBitXor({SOURCE_BINDING_HASH}))
        FROM {SOURCE_TABLE} FINAL
        WHERE {SERIAL_VALID}
        GROUP BY prefix
        ORDER BY prefix
        """,
    )
    observed = {
        str(row[0]): BatchStats(
            prefix=str(row[0]),
            rows=int(row[1]),
            max_source_rank=int(row[2]),
            binding_sum=str(row[3]),
            binding_xor=str(row[4]),
        )
        for row in rows
    }
    expected = [f"{value:02d}" for value in range(100)]
    unknown = sorted(set(observed) - set(expected))
    if unknown:
        raise RuntimeError(f"unexpected serial prefixes: {unknown}")
    return [
        observed.get(prefix, BatchStats(prefix, 0, 0, "0", "0"))
        for prefix in expected
    ]

def _table_metadata(client: Any) -> list[list[Any]]:
    return _rows(
        client,
        f"""
        SELECT engine,sorting_key,storage_policy
        FROM system.tables
        WHERE database='{TARGET_DATABASE}'
          AND name='us_event_serial_history'
        """,
    )


def _validate_lookup_schema(client: Any) -> bool:
    meta = _table_metadata(client)
    if not meta:
        return False
    if len(meta) != 1:
        raise RuntimeError("event serial lookup table metadata is not exact-one")
    engine, sorting_key, policy = (str(value) for value in meta[0])
    normalized_sort = sorting_key.replace(chr(96), "").replace("(", "").replace(")", "").strip()
    if engine != "ReplacingMergeTree":
        raise RuntimeError("event serial lookup engine drifted")
    if normalized_sort != "serial_number, event_key":
        raise RuntimeError("event serial lookup sorting key drifted")
    if policy != TARGET_STORAGE_POLICY:
        raise RuntimeError("event serial lookup storage policy drifted")
    columns = _rows(
        client,
        f"""
        SELECT name,type
        FROM system.columns
        WHERE database='{TARGET_DATABASE}'
          AND table='us_event_serial_history'
        ORDER BY position
        """,
    )
    observed = [(str(row[0]), str(row[1])) for row in columns]
    if observed != EXPECTED_COLUMNS:
        raise RuntimeError("event serial lookup column contract drifted")
    return True


def lookup_stats(client: Any) -> dict[str, object]:
    if not _validate_lookup_schema(client):
        return {
            "exists": False,
            "visible_rows": 0,
            "distinct_serials": 0,
            "max_source_rank": 0,
            "binding_sum": "0",
            "binding_xor": "0",
        }
    row = _rows(
        client,
        f"""
        SELECT count(),uniqExact(serial_number),max(source_rank),
               toString(sum({LOOKUP_BINDING_HASH})),
               toString(groupBitXor({LOOKUP_BINDING_HASH}))
        FROM {US_EVENT_SERIAL_LOOKUP_TABLE} FINAL
        """,
    )[0]
    return {
        "exists": True,
        "visible_rows": int(row[0]),
        "distinct_serials": int(row[1]),
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
    return None if not rows else str(rows[0][0])

def target_table_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {US_EVENT_SERIAL_LOOKUP_TABLE}
(
    event_key FixedString(64),
    serial_number String,
    event_code String,
    event_date Nullable(Date32),
    event_sequence UInt32,
    event_type_code String,
    description_text String,
    source_package_kind LowCardinality(String),
    source_effective_date Nullable(Date32),
    source_file String,
    source_row_hash FixedString(64),
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (serial_number, event_key)
SETTINGS storage_policy = '{TARGET_STORAGE_POLICY}'
""".strip()


def _capacity_contract(client: Any) -> dict[str, object]:
    disk = _rows(
        client,
        "SELECT free_space,total_space FROM system.disks WHERE name='hot_us'",
    )[0]
    table = _rows(
        client,
        f"""
        SELECT total_bytes,storage_policy
        FROM system.tables
        WHERE database='{TARGET_DATABASE}' AND name='us_event_history'
        """,
    )[0]
    if str(table[1]) != TARGET_STORAGE_POLICY:
        raise RuntimeError("accepted us_event_history storage policy drifted")
    source_bytes = int(table[0])
    estimated = max((source_bytes * 3) // 2, 1)
    projected = int(disk[0]) - estimated
    total = int(disk[1])
    if projected < int(total * MIN_FREE_RATIO_AFTER_ESTIMATE):
        raise RuntimeError("hot_us reserve would fall below 30%")
    return {
        "hot_us_free_bytes": int(disk[0]),
        "hot_us_total_bytes": total,
        "source_table_bytes": source_bytes,
        "estimated_lookup_bytes_ceiling": estimated,
        "projected_free_bytes": projected,
        "minimum_free_ratio_after_estimate": MIN_FREE_RATIO_AFTER_ESTIMATE,
    }


def _live_free(client: Any) -> tuple[int, int]:
    row = _rows(
        client,
        "SELECT free_space,total_space FROM system.disks WHERE name='hot_us'",
    )[0]
    return int(row[0]), int(row[1])


def _assert_remaining_capacity(
    client: Any,
    *,
    plan_capacity: Mapping[str, object],
    remaining_rows: int,
    total_rows: int,
) -> None:
    free_bytes, total_bytes = _live_free(client)
    if total_bytes != int(plan_capacity["hot_us_total_bytes"]):
        raise RuntimeError("hot_us total capacity drifted")
    ceiling = int(plan_capacity["estimated_lookup_bytes_ceiling"])
    remaining_estimate = 0
    if total_rows > 0:
        remaining_estimate = (ceiling * max(remaining_rows, 0)) // total_rows
    if free_bytes - remaining_estimate < int(total_bytes * MIN_FREE_RATIO_AFTER_ESTIMATE):
        raise RuntimeError("hot_us projected reserve fell below 30% during event backfill")


def prepare_plan(
    output: Path,
    *,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = exact_main_sha,
) -> dict[str, object]:
    target = client or accepted_us_target_read_client()
    main_sha = main_sha_getter()
    stats = source_stats(target)
    if stats.active_rows != stats.qualifying_rows:
        raise RuntimeError("accepted event source contains non-8-digit serial numbers")
    batches = source_batches(target)
    if sum(batch.rows for batch in batches) != stats.qualifying_rows:
        raise RuntimeError("event prefix batch counts do not cover frozen source truth")
    lookup = lookup_stats(target)
    marker = ready_marker(target)
    if lookup["exists"] or marker is not None:
        raise RuntimeError("event gate prepare requires absent lookup and READY marker")
    capacity = _capacity_contract(target)
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha,
        "implementation_sha": main_sha,
        "source_epoch": epoch_getter().to_dict(),
        "source_stats": stats.to_dict(),
        "source_batches": [batch.to_dict() for batch in batches],
        "target_precondition": {"lookup": lookup, "ready_marker": marker},
        "capacity_contract": capacity,
        "target_schema": {
            "storage_policy": TARGET_STORAGE_POLICY,
            "table_ddl_sha256": hashlib.sha256(
                target_table_ddl().encode("utf-8")
            ).hexdigest(),
        },
        "acceptance": {
            "benchmark_runs": BENCHMARK_RUNS,
            "serial_event_p95_ms": SLO_P95_MS,
            "ready_version": EVENT_SERIAL_LOOKUP_READY_VERSION,
        },
        "mutation_scope": {
            "create_table": US_EVENT_SERIAL_LOOKUP_TABLE,
            "backfill_source": SOURCE_TABLE,
            "batch_key": "left(serial_number,2)",
            "batch_count": 100,
            "final_marker_component": READY_COMPONENT,
            "operation": "CREATE_INSERT_BATCHED_PREFIX_ONLY",
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return envelope


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"plan", "plan_sha256"}:
        raise RuntimeError("malformed event serial production-gate plan")
    plan = dict(envelope["plan"])
    actual = _sha256(plan)
    if actual != expected_sha.lower() or str(envelope["plan_sha256"]) != actual:
        raise RuntimeError("event serial production-gate plan SHA mismatch")
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("unsupported event serial production-gate plan version")
    expected_scope = {
        "create_table": US_EVENT_SERIAL_LOOKUP_TABLE,
        "backfill_source": SOURCE_TABLE,
        "batch_key": "left(serial_number,2)",
        "batch_count": 100,
        "final_marker_component": READY_COMPONENT,
        "operation": "CREATE_INSERT_BATCHED_PREFIX_ONLY",
    }
    if dict(plan.get("mutation_scope") or {}) != expected_scope:
        raise RuntimeError("event serial production-gate mutation scope drifted")
    batches = list(plan.get("source_batches") or [])
    if len(batches) != 100:
        raise RuntimeError("event serial production-gate requires exactly 100 prefix batches")
    if [str(item["prefix"]) for item in batches] != [f"{i:02d}" for i in range(100)]:
        raise RuntimeError("event serial production-gate prefix ordering drifted")
    return plan
def _frozen_stats(plan: Mapping[str, Any]) -> SourceStats:
    return SourceStats(**dict(plan["source_stats"]))


def _frozen_batches(plan: Mapping[str, Any]) -> list[BatchStats]:
    return [BatchStats(**dict(item)) for item in list(plan["source_batches"])]


def _assert_plan_live(
    plan: Mapping[str, Any],
    *,
    client: Any,
    epoch_getter: Callable[[], USApplicantServingEpoch],
    main_sha_getter: Callable[[], str],
    recheck_source: bool,
) -> SourceStats:
    main_sha = main_sha_getter()
    if main_sha != str(plan["expected_main"]) or main_sha != str(
        plan["implementation_sha"]
    ):
        raise RuntimeError("event serial production-gate main SHA drifted")
    if epoch_getter().to_dict() != dict(plan["source_epoch"]):
        raise RuntimeError("accepted US serving epoch drifted")
    frozen = _frozen_stats(plan)
    if recheck_source and source_stats(client) != frozen:
        raise RuntimeError("accepted event source truth drifted")
    free_bytes, total_bytes = _live_free(client)
    capacity = dict(plan["capacity_contract"])
    if total_bytes != int(capacity["hot_us_total_bytes"]):
        raise RuntimeError("hot_us total capacity drifted")
    if free_bytes < int(total_bytes * MIN_FREE_RATIO_AFTER_ESTIMATE):
        raise RuntimeError("hot_us live free space fell below 30%")
    schema = dict(plan["target_schema"])
    if schema["storage_policy"] != TARGET_STORAGE_POLICY:
        raise RuntimeError("event target storage policy drifted")
    if schema["table_ddl_sha256"] != hashlib.sha256(
        target_table_ddl().encode("utf-8")
    ).hexdigest():
        raise RuntimeError("event target DDL hash drifted")
    return frozen


def _sql(value: object) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
def _batch_stats(client: Any, prefix: str) -> BatchStats:
    if len(prefix) != 2 or not prefix.isdigit():
        raise ValueError("event batch prefix must be two digits")
    row = _rows(
        client,
        f"""
        SELECT count(),
               ifNull(max(source_rank),0),
               toString(sum({LOOKUP_BINDING_HASH})),
               toString(groupBitXor({LOOKUP_BINDING_HASH}))
        FROM {US_EVENT_SERIAL_LOOKUP_TABLE} FINAL
        WHERE startsWith(serial_number,{_sql(prefix)})
        """,
    )[0]
    return BatchStats(
        prefix=prefix,
        rows=int(row[0]),
        max_source_rank=int(row[1]),
        binding_sum=str(row[2]),
        binding_xor=str(row[3]),
    )


def _batch_matches(observed: BatchStats, expected: BatchStats) -> bool:
    return observed == expected
def _sample_mismatches(client: Any, limit: int = 200) -> dict[str, int]:
    sample = _rows(
        client,
        f"""
        SELECT event_key,serial_number,source_row_hash,
               toString(source_package_id),source_rank
        FROM {US_EVENT_SERIAL_LOOKUP_TABLE} FINAL
        ORDER BY serial_number,event_key
        LIMIT {int(limit)}
        """,
    )
    if not sample:
        return {"checked": 0, "mismatches": 0}
    keys = ", ".join(_sql(row[0]) for row in sample)
    source = _rows(
        client,
        f"""
        SELECT event_key,serial_number,source_row_hash,
               toString(source_package_id),source_rank
        FROM {SOURCE_TABLE} FINAL
        WHERE event_key IN ({keys})
        """,
    )
    current = {str(row[0]): tuple(str(v) for v in row) for row in source}
    mismatches = sum(
        1 for row in sample
        if current.get(str(row[0])) != tuple(str(v) for v in row)
    )
    return {"checked": len(sample), "mismatches": mismatches}
def verify_completeness(
    client: Any,
    expected: SourceStats,
) -> dict[str, object]:
    source = source_stats(client)
    lookup = lookup_stats(client)
    sample = _sample_mismatches(client)
    complete = (
        source == expected
        and lookup["exists"] is True
        and int(lookup["visible_rows"]) == expected.qualifying_rows
        and str(lookup["binding_sum"]) == expected.binding_sum
        and str(lookup["binding_xor"]) == expected.binding_xor
        and sample["mismatches"] == 0
    )
    return {
        "complete": complete,
        "source": source.to_dict(),
        "lookup": lookup,
        "sample": sample,
        "count_match": int(lookup["visible_rows"]) == expected.qualifying_rows,
        "binding_sum_match": str(lookup["binding_sum"]) == expected.binding_sum,
        "binding_xor_match": str(lookup["binding_xor"]) == expected.binding_xor,
    }
def _backfill_sql(prefix: str) -> str:
    if len(prefix) != 2 or not prefix.isdigit():
        raise ValueError("event batch prefix must be two digits")
    return f"""
INSERT INTO {US_EVENT_SERIAL_LOOKUP_TABLE}
(
    event_key,serial_number,event_code,event_date,event_sequence,event_type_code,
    description_text,source_package_kind,source_effective_date,source_file,
    source_row_hash,source_package_id,source_rank,observed_at
)
SELECT
    event_key,serial_number,event_code,event_date,event_sequence,event_type_code,
    description_text,source_package_kind,source_effective_date,source_file,
    source_row_hash,source_package_id,source_rank,observed_at
FROM {SOURCE_TABLE} FINAL
WHERE startsWith(serial_number,{_sql(prefix)})
""".strip()


def _pick_benchmark_serial(client: Any) -> str:
    rows = _rows(
        client,
        f"""
        SELECT serial_number,count() AS n
        FROM {US_EVENT_SERIAL_LOOKUP_TABLE} FINAL
        GROUP BY serial_number
        HAVING n BETWEEN 1 AND 100
        ORDER BY serial_number
        LIMIT 1
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("could not select bounded event serial benchmark")
    return str(rows[0][0])
def _benchmark_once(client: Any, serial: str) -> tuple[float, int]:
    started = time.perf_counter()
    rows = _rows(
        client,
        f"""
        SELECT event_key,event_code,event_date,event_sequence,event_type_code,
               description_text,source_package_kind,source_effective_date,
               source_file,source_row_hash,toString(source_package_id),
               source_rank,observed_at
        FROM {US_EVENT_SERIAL_LOOKUP_TABLE} FINAL
        WHERE serial_number={_sql(serial)}
        ORDER BY event_date,event_sequence,event_code,event_key
        LIMIT 501
        """,
    )
    if not rows or len(rows) > 500:
        raise RuntimeError("event serial benchmark result bound failed")
    return (time.perf_counter() - started) * 1000.0, len(rows)


def benchmark_lookup(client: Any) -> dict[str, object]:
    serial = _pick_benchmark_serial(client)
    elapsed: list[float] = []
    counts: list[int] = []
    for _ in range(BENCHMARK_RUNS):
        ms, count = _benchmark_once(client, serial)
        elapsed.append(ms)
        counts.append(count)
    ordered = sorted(elapsed)
    p95 = ordered[-1]
    return {
        "serial_number": serial,
        "runs": BENCHMARK_RUNS,
        "elapsed_ms": [round(value, 3) for value in elapsed],
        "p50_ms": round(ordered[len(ordered) // 2], 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(elapsed), 3),
        "event_count": counts[0],
        "stable_event_count": len(set(counts)) == 1,
        "slo_p95_ms": SLO_P95_MS,
        "passed": p95 <= SLO_P95_MS and min(counts) > 0 and len(set(counts)) == 1,
    }


def authority_token(plan_sha: str) -> str:
    return f"GO #766 US-EVENT-SERIAL-LOOKUP {plan_sha} ACTIVATE"


def _marker_sql() -> str:
    return (
        "INSERT INTO markorbit_facts.schema_version (component,version) "
        f"VALUES ('{READY_COMPONENT}','{EVENT_SERIAL_LOOKUP_READY_VERSION}')"
    )


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


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
        raise PermissionError("exact #766 authority token is required")
    target = client or MutableTargetClient()
    stage = "PRECHECK"
    batch_summary = {
        "inserted_prefixes": [],
        "skipped_complete_prefixes": [],
        "empty_prefixes": [],
    }
    try:
        source = _assert_plan_live(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
            recheck_source=True,
        )
        marker = ready_marker(target)
        if marker == EVENT_SERIAL_LOOKUP_READY_VERSION:
            completeness = verify_completeness(target, source)
            benchmark = benchmark_lookup(target)
            smoke = events_for_serial(
                accepted_us_target_read_client(),
                str(benchmark["serial_number"]),
                limit=500,
            )
            if (
                completeness["complete"] is not True
                or benchmark["passed"] is not True
                or int(smoke.get("event_count") or 0) < 1
            ):
                raise RuntimeError("READY event serial lookup failed replay verification")
            receipt = {
                "version": RECEIPT_VERSION,
                "status": "SUCCESS",
                "replayed": True,
                "plan_sha256": plan_sha,
                "implementation_sha": plan["implementation_sha"],
                "completeness": completeness,
                "benchmark": benchmark,
                "runtime_smoke": {
                    "serial_number": smoke["serial_number"],
                    "event_count": smoke["event_count"],
                    "semantics": smoke["semantics"],
                },
                "ready_marker": marker,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_receipt(receipt_path, receipt)
            return receipt
        if marker is not None:
            raise RuntimeError(f"unexpected event serial lookup marker: {marker}")

        if not _validate_lookup_schema(target):
            stage = "SCHEMA"
            target.command(target_table_ddl())
            if not _validate_lookup_schema(target):
                raise RuntimeError("event serial lookup schema did not become visible")

        batches = _frozen_batches(plan)
        completed_rows = 0
        for expected in batches:
            stage = f"BATCH_{expected.prefix}"
            _assert_plan_live(
                plan,
                client=target,
                epoch_getter=epoch_getter,
                main_sha_getter=main_sha_getter,
                recheck_source=False,
            )
            remaining_rows = source.qualifying_rows - completed_rows
            _assert_remaining_capacity(
                target,
                plan_capacity=dict(plan["capacity_contract"]),
                remaining_rows=remaining_rows,
                total_rows=source.qualifying_rows,
            )
            observed = _batch_stats(target, expected.prefix)
            if expected.rows == 0:
                if not _batch_matches(observed, expected):
                    raise RuntimeError(
                        f"event batch {expected.prefix} should be empty but is not"
                    )
                batch_summary["empty_prefixes"].append(expected.prefix)
            elif observed.rows == 0:
                target.command(_backfill_sql(expected.prefix))
                observed = _batch_stats(target, expected.prefix)
                if not _batch_matches(observed, expected):
                    raise RuntimeError(
                        f"event batch {expected.prefix} failed post-insert digest verification"
                    )
                batch_summary["inserted_prefixes"].append(expected.prefix)
            elif _batch_matches(observed, expected):
                batch_summary["skipped_complete_prefixes"].append(expected.prefix)
            else:
                raise RuntimeError(
                    f"event batch {expected.prefix} is partial or digest-mismatched; "
                    "automatic resume refused"
                )
            completed_rows += expected.rows

        if completed_rows != source.qualifying_rows:
            raise RuntimeError("event batch execution did not cover frozen source rows")
        stage = "COMPLETENESS"
        _assert_plan_live(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
            recheck_source=False,
        )
        completeness = verify_completeness(target, source)
        if completeness["complete"] is not True:
            raise RuntimeError("event serial lookup completeness failed")

        stage = "BENCHMARK"
        benchmark = benchmark_lookup(target)
        if benchmark["passed"] is not True:
            raise RuntimeError("event serial lookup production benchmark failed")
        _assert_plan_live(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
            recheck_source=False,
        )

        stage = "READY"
        target.command(_marker_sql())
        if ready_marker(target) != EVENT_SERIAL_LOOKUP_READY_VERSION:
            raise RuntimeError("event serial READY marker did not become visible")

        stage = "RUNTIME_SMOKE"
        smoke = events_for_serial(
            accepted_us_target_read_client(),
            str(benchmark["serial_number"]),
            limit=500,
        )
        if int(smoke.get("event_count") or 0) < 1:
            raise RuntimeError("event serial runtime smoke returned no events")
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "replayed": False,
            "plan_sha256": plan_sha,
            "implementation_sha": plan["implementation_sha"],
            "source_epoch": plan["source_epoch"],
            "source_stats": source.to_dict(),
            "batch_summary": batch_summary,
            "completeness": completeness,
            "benchmark": benchmark,
            "runtime_smoke": {
                "serial_number": smoke["serial_number"],
                "event_count": smoke["event_count"],
                "semantics": smoke["semantics"],
            },
            "ready_marker": EVENT_SERIAL_LOOKUP_READY_VERSION,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_receipt(receipt_path, receipt)
        return receipt
    except Exception as exc:
        _write_receipt(
            receipt_path,
            {
                "version": RECEIPT_VERSION,
                "status": "FAILED",
                "stage": stage,
                "plan_sha256": plan_sha,
                "batch_summary": batch_summary,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Governed US event serial lookup production gate"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)

    execute = sub.add_parser("execute")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--plan-sha", required=True)
    execute.add_argument("--authority-token", required=True)
    execute.add_argument("--receipt", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "prepare":
        envelope = prepare_plan(args.output)
        print(
            json.dumps(
                {
                    "plan_path": str(args.output),
                    **envelope,
                    "required_authority_token": authority_token(
                        str(envelope["plan_sha256"])
                    ),
                    "mutation_performed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    result = execute_plan(
        args.plan,
        plan_sha=args.plan_sha,
        authority=args.authority_token,
        receipt_path=args.receipt,
    )
    print(
        json.dumps(
            {"receipt_path": str(args.receipt), **result},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
