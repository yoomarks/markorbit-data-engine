from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.cn.applicant_name_lookup_backfill_control import current_cn_applicant_serving_epoch
from app.cn.entity_portfolio_activation_operator import (
    ENTITY_TABLE,
    READINESS_TABLE,
    RELATIONSHIP_TABLE,
    _entity_fingerprint_expr,
    _query_rows,
    _sha256,
    _sql_text,
    _text,
    _write_json_atomic,
    benchmark_entity_lookup,
    current_main_sha,
    entity_projection_select,
    relationship_timeline_readiness,
    target_schema_state,
    _write_readiness,
)
from app.cn.entity_trademark_portfolio import READY_VERSION
from app.db import clickhouse_client

PLAN_VERSION = "CN_ENTITY_PORTFOLIO_ACTIVATION_PLAN_V4"
PROGRESS_VERSION = "CN_ENTITY_PORTFOLIO_ACTIVATION_PROGRESS_V3"
RECEIPT_VERSION = "CN_ENTITY_PORTFOLIO_ACTIVATION_RECEIPT_V2"
DEFAULT_BATCH_APPLICATIONS = 1_000_000
MAX_BATCH_APPLICATIONS = 2_000_000


@dataclass(frozen=True, slots=True)
class ActivationProgress:
    plan_sha256: str
    stage: str = "ENTITY"
    after_application_number: str = ""
    entity_rows_verified: int = 0
    entity_hash_xor: int = 0
    entity_batches_verified: int = 0

    def __post_init__(self) -> None:
        if self.stage not in {"ENTITY", "COMPLETE"}:
            raise ValueError("activation stage is invalid")
        if len(self.plan_sha256) != 64:
            raise ValueError("progress plan SHA-256 is invalid")


