from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from app.us.applicant_candidate_backfill import MAX_BATCH_SIZE
from app.us.applicant_candidate_backfill_control import (
    USApplicantServingEpoch,
    current_us_applicant_serving_epoch,
    execute_backfill_run,
    start_backfill_run,
)
from app.us.applicant_candidate_index import (
    APPLICANT_INDEX_SCHEMA_VERSION,
    APPLICANT_INDEX_TABLE,
)
from app.us.target_canary import (
    TARGET_DATABASE,
    TARGET_DISTRO,
    TARGET_HTTP_PORT,
    TARGET_NATIVE_HOST,
    WslNativeClickHouseClient,
)

PLAN_VERSION = "US_APPLICANT_CANDIDATE_BACKFILL_PLAN_V1"
RECEIPT_VERSION = "US_APPLICANT_CANDIDATE_BACKFILL_RECEIPT_V1"
OWNER_TABLE = "markorbit_facts.us_owner_current"
_EXPECTED_SORTING_KEY = "candidate_key, serial_number, owner_key"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_QUERY_SETTINGS = {
    "max_threads",
    "max_rows_to_read",
    "read_overflow_mode",
}
_PLAN_KEYS = {
    "version",
    "expected_main",
    "implementation_sha",
    "source_epoch",
    "batch_size",
    "target_schema",
    "mutation_scope",
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


def current_main_sha(repo_root: Path | None = None) -> str:
    root = repo_root or _repo_root()
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        encoding="utf-8",
    ).strip().lower()
    if not _HEX40.fullmatch(sha):
        raise RuntimeError("unable to resolve exact 40-character git HEAD")
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"],
        cwd=root,
        text=True,
        encoding="utf-8",
    ).strip()
    if branch != "main":
        raise RuntimeError(
            "production Applicant backfill operator must run from local main"
        )
    return sha


def _settings_sql(settings: Mapping[str, Any] | None) -> str:
    if not settings:
        return ""
    unknown = set(settings) - _ALLOWED_QUERY_SETTINGS
    if unknown:
        raise ValueError(
            f"unsupported Applicant backfill query settings: {sorted(unknown)}"
        )
    parts: list[str] = []
    for key in sorted(settings):
        value = settings[key]
        if key in {"max_threads", "max_rows_to_read"}:
            number = int(value)
            if number < 1:
                raise ValueError(f"{key} must be positive")
            parts.append(f"{key} = {number}")
        elif key == "read_overflow_mode":
            if str(value) != "throw":
                raise ValueError(
                    "read_overflow_mode must remain fail-closed at 'throw'"
                )
            parts.append("read_overflow_mode = 'throw'")
    return " SETTINGS " + ", ".join(parts)


class TargetApplicantBackfillClient:
    """Narrow adapter over the accepted production target transport."""

    def __init__(self, base: WslNativeClickHouseClient | None = None) -> None:
        self._base = base or WslNativeClickHouseClient()

    def query(
        self,
        sql: str,
        *,
        settings: Mapping[str, Any] | None = None,
    ):
        statement = sql.rstrip().rstrip(";") + _settings_sql(settings)
        return self._base.query(statement)

    def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        *,
        column_names: Sequence[str],
    ) -> None:
        if table != APPLICANT_INDEX_TABLE:
            raise RuntimeError(
                f"Applicant backfill may insert only into {APPLICANT_INDEX_TABLE}"
            )
        self._base.insert(table, rows, column_names=column_names)


def _rows(client: Any, sql: str) -> list[list[Any]]:
    result = client.query(sql, settings={"max_threads": 1})
    return list(result.result_rows)


