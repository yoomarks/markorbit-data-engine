from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.cn.entity_portfolio_activation_operator import (
    _query_rows,
    _sha256,
    _sorting_key,
    _sql_text,
    _text,
    _write_json_atomic,
    current_main_sha,
)
from app.cn.relationship_timeline import RELATIONSHIP_TIMELINE_READY_VERSION
from app.db import clickhouse_client

PLAN_VERSION = "CN_RELATIONSHIP_TIMELINE_ACTIVATION_PLAN_V1"
PROGRESS_VERSION = "CN_RELATIONSHIP_TIMELINE_ACTIVATION_PROGRESS_V1"
RECEIPT_VERSION = "CN_RELATIONSHIP_TIMELINE_ACTIVATION_RECEIPT_V1"
SOURCE_TABLE = "markorbit_facts.cn_observed_event"
TARGET_TABLE = "markorbit_facts.cn_trademark_relationship_event"
TARGET_MV = "cn_trademark_relationship_event_mv"
EXPECTED_TARGET_SORTING_KEY = "application_number, event_hash"
EVENT_TYPES = (
    "OWNER_RELATION_OBSERVED",
    "OWNER_RELATION_SUPERSEDED_OBSERVED",
    "CO_OWNER_RELATION_OBSERVED",
    "CO_OWNER_RELATION_SUPERSEDED_OBSERVED",
    "AGENT_RELATION_OBSERVED",
    "AGENT_RELATION_SUPERSEDED_OBSERVED",
)
FIXTURE_APPLICATION = "MO-TIMELINE-001"
FIXTURE_KIND = "CN_FIXTURE"
FIXTURE_FILE = "fixture.csv"
FIXTURE_HASHES = ("a" * 64, "b" * 64, "c" * 64)
DEFAULT_BATCH_EVENTS = 1_000_000
MAX_BATCH_EVENTS = 2_000_000


@dataclass(frozen=True, slots=True)
class ActivationProgress:
    plan_sha256: str
    after_event_hash: str = ""
    rows_submitted: int = 0
    batches_submitted: int = 0


def _event_type_sql() -> str:
    return ", ".join(_sql_text(value) for value in EVENT_TYPES)


def _rank_clause(max_source_rank: int | None) -> str:
    if max_source_rank is None:
        return ""
    return f"AND source_rank <= {int(max_source_rank)}"