def relationship_stats(
    client: Any,
    *,
    max_source_rank: int | None = None,
    final: bool,
) -> dict[str, Any]:
    final_sql = " FINAL" if final else ""
    rank_sql = (
        f"WHERE source_rank <= {int(max_source_rank)}"
        if max_source_rank is not None
        else ""
    )
    rows = _query_rows(
        client,
        f"""
        SELECT count(), max(source_rank), max(toString(event_hash)),
               max(application_number), groupBitXor(cityHash64(event_hash))
        FROM {RELATIONSHIP_TABLE}{final_sql}
        {rank_sql}
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN relationship statistics are unavailable")
    return {
        "row_count": int(rows[0][0] or 0),
        "max_source_rank": int(rows[0][1] or 0),
        "max_event_hash": _text(rows[0][2]),
        "max_application_number": _text(rows[0][3]),
        "event_hash_xor": int(rows[0][4] or 0),
    }


def optimized_projection(
    *, after_application: str, boundary_application: str, max_source_rank: int
) -> str:
    sql = entity_projection_select(
        after_application=after_application,
        boundary_application=boundary_application,
        max_source_rank=max_source_rank,
    )
    needle = f"FROM {RELATIONSHIP_TABLE} FINAL"
    if needle not in sql:
        raise RuntimeError("CN entity projection source shape drifted")
    return sql.replace(needle, f"FROM {RELATIONSHIP_TABLE}", 1)


def entity_range_stats(
    client: Any,
    *,
    after_application: str,
    boundary_application: str,
    max_source_rank: int,
    expected: bool,
) -> dict[str, int]:
    if expected:
        source = optimized_projection(
            after_application=after_application,
            boundary_application=boundary_application,
            max_source_rank=max_source_rank,
        )
        sql = f"SELECT count(), groupBitXor({_entity_fingerprint_expr()}) FROM ({source})"
    else:
        clauses = [f"application_number <= {_sql_text(boundary_application)}"]
        if after_application:
            clauses.append(f"application_number > {_sql_text(after_application)}")
        sql = f"""
            SELECT count(), groupBitXor({_entity_fingerprint_expr()})
            FROM {ENTITY_TABLE}
            WHERE source_rank <= {int(max_source_rank)}
              AND {' AND '.join(clauses)}
        """
    rows = _query_rows(client, sql, settings={"max_threads": 4})
    if len(rows) != 1:
        raise RuntimeError("CN entity range statistics query failed")
    return {"row_count": int(rows[0][0] or 0), "row_hash_xor": int(rows[0][1] or 0)}


def entity_total_stats(client: Any, *, max_source_rank: int) -> dict[str, Any]:
    rows = _query_rows(
        client,
        f"""
        SELECT count(), max(application_number),
               groupBitXor({_entity_fingerprint_expr()})
        FROM {ENTITY_TABLE}
        WHERE source_rank <= {int(max_source_rank)}
        """,
        settings={"max_threads": 4},
    )
    if len(rows) != 1:
        raise RuntimeError("CN entity total statistics query failed")
    return {
        "row_count": int(rows[0][0] or 0),
        "max_application_number": _text(rows[0][1]),
        "row_hash_xor": int(rows[0][2] or 0),
    }


def application_boundary(
    client: Any,
    *,
    after_application: str,
    max_source_rank: int,
    batch_size: int,
) -> str | None:
    after_sql = (
        f"PREWHERE application_number > {_sql_text(after_application)}"
        if after_application
        else ""
    )
    rows = _query_rows(
        client,
        f"""
        SELECT max(application_number)
        FROM
        (
            SELECT application_number
            FROM {RELATIONSHIP_TABLE}
            {after_sql}
            WHERE source_rank <= {int(max_source_rank)}
            GROUP BY application_number
            ORDER BY application_number
            LIMIT {int(batch_size)}
        )
        """,
        settings={"max_threads": 4, "optimize_aggregation_in_order": 1},
    )
    boundary = _text(rows[0][0]).strip() if rows else ""
    return boundary or None


def insert_entity_batch(
    client: Any,
    *,
    after_application: str,
    boundary_application: str,
    max_source_rank: int,
) -> None:
    projection = optimized_projection(
        after_application=after_application,
        boundary_application=boundary_application,
        max_source_rank=max_source_rank,
    )
    client.command(f"INSERT INTO {ENTITY_TABLE} {projection}")


def _same_stats(left: Mapping[str, int], right: Mapping[str, int]) -> bool:
    return dict(left) == dict(right)


def batch_action(expected: Mapping[str, int], actual: Mapping[str, int]) -> str:
    if _same_stats(expected, actual):
        return "RECOVER"
    if int(actual.get("row_count") or 0) == 0 and int(actual.get("row_hash_xor") or 0) == 0:
        return "INSERT"
    return "MISMATCH"


def prepare_activation_plan(
    output_path: Path,
    *,
    batch_applications: int = DEFAULT_BATCH_APPLICATIONS,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    if not 1 <= batch_applications <= MAX_BATCH_APPLICATIONS:
        raise ValueError("batch application count is outside accepted bounds")
    target = client or clickhouse_client()
    implementation_sha = main_sha_getter().lower()
    readiness = relationship_timeline_readiness(target)
    schema = target_schema_state(target)
    final_stats = relationship_stats(target, final=True)
    physical_stats = relationship_stats(
        target, max_source_rank=int(final_stats["max_source_rank"]), final=False
    )
    if physical_stats != final_stats:
        raise RuntimeError("CN relationship physical snapshot is not FINAL-equivalent")
    current = entity_total_stats(target, max_source_rank=int(final_stats["max_source_rank"]))
    resume_after = str(current["max_application_number"])
    if current["row_count"]:
        expected_prefix = entity_range_stats(
            target,
            after_application="",
            boundary_application=resume_after,
            max_source_rank=int(final_stats["max_source_rank"]),
            expected=True,
        )
        actual_prefix = {
            "row_count": int(current["row_count"]),
            "row_hash_xor": int(current["row_hash_xor"]),
        }
        if expected_prefix != actual_prefix:
            raise RuntimeError("CN entity existing rows are not a complete source prefix")
    plan = {
        "version": PLAN_VERSION,
        "expected_main": implementation_sha,
        "implementation_sha": implementation_sha,
        "source_epoch": current_cn_applicant_serving_epoch().to_dict(),
        "upstream_readiness": readiness,
        "source_stats": final_stats,
        "physical_source_stats": physical_stats,
        "target_schema": schema,
        "batch_applications": batch_applications,
        "resume_prefix": {
            "after_application_number": resume_after,
            "entity_rows_verified": int(current["row_count"]),
            "entity_hash_xor": int(current["row_hash_xor"]),
        },
        "mutation_scope": {
            "insert_only_tables": [ENTITY_TABLE, READINESS_TABLE],
            "destructive_operations": False,
            "source_final_elision_requires_physical_equivalence": True,
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    _write_json_atomic(output_path, envelope)
    return envelope


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    computed = _sha256(envelope["plan"])
    if envelope.get("plan_sha256") != computed or computed != expected_sha.strip().lower():
        raise RuntimeError("CN entity V4 plan SHA mismatch")
    plan = dict(envelope["plan"])
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("CN entity V4 plan version is unsupported")
    return plan


def validate_live_plan(
    plan: Mapping[str, Any],
    *,
    client: Any,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> None:
    if main_sha_getter().lower() != str(plan["expected_main"]):
        raise RuntimeError("CN entity V4 main SHA drifted")
    if relationship_timeline_readiness(client) != dict(plan["upstream_readiness"]):
        raise RuntimeError("CN entity V4 upstream readiness drifted")
    if target_schema_state(client) != dict(plan["target_schema"]):
        raise RuntimeError("CN entity V4 target schema drifted")
    frozen = dict(plan["source_stats"])
    max_rank = int(frozen["max_source_rank"])
    if relationship_stats(client, max_source_rank=max_rank, final=True) != frozen:
        raise RuntimeError("CN entity V4 frozen FINAL source drifted")
    if relationship_stats(client, max_source_rank=max_rank, final=False) != dict(
        plan["physical_source_stats"]
    ):
        raise RuntimeError("CN entity V4 frozen physical source drifted")


def initial_progress(plan: Mapping[str, Any], plan_sha: str) -> ActivationProgress:
    prefix = dict(plan["resume_prefix"])
    return ActivationProgress(
        plan_sha256=plan_sha.lower(),
        after_application_number=str(prefix["after_application_number"]),
        entity_rows_verified=int(prefix["entity_rows_verified"]),
        entity_hash_xor=int(prefix["entity_hash_xor"]),
    )


def load_progress(path: Path, plan: Mapping[str, Any], plan_sha: str) -> ActivationProgress:
    if not path.exists():
        return initial_progress(plan, plan_sha)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != PROGRESS_VERSION:
        raise RuntimeError("CN entity V4 progress version is unsupported")
    progress = ActivationProgress(**dict(payload["progress"]))
    if progress.plan_sha256 != plan_sha.lower():
        raise RuntimeError("CN entity V4 progress belongs to another plan")
    return progress


def save_progress(path: Path, progress: ActivationProgress) -> None:
    _write_json_atomic(path, {"version": PROGRESS_VERSION, "progress": asdict(progress)})


def execute_activation_plan(
    plan_path: Path,
    *,
    plan_sha: str,
    authority_token: str,
    progress_path: Path,
    receipt_path: Path,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    expected_token = f"GO #741 CN entity portfolio activation {plan_sha.lower()}"
    if authority_token.strip() != expected_token:
        raise PermissionError("fresh exact CN entity V4 authority token is required")
    target = client or clickhouse_client()
    validate_live_plan(plan, client=target, main_sha_getter=main_sha_getter)
    source_stats = dict(plan["source_stats"])
    max_rank = int(source_stats["max_source_rank"])
    batch_size = int(plan["batch_applications"])
    progress = load_progress(progress_path, plan, plan_sha)
    try:
        while progress.stage == "ENTITY":
            boundary = application_boundary(
                target,
                after_application=progress.after_application_number,
                max_source_rank=max_rank,
                batch_size=batch_size,
            )
            if boundary is None:
                progress = ActivationProgress(
                    plan_sha256=progress.plan_sha256,
                    stage="COMPLETE",
                    after_application_number=progress.after_application_number,
                    entity_rows_verified=progress.entity_rows_verified,
                    entity_hash_xor=progress.entity_hash_xor,
                    entity_batches_verified=progress.entity_batches_verified,
                )
                save_progress(progress_path, progress)
                break
            expected = entity_range_stats(
                target,
                after_application=progress.after_application_number,
                boundary_application=boundary,
                max_source_rank=max_rank,
                expected=True,
            )
            actual = entity_range_stats(
                target,
                after_application=progress.after_application_number,
                boundary_application=boundary,
                max_source_rank=max_rank,
                expected=False,
            )
            action = batch_action(expected, actual)
            if action == "MISMATCH":
                raise RuntimeError(
                    f"CN entity V4 batch pre-state mismatch expected={expected!r} actual={actual!r}"
                )
            if action == "INSERT":
                insert_entity_batch(
                    target,
                    after_application=progress.after_application_number,
                    boundary_application=boundary,
                    max_source_rank=max_rank,
                )
                actual = entity_range_stats(
                    target,
                    after_application=progress.after_application_number,
                    boundary_application=boundary,
                    max_source_rank=max_rank,
                    expected=False,
                )
                if not _same_stats(expected, actual):
                    raise RuntimeError(
                        f"CN entity V4 batch completeness mismatch expected={expected!r} actual={actual!r}"
                    )
            progress = ActivationProgress(
                plan_sha256=progress.plan_sha256,
                stage="ENTITY",
                after_application_number=boundary,
                entity_rows_verified=progress.entity_rows_verified + expected["row_count"],
                entity_hash_xor=progress.entity_hash_xor ^ expected["row_hash_xor"],
                entity_batches_verified=progress.entity_batches_verified + 1,
            )
            save_progress(progress_path, progress)

        if progress.stage != "COMPLETE":
            raise RuntimeError("CN entity V4 activation did not reach COMPLETE")
        validate_live_plan(plan, client=target, main_sha_getter=main_sha_getter)
        actual_total = entity_total_stats(target, max_source_rank=max_rank)
        expected_total = {
            "row_count": progress.entity_rows_verified,
            "row_hash_xor": progress.entity_hash_xor,
        }
        actual_fingerprint = {
            "row_count": int(actual_total["row_count"]),
            "row_hash_xor": int(actual_total["row_hash_xor"]),
        }
        if actual_fingerprint != expected_total:
            raise RuntimeError(
                f"CN entity V4 final completeness mismatch expected={expected_total!r} actual={actual_fingerprint!r}"
            )
        benchmark = benchmark_entity_lookup(target, max_source_rank=max_rank)
        implementation_sha = str(plan["implementation_sha"])
        _write_readiness(
            target,
            plan_sha256=plan_sha.lower(),
            implementation_sha=implementation_sha,
            max_source_rank=max_rank,
            max_event_hash=str(source_stats["max_event_hash"]),
        )
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "implementation_sha": implementation_sha,
            "source_stats": source_stats,
            "progress": asdict(progress),
            "target_stats": actual_total,
            "benchmark": benchmark,
            "readiness_version": READY_VERSION,
        }
    except Exception as exc:
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "FAILED",
            "plan_sha256": plan_sha.lower(),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "progress": asdict(progress),
        }
        _write_json_atomic(receipt_path, receipt)
        raise
    _write_json_atomic(receipt_path, receipt)
    return receipt


def _default_output(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parents[2] / "reports" / f"{prefix}_{stamp}.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optimized guarded CN entity portfolio activation")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--batch-applications", type=int, default=DEFAULT_BATCH_APPLICATIONS)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--plan-sha", required=True)
    apply.add_argument("--authority-token", required=True)
    apply.add_argument("--progress", type=Path, required=True)
    apply.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare_activation_plan(
            args.output,
            batch_applications=args.batch_applications,
        )
        print(json.dumps({"plan_path": str(args.output), **result}, indent=2, sort_keys=True))
        return 0
    result = execute_activation_plan(
        args.plan,
        plan_sha=args.plan_sha,
        authority_token=args.authority_token,
        progress_path=args.progress,
        receipt_path=args.receipt,
    )
    print(json.dumps({"receipt_path": str(args.receipt), **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
