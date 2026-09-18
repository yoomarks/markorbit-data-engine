from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.cn.entity_portfolio_activation_operator import (
    _query_rows,
    _sha256,
    _sorting_key,
    current_main_sha,
)
from app.cn.relationship_timeline import RELATIONSHIP_TIMELINE_READY_VERSION
from app.db import clickhouse_client

PLAN_VERSION = "CN_ENTITY_SCHEMA_ACTIVATION_PLAN_V1"
RECEIPT_VERSION = "CN_ENTITY_SCHEMA_ACTIVATION_RECEIPT_V1"
SCHEMA_VERSION = "CN_ENTITY_TRADEMARK_PORTFOLIO_SCHEMA_V1"
SQL_RELATIVE_PATH = Path("database/clickhouse/init/020_cn_entity_trademark_portfolio.sql")
ENTITY_OBJECTS = (
    "cn_entity_trademark_relationship_event",
    "cn_entity_trademark_relationship_event_mv",
    "cn_entity_trademark_portfolio_readiness",
)
EXPECTED_SCHEMA = {
    "cn_entity_trademark_relationship_event": {
        "engine": "ReplacingMergeTree",
        "sorting_key": "entity_id, role, application_number, relation_key, event_hash",
    },
    "cn_entity_trademark_relationship_event_mv": {
        "engine": "MaterializedView",
        "sorting_key": "",
    },
    "cn_entity_trademark_portfolio_readiness": {
        "engine": "ReplacingMergeTree",
        "sorting_key": "ready_version",
    },
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sql_path(repo_root: Path | None = None) -> Path:
    return (repo_root or _repo_root()) / SQL_RELATIVE_PATH


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def entity_schema_state(client: Any) -> dict[str, dict[str, str]]:
    names = ", ".join("'" + name + "'" for name in ENTITY_OBJECTS)
    rows = _query_rows(
        client,
        f"""
        SELECT name, engine, sorting_key
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name IN ({names})
        ORDER BY name
        """,
    )
    return {
        str(name): {"engine": str(engine), "sorting_key": _sorting_key(str(key))}
        for name, engine, key in rows
    }


def relationship_ready(client: Any) -> dict[str, str]:
    rows = _query_rows(
        client,
        """
        SELECT component, version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'CN_RELATIONSHIP_TIMELINE'
        LIMIT 1
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1 or str(rows[0][1]) != RELATIONSHIP_TIMELINE_READY_VERSION:
        raise RuntimeError("CN relationship timeline is not accepted READY_V2")
    return {"component": str(rows[0][0]), "version": str(rows[0][1])}


def entity_schema_marker(client: Any) -> list[tuple[Any, ...]]:
    return _query_rows(
        client,
        """
        SELECT component, version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'CN_ENTITY_TRADEMARK_PORTFOLIO'
        """,
        settings={"max_threads": 1},
    )


def prepare_schema_plan(
    output_path: Path,
    *,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    target = client or clickhouse_client()
    root = repo_root or _repo_root()
    sql_path = _sql_path(root)
    pre_schema = entity_schema_state(target)
    pre_marker = entity_schema_marker(target)
    if pre_schema or pre_marker:
        raise RuntimeError("CN entity schema activation requires a clean missing-schema pre-state")
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha_getter().lower(),
        "sql_relative_path": SQL_RELATIVE_PATH.as_posix(),
        "sql_sha256": _file_sha256(sql_path),
        "upstream_readiness": relationship_ready(target),
        "pre_schema": pre_schema,
        "pre_marker": pre_marker,
        "expected_schema": EXPECTED_SCHEMA,
        "expected_schema_marker": SCHEMA_VERSION,
        "mutation_scope": {
            "create_if_not_exists_only": list(ENTITY_OBJECTS),
            "schema_marker_insert_only": True,
            "destructive_operations": False,
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    _write_json(output_path, envelope)
    return envelope


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    computed = _sha256(envelope["plan"])
    if envelope.get("plan_sha256") != computed or computed != expected_sha.strip().lower():
        raise RuntimeError("CN entity schema plan SHA mismatch")
    plan = dict(envelope["plan"])
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("CN entity schema plan version is unsupported")
    return plan


def _split_sql(sql: str) -> list[str]:
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    if len(statements) != 4:
        raise RuntimeError("CN entity schema SQL statement count drifted")
    forbidden = ("DROP ", "DELETE ", "TRUNCATE ", "ALTER ")
    upper = sql.upper()
    if any(token in upper for token in forbidden):
        raise RuntimeError("CN entity schema SQL contains a destructive statement")
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
        raise RuntimeError("CN entity schema activation main SHA drifted")
    sql_path = root / str(plan["sql_relative_path"])
    if _file_sha256(sql_path) != str(plan["sql_sha256"]):
        raise RuntimeError("CN entity schema SQL drifted")
    if relationship_ready(client) != dict(plan["upstream_readiness"]):
        raise RuntimeError("CN relationship readiness drifted")
    if entity_schema_state(client) != dict(plan["pre_schema"]):
        raise RuntimeError("CN entity schema pre-state drifted")
    if entity_schema_marker(client) != [tuple(row) for row in plan["pre_marker"]]:
        raise RuntimeError("CN entity schema marker pre-state drifted")


def apply_schema(
    plan_path: Path,
    *,
    plan_sha: str,
    authority_token: str,
    receipt_path: Path,
    client: Any | None = None,
) -> dict[str, Any]:
    plan = load_plan(plan_path, plan_sha)
    expected = f"GO #741 CN entity schema activation {plan_sha.lower()}"
    if authority_token.strip() != expected:
        raise PermissionError("fresh exact CN entity schema authority token is required")
    target = client or clickhouse_client()
    validate_live_plan(plan, client=target)
    sql_path = _repo_root() / str(plan["sql_relative_path"])
    statements = _split_sql(sql_path.read_text(encoding="utf-8"))
    try:
        for statement in statements:
            target.command(statement)
        actual_schema = entity_schema_state(target)
        marker = entity_schema_marker(target)
        if actual_schema != dict(plan["expected_schema"]):
            raise RuntimeError(f"CN entity schema post-state mismatch: {actual_schema!r}")
        if marker != [("CN_ENTITY_TRADEMARK_PORTFOLIO", SCHEMA_VERSION)]:
            raise RuntimeError(f"CN entity schema marker mismatch: {marker!r}")
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "implementation_sha": plan["expected_main"],
            "sql_sha256": plan["sql_sha256"],
            "schema": actual_schema,
            "schema_marker": SCHEMA_VERSION,
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
    parser = argparse.ArgumentParser(description="Guarded CN entity schema production activation")
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
        result = prepare_schema_plan(args.output)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = apply_schema(
        args.plan,
        plan_sha=args.plan_sha,
        authority_token=args.authority_token,
        receipt_path=args.receipt,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