def source_stats(client: Any, *, max_source_rank: int | None = None) -> dict[str, Any]:
    rows = _query_rows(
        client,
        f"""
        SELECT count(), max(source_rank), max(toString(event_hash)),
               max(application_number), groupBitXor(cityHash64(event_hash))
        FROM {SOURCE_TABLE} FINAL
        WHERE event_type IN ({_event_type_sql()})
          {_rank_clause(max_source_rank)}
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN relationship source statistics are unavailable")
    stats = {
        "row_count": int(rows[0][0] or 0),
        "max_source_rank": int(rows[0][1] or 0),
        "max_event_hash": _text(rows[0][2]),
        "max_application_number": _text(rows[0][3]),
        "event_hash_xor": int(rows[0][4] or 0),
    }
    if stats["row_count"] <= 0 or stats["max_source_rank"] <= 0:
        raise RuntimeError("CN relationship source is empty")
    if len(stats["max_event_hash"]) != 64:
        raise RuntimeError("CN relationship source watermark is malformed")
    return stats


def target_stats(client: Any, *, max_source_rank: int) -> dict[str, Any]:
    rows = _query_rows(
        client,
        f"""
        SELECT count(), max(source_rank), max(toString(event_hash)),
               max(application_number), groupBitXor(cityHash64(event_hash))
        FROM {TARGET_TABLE} FINAL
        WHERE source_rank <= {int(max_source_rank)}
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("CN relationship target statistics are unavailable")
    return {
        "row_count": int(rows[0][0] or 0),
        "max_source_rank": int(rows[0][1] or 0),
        "max_event_hash": _text(rows[0][2]),
        "max_application_number": _text(rows[0][3]),
        "event_hash_xor": int(rows[0][4] or 0),
    }


def fixture_rows(client: Any) -> list[tuple[str, str, int, str]]:
    hashes = ", ".join(_sql_text(value) for value in FIXTURE_HASHES)
    rows = _query_rows(
        client,
        f"""
        SELECT application_number, source_package_kind, source_rank, toString(event_hash)
        FROM {TARGET_TABLE} FINAL
        WHERE application_number = {_sql_text(FIXTURE_APPLICATION)}
          AND source_package_kind = {_sql_text(FIXTURE_KIND)}
          AND source_file = {_sql_text(FIXTURE_FILE)}
          AND event_hash IN ({hashes})
        ORDER BY source_rank, event_hash
        """,
        settings={"max_threads": 1},
    )
    return [
        (str(app), str(kind), int(rank), _text(event_hash))
        for app, kind, rank, event_hash in rows
    ]


def target_schema_state(client: Any) -> dict[str, Any]:
    rows = _query_rows(
        client,
        """
        SELECT name, engine, sorting_key
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name IN (
            'cn_observed_event',
            'cn_trademark_relationship_event',
            'cn_trademark_relationship_event_mv'
          )
        ORDER BY name
        """,
    )
    tables = {
        str(name): {"engine": str(engine), "sorting_key": _sorting_key(str(key))}
        for name, engine, key in rows
    }
    expected = {"cn_observed_event", "cn_trademark_relationship_event", TARGET_MV}
    if set(tables) != expected:
        raise RuntimeError("CN relationship activation schema is incomplete")
    if tables["cn_trademark_relationship_event"]["sorting_key"] != EXPECTED_TARGET_SORTING_KEY:
        raise RuntimeError("CN relationship target sorting key drifted")
    if tables[TARGET_MV]["engine"] != "MaterializedView":
        raise RuntimeError("CN relationship incremental materialized view is missing")
    return tables


def prepare_activation_plan(
    output_path: Path,
    *,
    batch_events: int = DEFAULT_BATCH_EVENTS,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    if not 1 <= batch_events <= MAX_BATCH_EVENTS:
        raise ValueError("batch_events is outside accepted bounds")
    target = client or clickhouse_client()
    implementation_sha = main_sha_getter().lower()
    source = source_stats(target)
    plan = {
        "version": PLAN_VERSION,
        "expected_main": implementation_sha,
        "implementation_sha": implementation_sha,
        "source_stats": source,
        "target_schema": target_schema_state(target),
        "fixture_rows": fixture_rows(target),
        "batch_events": batch_events,
        "mutation_scope": {
            "cleanup": "EXACT_FIXTURE_ROWS_ONLY",
            "backfill": "INSERT_ONLY",
            "target_table": TARGET_TABLE,
            "ready_version": RELATIONSHIP_TIMELINE_READY_VERSION,
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return envelope


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"plan", "plan_sha256"}:
        raise RuntimeError("CN relationship activation plan envelope is malformed")
    computed = _sha256(envelope["plan"])
    if envelope["plan_sha256"] != computed or computed != expected_sha.strip().lower():
        raise RuntimeError("CN relationship activation plan SHA mismatch")
    plan = dict(envelope["plan"])
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("CN relationship activation plan version is unsupported")
    return plan
def validate_live_plan(
    plan: Mapping[str, Any],
    *,
    client: Any,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> None:
    sha = main_sha_getter().lower()
    if sha != plan.get("expected_main") or sha != plan.get("implementation_sha"):
        raise RuntimeError("CN relationship activation main SHA drifted")
    if target_schema_state(client) != dict(plan.get("target_schema") or {}):
        raise RuntimeError("CN relationship activation schema drifted")
    frozen = dict(plan.get("source_stats") or {})
    max_rank = int(frozen.get("max_source_rank") or 0)
    if source_stats(client, max_source_rank=max_rank) != frozen:
        raise RuntimeError("CN relationship frozen source snapshot drifted")


def cleanup_fixture_rows(
    plan_path: Path,
    *,
    plan_sha: str,
    authority_token: str,
    client: Any | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    expected = f"GO #741 CN relationship timeline fixture cleanup {plan_sha.lower()}"
    if authority_token.strip() != expected:
        raise PermissionError("fresh exact fixture-cleanup authority token is required")
    target = client or clickhouse_client()
    validate_live_plan(plan, client=target)
    current = fixture_rows(target)
    frozen = [tuple(row) for row in plan.get("fixture_rows") or []]
    if current != frozen or len(current) != 3:
        raise RuntimeError("CN relationship fixture rows drifted from reviewed plan")
    hashes = ", ".join(_sql_text(value) for value in FIXTURE_HASHES)
    target.command(
        f"""
        ALTER TABLE {TARGET_TABLE}
        DELETE WHERE application_number = {_sql_text(FIXTURE_APPLICATION)}
          AND source_package_kind = {_sql_text(FIXTURE_KIND)}
          AND source_file = {_sql_text(FIXTURE_FILE)}
          AND event_hash IN ({hashes})
        SETTINGS mutations_sync = 2
        """
    )
    if fixture_rows(target):
        raise RuntimeError("CN relationship fixture cleanup did not converge")
    return {"status": "SUCCESS", "deleted_fixture_rows": 3, "plan_sha256": plan_sha.lower()}


def _boundary(client: Any, *, after_hash: str, max_rank: int, batch_events: int) -> str | None:
    after = f"AND event_hash > {_sql_text(after_hash)}" if after_hash else ""
    rows = _query_rows(
        client,
        f"""
        SELECT max(toString(event_hash))
        FROM (
            SELECT event_hash
            FROM {SOURCE_TABLE} FINAL
            WHERE event_type IN ({_event_type_sql()})
              AND source_rank <= {int(max_rank)}
              {after}
            ORDER BY event_hash
            LIMIT {int(batch_events)}
        )
        """,
        settings={"max_threads": 1},
    )
    boundary = _text(rows[0][0]).strip() if rows else ""
    return boundary or None


def _insert_batch(client: Any, *, after_hash: str, boundary: str, max_rank: int) -> None:
    after = f"AND event_hash > {_sql_text(after_hash)}" if after_hash else ""
    client.command(
        f"""
        INSERT INTO {TARGET_TABLE}
        SELECT event_id, application_number, event_type, event_date, observed_at,
               field_name, old_value_compact, new_value_compact, evidence_level,
               source_package_id, source_package_kind, source_file,
               source_first_line, source_last_line, source_row_hash, source_rank, event_hash
        FROM {SOURCE_TABLE} FINAL
        WHERE event_type IN ({_event_type_sql()})
          AND source_rank <= {int(max_rank)}
          {after}
          AND event_hash <= {_sql_text(boundary)}
        """
    )


def _batch_count(client: Any, *, after_hash: str, boundary: str, max_rank: int) -> int:
    after = f"AND event_hash > {_sql_text(after_hash)}" if after_hash else ""
    rows = _query_rows(
        client,
        f"""
        SELECT count()
        FROM {SOURCE_TABLE} FINAL
        WHERE event_type IN ({_event_type_sql()})
          AND source_rank <= {int(max_rank)}
          {after}
          AND event_hash <= {_sql_text(boundary)}
        """,
        settings={"max_threads": 1},
    )
    return int(rows[0][0] or 0)


def _write_ready(client: Any) -> None:
    client.command(
        f"""
        INSERT INTO markorbit_facts.schema_version (component, version)
        VALUES ('CN_RELATIONSHIP_TIMELINE', {_sql_text(RELATIONSHIP_TIMELINE_READY_VERSION)})
        """
    )


def execute_backfill(
    plan_path: Path,
    *,
    plan_sha: str,
    authority_token: str,
    progress_path: Path,
    receipt_path: Path,
    client: Any | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    expected = f"GO #741 CN relationship timeline activation {plan_sha.lower()}"
    if authority_token.strip() != expected:
        raise PermissionError("fresh exact relationship activation authority token is required")
    target = client or clickhouse_client()
    validate_live_plan(plan, client=target)
    if fixture_rows(target):
        raise RuntimeError("reviewed fixture cleanup must complete before backfill")
    frozen = dict(plan["source_stats"])
    max_rank = int(frozen["max_source_rank"])
    if progress_path.exists():
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
        if payload.get("version") != PROGRESS_VERSION:
            raise RuntimeError("CN relationship progress version is unsupported")
        progress = ActivationProgress(**payload["progress"])
    else:
        progress = ActivationProgress(plan_sha256=plan_sha.lower())
    if progress.plan_sha256 != plan_sha.lower():
        raise RuntimeError("CN relationship progress belongs to another plan")
    batch_events = int(plan["batch_events"])
    try:
        while True:
            boundary = _boundary(
                target,
                after_hash=progress.after_event_hash,
                max_rank=max_rank,
                batch_events=batch_events,
            )
            if boundary is None:
                break
            count = _batch_count(
                target,
                after_hash=progress.after_event_hash,
                boundary=boundary,
                max_rank=max_rank,
            )
            _insert_batch(
                target,
                after_hash=progress.after_event_hash,
                boundary=boundary,
                max_rank=max_rank,
            )
            progress = ActivationProgress(
                plan_sha256=progress.plan_sha256,
                after_event_hash=boundary,
                rows_submitted=progress.rows_submitted + count,
                batches_submitted=progress.batches_submitted + 1,
            )
            _write_json_atomic(
                progress_path,
                {"version": PROGRESS_VERSION, "progress": asdict(progress)},
            )
        actual = target_stats(target, max_source_rank=max_rank)
        if actual != frozen:
            raise RuntimeError(f"CN relationship completeness mismatch: {actual!r} != {frozen!r}")
        _write_ready(target)
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "implementation_sha": plan["implementation_sha"],
            "source_stats": frozen,
            "target_stats": actual,
            "progress": asdict(progress),
            "ready_version": RELATIONSHIP_TIMELINE_READY_VERSION,
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
    parser = argparse.ArgumentParser(description="Guarded CN relationship timeline production activation")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path)
    prepare.add_argument("--batch-events", type=int, default=DEFAULT_BATCH_EVENTS)
    for command in ("cleanup", "apply"):
        action = sub.add_parser(command)
        action.add_argument("--plan", type=Path, required=True)
        action.add_argument("--plan-sha", required=True)
        action.add_argument("--authority-token", required=True)
        if command == "apply":
            action.add_argument("--progress", type=Path)
            action.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        path = args.output or _default_output("production_cn_relationship_timeline_plan")
        result = prepare_activation_plan(path, batch_events=args.batch_events)
        print(json.dumps({"plan_path": str(path), **result}, indent=2, sort_keys=True))
        return 0
    if args.command == "cleanup":
        result = cleanup_fixture_rows(
            args.plan,
            plan_sha=args.plan_sha,
            authority_token=args.authority_token,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    progress = args.progress or _default_output("production_cn_relationship_timeline_progress")
    receipt = args.receipt or _default_output("production_cn_relationship_timeline_receipt")
    result = execute_backfill(
        args.plan,
        plan_sha=args.plan_sha,
        authority_token=args.authority_token,
        progress_path=progress,
        receipt_path=receipt,
    )
    print(json.dumps({"receipt_path": str(receipt), **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