def target_schema_state(client: Any) -> dict[str, object]:
    tables = _rows(
        client,
        """
        SELECT name, toString(uuid), engine, sorting_key
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name IN ('us_owner_current', 'us_applicant_candidate_current')
        ORDER BY name
        """,
    )
    by_name = {
        str(row[0]): {
            "uuid": str(row[1]),
            "engine": str(row[2]),
            "sorting_key": str(row[3]),
        }
        for row in tables
    }
    if set(by_name) != {"us_owner_current", "us_applicant_candidate_current"}:
        raise RuntimeError("US Applicant backfill target tables are incomplete")
    index = by_name["us_applicant_candidate_current"]
    normalized_key = (
        str(index["sorting_key"])
        .replace("`", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )
    if (
        index["engine"] != "ReplacingMergeTree"
        or normalized_key != _EXPECTED_SORTING_KEY
    ):
        raise RuntimeError(
            "US Applicant index schema does not match the accepted contract"
        )
    marker_rows = _rows(
        client,
        """
        SELECT version
        FROM markorbit_facts.schema_version FINAL
        WHERE component = 'US_OWNER_READ'
        LIMIT 1
        """,
    )
    if (
        len(marker_rows) != 1
        or str(marker_rows[0][0]) != APPLICANT_INDEX_SCHEMA_VERSION
    ):
        raise RuntimeError("US Applicant owner-read schema marker is missing or stale")
    return {
        "target": {
            "distro": TARGET_DISTRO,
            "host": TARGET_NATIVE_HOST,
            "http_port": TARGET_HTTP_PORT,
            "database": TARGET_DATABASE,
        },
        "schema_marker": APPLICANT_INDEX_SCHEMA_VERSION,
        "owner_table": by_name["us_owner_current"],
        "candidate_table": index,
    }


def _validate_batch_size(batch_size: int) -> int:
    batch_size = int(batch_size)
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(
            f"batch_size must be between 1 and {MAX_BATCH_SIZE}"
        )
    return batch_size


def prepare_backfill_plan(
    output_path: Path,
    *,
    batch_size: int = 5_000,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, object]:
    batch_size = _validate_batch_size(batch_size)
    epoch = epoch_getter()
    main_sha = main_sha_getter().lower()
    if not _HEX40.fullmatch(main_sha):
        raise RuntimeError("Applicant backfill plan requires exact implementation SHA")
    target_client = client or TargetApplicantBackfillClient()
    plan = {
        "version": PLAN_VERSION,
        "expected_main": main_sha,
        "implementation_sha": main_sha,
        "source_epoch": epoch.to_dict(),
        "batch_size": batch_size,
        "target_schema": target_schema_state(target_client),
        "mutation_scope": {
            "table": APPLICANT_INDEX_TABLE,
            "operation": "INSERT_ONLY",
            "control_job_type": "US_APPLICANT_CANDIDATE_BACKFILL_V1",
        },
    }
    envelope = {
        "plan": plan,
        "plan_sha256": _sha256(plan),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return envelope


def load_backfill_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    expected_sha = expected_sha.strip().lower()
    if not _HEX64.fullmatch(expected_sha):
        raise ValueError("--plan-sha must be an exact 64-character SHA-256")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(envelope) != {"plan", "plan_sha256"}
        or not isinstance(envelope["plan"], dict)
    ):
        raise RuntimeError("malformed Applicant backfill plan envelope")
    computed = _sha256(envelope["plan"])
    if envelope["plan_sha256"] != computed or computed != expected_sha:
        raise RuntimeError("Applicant backfill plan SHA-256 mismatch")
    plan = dict(envelope["plan"])
    if set(plan) != _PLAN_KEYS:
        raise RuntimeError("malformed Applicant backfill plan keys")
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("unsupported Applicant backfill plan version")
    return plan


def validate_live_plan(
    plan: Mapping[str, Any],
    *,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> USApplicantServingEpoch:
    main_sha = main_sha_getter().lower()
    if main_sha != str(plan.get("expected_main") or ""):
        raise RuntimeError(
            "Applicant backfill main SHA drifted from the frozen plan"
        )
    if str(plan.get("implementation_sha") or "") != main_sha:
        raise RuntimeError(
            "Applicant backfill implementation SHA does not match current main"
        )
    epoch = epoch_getter()
    frozen_epoch = dict(plan.get("source_epoch") or {})
    if epoch.to_dict() != frozen_epoch:
        raise RuntimeError(
            "Applicant backfill serving epoch drifted from the frozen plan"
        )
    target_client = client or TargetApplicantBackfillClient()
    if target_schema_state(target_client) != dict(plan.get("target_schema") or {}):
        raise RuntimeError(
            "Applicant backfill target schema identity drifted from the frozen plan"
        )
    _validate_batch_size(int(plan.get("batch_size") or 0))
    mutation_scope = dict(plan.get("mutation_scope") or {})
    if mutation_scope != {
        "table": APPLICANT_INDEX_TABLE,
        "operation": "INSERT_ONLY",
        "control_job_type": "US_APPLICANT_CANDIDATE_BACKFILL_V1",
    }:
        raise RuntimeError("Applicant backfill mutation scope is malformed")
    return epoch


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def execute_backfill_plan(
    plan_path: Path,
    *,
    plan_sha: str,
    receipt_path: Path,
    production_mutation_authorized: bool,
    resume_run_id: str | None = None,
    client: Any | None = None,
    epoch_getter: Callable[[], USApplicantServingEpoch] = current_us_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
    start_fn: Callable[..., str] = start_backfill_run,
    execute_fn: Callable[..., dict[str, Any]] = execute_backfill_run,
) -> dict[str, Any]:
    if production_mutation_authorized is not True:
        raise PermissionError(
            "explicit production mutation authorization is required"
        )
    run_id = resume_run_id
    plan: dict[str, Any] = {}
    try:
        plan = load_backfill_plan(plan_path, plan_sha)
        target_client = client or TargetApplicantBackfillClient()
        epoch = validate_live_plan(
            plan,
            client=target_client,
            epoch_getter=epoch_getter,
            main_sha_getter=main_sha_getter,
        )
        if run_id is None:
            run_id = start_fn(
                epoch=epoch,
                implementation_sha=str(plan["implementation_sha"]),
                production_mutation_authorized=True,
            )
        result = execute_fn(
            run_id,
            client=target_client,
            serving_epoch_getter=epoch_getter,
            batch_size=int(plan["batch_size"]),
        )
        completeness = dict(result["completeness"])
        if completeness.get("complete") is not True:
            raise RuntimeError(
                "Applicant backfill returned a non-complete completeness receipt"
            )
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "run_id": run_id,
            "implementation_sha": str(plan["implementation_sha"]),
            "source_epoch": result["source_epoch"],
            "cursor": result["cursor"],
            "completeness": completeness,
        }
        _write_receipt(receipt_path, receipt)
        return receipt
    except Exception as exc:
        failure = {
            "version": RECEIPT_VERSION,
            "status": "FAILED",
            "plan_sha256": plan_sha.strip().lower(),
            "run_id": run_id,
            "implementation_sha": str(plan.get("implementation_sha") or ""),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        _write_receipt(receipt_path, failure)
        raise


def _default_output(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _repo_root() / "reports" / f"{prefix}_{stamp}.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exact-plan gated US Applicant candidate backfill"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path)
    prepare.add_argument("--batch-size", type=int, default=5_000)

    execute = sub.add_parser("execute")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--plan-sha", required=True)
    execute.add_argument("--receipt", type=Path)
    execute.add_argument(
        "--authorize-production-mutation",
        action="store_true",
    )

    resume = sub.add_parser("resume")
    resume.add_argument("--plan", type=Path, required=True)
    resume.add_argument("--plan-sha", required=True)
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--receipt", type=Path)
    resume.add_argument(
        "--authorize-production-mutation",
        action="store_true",
    )
    args = parser.parse_args(argv)

    if args.command == "prepare":
        path = args.output or _default_output(
            "production_us_applicant_candidate_backfill_plan"
        )
        envelope = prepare_backfill_plan(
            path,
            batch_size=args.batch_size,
        )
        print(
            json.dumps(
                {"plan_path": str(path), **envelope},
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    receipt = args.receipt or _default_output(
        "production_us_applicant_candidate_backfill_receipt"
    )
    result = execute_backfill_plan(
        args.plan,
        plan_sha=args.plan_sha,
        receipt_path=receipt,
        production_mutation_authorized=bool(
            args.authorize_production_mutation
        ),
        resume_run_id=(
            args.run_id if args.command == "resume" else None
        ),
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
