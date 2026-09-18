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
from app.db import clickhouse_client

PLAN_VERSION = "US_RECORDED_PARTY_SCHEMA_ACTIVATION_PLAN_V1"
RECEIPT_VERSION = "US_RECORDED_PARTY_SCHEMA_ACTIVATION_RECEIPT_V1"
SCHEMA_VERSION = "US_RECORDED_PARTY_HISTORY_SCHEMA_V1"
SQL_RELATIVE_PATH = Path("database/clickhouse/init/021_us_recorded_party_history.sql")
TARGET_OBJECTS = (
    "us_recorded_party_relationship_event",
    "us_assignment_recorded_party_relationship_mv",
    "us_ttab_recorded_party_relationship_mv",
    "us_recorded_party_history_readiness",
)
EXPECTED_SCHEMA = {
    "us_recorded_party_relationship_event": {
        "engine": "ReplacingMergeTree",
        "sorting_key": (
            "normalized_name, source_domain, relationship_type, serial_number, "
            "resource_id, party_key, relationship_observation_hash"
        ),
    },
    "us_assignment_recorded_party_relationship_mv": {
        "engine": "MaterializedView",
        "sorting_key": "",
    },
    "us_ttab_recorded_party_relationship_mv": {
        "engine": "MaterializedView",
        "sorting_key": "",
    },
    "us_recorded_party_history_readiness": {
        "engine": "ReplacingMergeTree",
        "sorting_key": "ready_version",
    },
}


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


def target_schema_state(client: Any) -> dict[str, dict[str, str]]:
    names = ", ".join("'" + name + "'" for name in TARGET_OBJECTS)
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
        str(name): {
            "engine": str(engine),
            "sorting_key": _sorting_key(str(key)),
        }
        for name, engine, key in rows
    }


def source_versions(client: Any) -> dict[str, str]:
    rows = _query_rows(
        client,
        """
        SELECT component, version
        FROM markorbit_facts.schema_version FINAL
        WHERE component IN ('US_ASSIGNMENT', 'US_TTAB')
        ORDER BY component
        """,
        settings={"max_threads": 1},
    )
    result = {str(component): str(version) for component, version in rows}
    if set(result) != {"US_ASSIGNMENT", "US_TTAB"}:
        raise RuntimeError("US Assignment/TTAB source schema versions are incomplete")
    return result


def schema_marker(client: Any) -> list[tuple[Any, ...]]:
    return _query_rows(
        client,
        """
        SELECT component, version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'US_RECORDED_PARTY_HISTORY'
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
    sql_path = root / SQL_RELATIVE_PATH
    pre_schema = target_schema_state(target)
    pre_marker = schema_marker(target)
    if pre_schema or pre_marker:
        raise RuntimeError(
            "US recorded party schema activation requires a clean missing-schema pre-state"
        )
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha_getter().lower(),
        "sql_relative_path": SQL_RELATIVE_PATH.as_posix(),
        "sql_sha256": _file_sha256(sql_path),
        "source_versions": source_versions(target),
        "pre_schema": pre_schema,
        "pre_marker": pre_marker,
        "expected_schema": EXPECTED_SCHEMA,
        "expected_schema_marker": SCHEMA_VERSION,
        "mutation_scope": {
            "create_if_not_exists_only": list(TARGET_OBJECTS),
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
    if (
        envelope.get("plan_sha256") != computed
        or computed != expected_sha.strip().lower()
    ):
        raise RuntimeError("US recorded party schema plan SHA mismatch")
    plan = dict(envelope["plan"])
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("US recorded party schema plan version is unsupported")
    return plan


def _split_sql(sql: str) -> list[str]:
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    if len(statements) != 5:
        raise RuntimeError("US recorded party schema SQL statement count drifted")
    upper = sql.upper()
    if any(token in upper for token in ("DROP ", "DELETE ", "TRUNCATE ", "ALTER ")):
        raise RuntimeError(
            "US recorded party schema SQL contains a destructive statement"
        )
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
        raise RuntimeError("US recorded party schema main SHA drifted")
    sql_path = root / str(plan["sql_relative_path"])
    if _file_sha256(sql_path) != str(plan["sql_sha256"]):
        raise RuntimeError("US recorded party schema SQL drifted")
    if source_versions(client) != dict(plan["source_versions"]):
        raise RuntimeError("US recorded party source schema versions drifted")
    if target_schema_state(client) != dict(plan["pre_schema"]):
        raise RuntimeError("US recorded party schema pre-state drifted")
    if schema_marker(client) != [tuple(row) for row in plan["pre_marker"]]:
        raise RuntimeError("US recorded party schema marker pre-state drifted")


def apply_schema(
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
    expected = f"GO #741 US recorded party schema activation {plan_sha.lower()}"
    if authority_token.strip() != expected:
        raise PermissionError(
            "fresh exact US recorded party schema authority token is required"
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
        for statement in _split_sql(sql_path.read_text(encoding="utf-8")):
            target.command(statement)
        actual_schema = target_schema_state(target)
        marker = schema_marker(target)
        if actual_schema != dict(plan["expected_schema"]):
            raise RuntimeError(
                f"US recorded party schema post-state mismatch: {actual_schema!r}"
            )
        if marker != [("US_RECORDED_PARTY_HISTORY", SCHEMA_VERSION)]:
            raise RuntimeError(
                f"US recorded party schema marker mismatch: {marker!r}"
            )
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
    parser = argparse.ArgumentParser(
        description="Guarded US recorded party schema production activation"
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
