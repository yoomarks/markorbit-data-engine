from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.cn.entity_portfolio_activation_operator import _sha256, current_main_sha
from app.db import clickhouse_client
from app.us.recorded_party_activation_support import readiness_rows, target_stats
from app.us.recorded_party_schema_activation_operator import (
    EXPECTED_SCHEMA,
    source_versions,
    target_schema_state,
)

PLAN_VERSION = "US_RECORDED_PARTY_SCHEMA_REPAIR_PLAN_V1"
RECEIPT_VERSION = "US_RECORDED_PARTY_SCHEMA_REPAIR_RECEIPT_V1"
PRE_SCHEMA_VERSION = "US_RECORDED_PARTY_HISTORY_SCHEMA_V1"
POST_SCHEMA_VERSION = "US_RECORDED_PARTY_HISTORY_SCHEMA_V2"
SQL_RELATIVE_PATH = Path(
    "database/clickhouse/init/022_us_recorded_party_history_v2_repair.sql"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def schema_marker(client: Any) -> list[tuple[Any, ...]]:
    result = client.query(
        """
        SELECT component, version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'US_RECORDED_PARTY_HISTORY'
        """,
        settings={"max_threads": 1},
    )
    return list(result.result_rows)


def prepare_repair_plan(
    output_path: Path,
    *,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    target = client or clickhouse_client()
    root = repo_root or _repo_root()
    sql_path = root / SQL_RELATIVE_PATH
    current_marker = schema_marker(target)
    if current_marker != [("US_RECORDED_PARTY_HISTORY", PRE_SCHEMA_VERSION)]:
        raise RuntimeError(
            f"US recorded party repair requires V1 marker: {current_marker!r}"
        )
    if target_schema_state(target) != EXPECTED_SCHEMA:
        raise RuntimeError("US recorded party repair target schema drifted")
    if readiness_rows(target):
        raise RuntimeError(
            "US recorded party repair requires readiness to remain absent"
        )
    pre_target = target_stats(target)
    if int(pre_target["row_count"]) <= 0:
        raise RuntimeError(
            "US recorded party repair requires the failed unaccepted prefix"
        )
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha_getter().lower(),
        "sql_relative_path": SQL_RELATIVE_PATH.as_posix(),
        "sql_sha256": _file_sha256(sql_path),
        "source_versions": source_versions(target),
        "pre_schema_marker": PRE_SCHEMA_VERSION,
        "post_schema_marker": POST_SCHEMA_VERSION,
        "target_schema": EXPECTED_SCHEMA,
        "target_pre_state": pre_target,
        "readiness_pre_state": [],
        "mutation_scope": {
            "drop_only": [
                "markorbit_facts.us_assignment_recorded_party_relationship_mv",
                "markorbit_facts.us_ttab_recorded_party_relationship_mv",
            ],
            "truncate_only": [
                "markorbit_facts.us_recorded_party_relationship_event"
            ],
            "recreate_materialized_views": True,
            "schema_marker_insert_only": True,
            "source_tables_mutated": False,
            "readiness_written": False,
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    _write_json(output_path, envelope)
    return envelope


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    computed = _sha256(envelope["plan"])
    if (
        envelope.get("plan_sha256") != computed
        or computed != expected_sha.strip().lower()
    ):
        raise RuntimeError("US recorded party repair plan SHA mismatch")
    plan = dict(envelope["plan"])
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("US recorded party repair plan version is unsupported")
    return plan


def _split_repair_sql(sql: str) -> list[str]:
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    if len(statements) != 6:
        raise RuntimeError("US recorded party repair SQL statement count drifted")
    expected_prefixes = (
        "DROP TABLE IF EXISTS markorbit_facts.us_assignment_recorded_party_relationship_mv",
        "DROP TABLE IF EXISTS markorbit_facts.us_ttab_recorded_party_relationship_mv",
        "TRUNCATE TABLE markorbit_facts.us_recorded_party_relationship_event",
        "CREATE MATERIALIZED VIEW markorbit_facts.us_assignment_recorded_party_relationship_mv",
        "CREATE MATERIALIZED VIEW markorbit_facts.us_ttab_recorded_party_relationship_mv",
        "INSERT INTO markorbit_facts.schema_version",
    )
    for statement, prefix in zip(statements, expected_prefixes, strict=True):
        if not statement.startswith(prefix):
            raise RuntimeError(
                f"US recorded party repair SQL drifted at expected prefix: {prefix}"
            )
    upper = sql.upper()
    if "DELETE " in upper or "DROP DATABASE" in upper or "TRUNCATE DATABASE" in upper:
        raise RuntimeError("US recorded party repair SQL exceeded bounded scope")
    return statements


def validate_live_plan(
    plan: Mapping[str, Any],
    *,
    client: Any,
    main_sha_getter: Callable[[], str] = current_main_sha,
    repo_root: Path | None = None,
) -> None:
    root = repo_root or _repo_root()
    if main_sha_getter().lower() != str(plan["expected_main"]):
        raise RuntimeError("US recorded party repair main SHA drifted")
    sql_path = root / str(plan["sql_relative_path"])
    if _file_sha256(sql_path) != str(plan["sql_sha256"]):
        raise RuntimeError("US recorded party repair SQL drifted")
    if source_versions(client) != dict(plan["source_versions"]):
        raise RuntimeError("US recorded party repair source versions drifted")
    if target_schema_state(client) != dict(plan["target_schema"]):
        raise RuntimeError("US recorded party repair target schema drifted")
    marker = schema_marker(client)
    if marker != [("US_RECORDED_PARTY_HISTORY", PRE_SCHEMA_VERSION)]:
        raise RuntimeError(f"US recorded party repair marker drifted: {marker!r}")
    if readiness_rows(client):
        raise RuntimeError("US recorded party readiness unexpectedly exists")
    if target_stats(client) != dict(plan["target_pre_state"]):
        raise RuntimeError("US recorded party failed-prefix fingerprint drifted")


def apply_repair(
    plan_path: Path,
    *,
    plan_sha: str,
    authority_token: str,
    receipt_path: Path,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    expected = f"GO #741 US recorded party schema repair {plan_sha.lower()}"
    if authority_token.strip() != expected:
        raise PermissionError(
            "fresh exact US recorded party schema repair authority token is required"
        )
    target = client or clickhouse_client()
    validate_live_plan(
        plan,
        client=target,
        main_sha_getter=main_sha_getter,
        repo_root=repo_root,
    )
    root = repo_root or _repo_root()
    sql_path = root / str(plan["sql_relative_path"])
    try:
        for statement in _split_repair_sql(sql_path.read_text(encoding="utf-8")):
            target.command(statement)
        post_target = target_stats(target)
        marker = schema_marker(target)
        if post_target != {"row_count": 0, "row_hash_xor": 0}:
            raise RuntimeError(
                f"US recorded party repair target not empty: {post_target!r}"
            )
        if marker != [("US_RECORDED_PARTY_HISTORY", POST_SCHEMA_VERSION)]:
            raise RuntimeError(
                f"US recorded party repair marker mismatch: {marker!r}"
            )
        if target_schema_state(target) != EXPECTED_SCHEMA:
            raise RuntimeError("US recorded party repair schema post-state mismatch")
        if readiness_rows(target):
            raise RuntimeError("US recorded party repair wrote readiness unexpectedly")
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "implementation_sha": plan["expected_main"],
            "sql_sha256": plan["sql_sha256"],
            "pre_target": plan["target_pre_state"],
            "post_target": post_target,
            "schema_marker": POST_SCHEMA_VERSION,
        }
    except Exception as exc:
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "FAILED",
            "plan_sha256": plan_sha.lower(),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        _write_json(receipt_path, receipt)
        raise
    _write_json(receipt_path, receipt)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guarded US recorded-party V1-to-V2 schema repair"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--plan-sha", required=True)
    apply.add_argument("--authority-token", required=True)
    apply.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare_repair_plan(args.output)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = apply_repair(
        args.plan,
        plan_sha=args.plan_sha,
        authority_token=args.authority_token,
        receipt_path=args.receipt,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
