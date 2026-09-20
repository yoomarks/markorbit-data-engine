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
from app.us.registration_lookup import (
    REGISTRATION_LOOKUP_READY_VERSION,
    US_REGISTRATION_LOOKUP_TABLE,
    lookup_registration,
)
from app.us.target_canary import (
    TARGET_DATABASE,
    TARGET_STORAGE_POLICY,
    WslNativeClickHouseClient,
)

PLAN_VERSION = "US_REGISTRATION_LOOKUP_PRODUCTION_GATE_PLAN_V1"
RECEIPT_VERSION = "US_REGISTRATION_LOOKUP_PRODUCTION_GATE_RECEIPT_V1"
SOURCE_TABLE = "markorbit_facts.us_case_current"
READY_COMPONENT = "US_REGISTRATION_CANDIDATE_LOOKUP"
BENCHMARK_RUNS = 7
SLO_P95_MS = 150.0
MIN_FREE_RATIO_AFTER_ESTIMATE = 0.30
EXPECTED_COLUMNS = [
    ("registration_number", "String"),
    ("serial_number", "String"),
    ("source_row_hash", "FixedString(64)"),
    ("record_hash", "FixedString(64)"),
    ("source_package_id", "UUID"),
    ("source_rank", "UInt64"),
    ("observed_at", "DateTime64(3, 'UTC')"),
]
SOURCE_BINDING_HASH = (
    "cityHash64(concat(registration_number,'\\x1f',serial_number,'\\x1f',"
    "source_row_hash,'\\x1f',record_hash,'\\x1f',toString(last_source_package_id),"
    "'\\x1f',toString(source_rank)))"
)
LOOKUP_BINDING_HASH = (
    "cityHash64(concat(registration_number,'\\x1f',serial_number,'\\x1f',"
    "source_row_hash,'\\x1f',record_hash,'\\x1f',toString(source_package_id),"
    "'\\x1f',toString(source_rank)))"
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
    if head != origin or dirty:
        raise RuntimeError("registration production gate requires clean exact main")
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
        SELECT countIf(is_deleted=0),
               countIf(is_deleted=0 AND registration_number!=''),
               maxIf(source_rank,is_deleted=0 AND registration_number!=''),
               toString(sumIf({SOURCE_BINDING_HASH},is_deleted=0 AND registration_number!='')),
               toString(groupBitXorIf({SOURCE_BINDING_HASH},is_deleted=0 AND registration_number!=''))
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
def _table_metadata(client: Any) -> list[list[Any]]:
    return _rows(
        client,
        f"""
        SELECT engine,sorting_key,storage_policy
        FROM system.tables
        WHERE database='{TARGET_DATABASE}'
          AND name='us_registration_candidate_lookup'
        """,
    )


def lookup_stats(client: Any) -> dict[str, object]:
    meta = _table_metadata(client)
    if not meta:
        return {
            "exists": False,
            "visible_rows": 0,
            "distinct_registrations": 0,
            "max_source_rank": 0,
            "binding_sum": "0",
            "binding_xor": "0",
        }
    if len(meta) != 1:
        raise RuntimeError("registration lookup table metadata is not exact-one")
    engine, sorting_key, policy = (str(value) for value in meta[0])
    normalized_sort = (
        sorting_key.replace(chr(96), "").replace("(", "").replace(")", "").strip()
    )
    if engine != "ReplacingMergeTree":
        raise RuntimeError("registration lookup engine drifted")
    if normalized_sort != "registration_number, serial_number":
        raise RuntimeError("registration lookup sorting key drifted")
    if policy != TARGET_STORAGE_POLICY:
        raise RuntimeError("registration lookup storage policy drifted")
    columns = _rows(
        client,
        f"""
        SELECT name,type
        FROM system.columns
        WHERE database='{TARGET_DATABASE}'
          AND table='us_registration_candidate_lookup'
        ORDER BY position
        """,
    )
    observed = [(str(row[0]), str(row[1])) for row in columns]
    if observed != EXPECTED_COLUMNS:
        raise RuntimeError("registration lookup column contract drifted")
    row = _rows(
        client,
        f"""
        SELECT count(),uniqExact(registration_number),max(source_rank),
               toString(sum({LOOKUP_BINDING_HASH})),
               toString(groupBitXor({LOOKUP_BINDING_HASH}))
        FROM {US_REGISTRATION_LOOKUP_TABLE} FINAL
        """,
    )[0]
    return {
        "exists": True,
        "visible_rows": int(row[0]),
        "distinct_registrations": int(row[1]),
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
CREATE TABLE IF NOT EXISTS {US_REGISTRATION_LOOKUP_TABLE}
(
    registration_number String,
    serial_number String,
    source_row_hash FixedString(64),
    record_hash FixedString(64),
    source_package_id UUID,
    source_rank UInt64,
    observed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(source_rank)
ORDER BY (registration_number, serial_number)
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
        WHERE database='{TARGET_DATABASE}' AND name='us_case_current'
        """,
    )[0]
    if str(table[1]) != TARGET_STORAGE_POLICY:
        raise RuntimeError("accepted us_case_current storage policy drifted")
    source_bytes = int(table[0])
    estimated = max(source_bytes * 2, 1)
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
    lookup = lookup_stats(target)
    marker = ready_marker(target)
    if lookup["exists"] or marker is not None:
        raise RuntimeError(
            "registration gate prepare requires absent lookup and READY marker"
        )
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha,
        "implementation_sha": main_sha,
        "source_epoch": epoch_getter().to_dict(),
        "source_stats": stats.to_dict(),
        "target_precondition": {"lookup": lookup, "ready_marker": marker},
        "capacity_contract": _capacity_contract(target),
        "target_schema": {
            "storage_policy": TARGET_STORAGE_POLICY,
            "table_ddl_sha256": hashlib.sha256(
                target_table_ddl().encode("utf-8")
            ).hexdigest(),
        },
        "acceptance": {
            "benchmark_runs": BENCHMARK_RUNS,
            "exact_registration_p95_ms": SLO_P95_MS,
            "ready_version": REGISTRATION_LOOKUP_READY_VERSION,
        },
        "mutation_scope": {
            "create_table": US_REGISTRATION_LOOKUP_TABLE,
            "backfill_source": SOURCE_TABLE,
            "final_marker_component": READY_COMPONENT,
            "operation": "CREATE_INSERT_ONLY",
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
        raise RuntimeError("malformed registration production-gate plan")
    plan = dict(envelope["plan"])
    actual = _sha256(plan)
    if actual != expected_sha.lower() or str(envelope["plan_sha256"]) != actual:
        raise RuntimeError("registration production-gate plan SHA mismatch")
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("unsupported registration production-gate plan version")
    if dict(plan.get("mutation_scope") or {}) != {
        "create_table": US_REGISTRATION_LOOKUP_TABLE,
        "backfill_source": SOURCE_TABLE,
        "final_marker_component": READY_COMPONENT,
        "operation": "CREATE_INSERT_ONLY",
    }:
        raise RuntimeError("registration production-gate mutation scope drifted")
    return plan


def _frozen_stats(plan: Mapping[str, Any]) -> SourceStats:
    return SourceStats(**dict(plan["source_stats"]))


def _assert_plan_live(
    plan: Mapping[str, Any],
    *,
    client: Any,
    epoch_getter: Callable[[], USApplicantServingEpoch],
    main_sha_getter: Callable[[], str],
) -> SourceStats:
    main_sha = main_sha_getter()
    if main_sha != str(plan["expected_main"]) or main_sha != str(
        plan["implementation_sha"]
    ):
        raise RuntimeError("registration production-gate main SHA drifted")
    if epoch_getter().to_dict() != dict(plan["source_epoch"]):
        raise RuntimeError("accepted US serving epoch drifted")
    observed = source_stats(client)
    frozen = _frozen_stats(plan)
    if observed != frozen:
        raise RuntimeError("accepted current case truth drifted")
    live_capacity = _capacity_contract(client)
    frozen_capacity = dict(plan["capacity_contract"])
    if (
        live_capacity["hot_us_total_bytes"]
        != frozen_capacity["hot_us_total_bytes"]
    ):
        raise RuntimeError("hot_us total capacity drifted")
    if live_capacity["projected_free_bytes"] < int(
        int(live_capacity["hot_us_total_bytes"]) * MIN_FREE_RATIO_AFTER_ESTIMATE
    ):
        raise RuntimeError("hot_us live reserve fell below 30%")
    schema = dict(plan["target_schema"])
    if schema["storage_policy"] != TARGET_STORAGE_POLICY:
        raise RuntimeError("registration target storage policy drifted")
    if schema["table_ddl_sha256"] != hashlib.sha256(
        target_table_ddl().encode("utf-8")
    ).hexdigest():
        raise RuntimeError("registration target DDL hash drifted")
    return frozen
def _sql(value: object) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sample_mismatches(client: Any, limit: int = 200) -> dict[str, int]:
    sample = _rows(
        client,
        f"""
        SELECT registration_number,serial_number,source_row_hash,record_hash,
               toString(source_package_id),source_rank
        FROM {US_REGISTRATION_LOOKUP_TABLE} FINAL
        ORDER BY registration_number,serial_number
        LIMIT {int(limit)}
        """,
    )
    if not sample:
        return {"checked": 0, "mismatches": 0}
    serials = ", ".join(_sql(row[1]) for row in sample)
    source = _rows(
        client,
        f"""
        SELECT registration_number,serial_number,source_row_hash,record_hash,
               toString(last_source_package_id),source_rank
        FROM {SOURCE_TABLE} FINAL
        WHERE is_deleted=0 AND serial_number IN ({serials})
        """,
    )
    current = {str(row[1]): tuple(str(v) for v in row) for row in source}
    mismatches = sum(
        1
        for row in sample
        if current.get(str(row[1])) != tuple(str(v) for v in row)
    )
    return {"checked": len(sample), "mismatches": mismatches}


def verify_completeness(
    client: Any, expected: SourceStats
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
def _backfill_sql() -> str:
    return f"""
INSERT INTO {US_REGISTRATION_LOOKUP_TABLE}
(registration_number,serial_number,source_row_hash,record_hash,source_package_id,source_rank)
SELECT registration_number,serial_number,source_row_hash,record_hash,
       last_source_package_id,source_rank
FROM {SOURCE_TABLE} FINAL
WHERE is_deleted=0 AND registration_number!=''
""".strip()


def _pick_benchmark_registration(client: Any) -> str:
    rows = _rows(
        client,
        f"""
        SELECT registration_number,count() AS n
        FROM {US_REGISTRATION_LOOKUP_TABLE} FINAL
        GROUP BY registration_number
        HAVING n BETWEEN 1 AND 20
        ORDER BY registration_number
        LIMIT 1
        """,
    )
    if len(rows) != 1:
        raise RuntimeError("could not select bounded registration benchmark")
    return str(rows[0][0])
def _benchmark_once(client: Any, registration: str) -> tuple[float, int]:
    started = time.perf_counter()
    candidates = _rows(
        client,
        f"""
        SELECT serial_number FROM {US_REGISTRATION_LOOKUP_TABLE} FINAL
        WHERE registration_number={_sql(registration)}
        ORDER BY registration_number,serial_number
        LIMIT 501
        """,
    )
    if not candidates or len(candidates) > 500:
        raise RuntimeError("registration benchmark candidate bound failed")
    serials = ", ".join(_sql(row[0]) for row in candidates)
    matches = _rows(
        client,
        f"""
        SELECT serial_number FROM {SOURCE_TABLE} FINAL
        WHERE serial_number IN ({serials})
          AND registration_number={_sql(registration)}
          AND is_deleted=0
        ORDER BY serial_number
        LIMIT 500
        """,
    )
    return (time.perf_counter() - started) * 1000.0, len(matches)
def benchmark_lookup(client: Any) -> dict[str, object]:
    registration = _pick_benchmark_registration(client)
    elapsed: list[float] = []
    counts: list[int] = []
    for _ in range(BENCHMARK_RUNS):
        ms, count = _benchmark_once(client, registration)
        elapsed.append(ms)
        counts.append(count)
    ordered = sorted(elapsed)
    p95 = ordered[-1]
    return {
        "registration_number": registration,
        "runs": BENCHMARK_RUNS,
        "elapsed_ms": [round(value, 3) for value in elapsed],
        "p50_ms": round(ordered[len(ordered) // 2], 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(elapsed), 3),
        "match_count": counts[0],
        "stable_match_count": len(set(counts)) == 1,
        "slo_p95_ms": SLO_P95_MS,
        "passed": p95 <= SLO_P95_MS and min(counts) > 0 and len(set(counts)) == 1,
    }


def authority_token(plan_sha: str) -> str:
    return f"GO #765 US-REGISTRATION-LOOKUP {plan_sha} ACTIVATE"
def _marker_sql() -> str:
    return (
        "INSERT INTO markorbit_facts.schema_version (component,version) "
        f"VALUES ('{READY_COMPONENT}','{REGISTRATION_LOOKUP_READY_VERSION}')"
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
        raise PermissionError("exact #765 authority token is required")
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
        if marker == REGISTRATION_LOOKUP_READY_VERSION:
            completeness = verify_completeness(target, source)
            benchmark = benchmark_lookup(target)
            if not completeness["complete"] or not benchmark["passed"]:
                raise RuntimeError("READY registration lookup failed replay verification")
            receipt = {
                "version": RECEIPT_VERSION,
                "status": "SUCCESS",
                "replayed": True,
                "plan_sha256": plan_sha,
                "implementation_sha": plan["implementation_sha"],
                "completeness": completeness,
                "benchmark": benchmark,
                "ready_marker": marker,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_receipt(receipt_path, receipt)
            return receipt
        if marker is not None:
            raise RuntimeError(f"unexpected registration lookup marker: {marker}")
        if lookup["exists"] is not True:
            stage = "SCHEMA"
            target.command(target_table_ddl())
            lookup = lookup_stats(target)
        visible = int(lookup["visible_rows"])
        if visible not in {0, source.qualifying_rows}:
            raise RuntimeError(
                "registration lookup is partially populated; automatic resume refused"
            )

        _assert_plan_live(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )
        if visible == 0:
            stage = "BACKFILL"
            target.command(_backfill_sql())

        stage = "COMPLETENESS"
        _assert_plan_live(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )
        completeness = verify_completeness(target, source)
        if completeness["complete"] is not True:
            raise RuntimeError("registration lookup completeness failed")
        stage = "BENCHMARK"
        benchmark = benchmark_lookup(target)
        _assert_plan_live(
            plan,
            client=target,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )
        if benchmark["passed"] is not True:
            raise RuntimeError("registration lookup production benchmark failed")

        stage = "READY"
        target.command(_marker_sql())
        if ready_marker(target) != REGISTRATION_LOOKUP_READY_VERSION:
            raise RuntimeError("registration READY marker did not become visible")

        stage = "RUNTIME_SMOKE"
        smoke = lookup_registration(
            accepted_us_target_read_client(),
            str(benchmark["registration_number"]),
        )
        if int(smoke.get("match_count") or 0) < 1:
            raise RuntimeError("registration runtime smoke returned no current fact")

        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "replayed": False,
            "plan_sha256": plan_sha,
            "implementation_sha": plan["implementation_sha"],
            "source_epoch": plan["source_epoch"],
            "source_stats": source.to_dict(),
            "completeness": completeness,
            "benchmark": benchmark,
            "runtime_smoke": {
                "registration_number": smoke["registration_number"],
                "candidate_count": smoke["candidate_count"],
                "match_count": smoke["match_count"],
                "semantics": smoke["semantics"],
            },
            "ready_marker": REGISTRATION_LOOKUP_READY_VERSION,
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
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Governed US registration lookup production gate"
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
