from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from app.applicant_name_lookup import (
    APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
    CN_APPLICANT_NAME_LOOKUP_TABLE,
)
from app.cn.applicant_name_lookup_backfill import MAX_BATCH_SIZE
from app.cn.applicant_name_lookup_backfill_control import (
    BACKFILL_JOB_TYPE,
    CNApplicantServingEpoch,
    current_cn_applicant_serving_epoch,
    execute_backfill_run,
    start_backfill_run,
)
from app.us.target_canary import WslNativeClickHouseClient

PLAN_VERSION = "CN_APPLICANT_NAME_LOOKUP_BACKFILL_PLAN_V1"
RECEIPT_VERSION = "CN_APPLICANT_NAME_LOOKUP_BACKFILL_RECEIPT_V1"
EXPECTED_SORTING_KEY = "normalized_name, entity_id, application_number, relation_key"
PLAN_KEYS = {
    "version",
    "expected_main",
    "implementation_sha",
    "source_epoch",
    "batch_size",
    "target_schema",
    "mutation_scope",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def current_main_sha(repo_root: Path | None = None) -> str:
    root = repo_root or Path(__file__).resolve().parents[2]
    sha = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8")
        .strip()
        .lower()
    )
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=root, text=True, encoding="utf-8"
    ).strip()
    if not HEX40.fullmatch(sha) or branch != "main":
        raise RuntimeError("production CN Applicant backfill operator must run from local main")
    return sha


class TargetCNApplicantBackfillClient:
    def __init__(self, base: WslNativeClickHouseClient | None = None) -> None:
        self._base = base or WslNativeClickHouseClient()

    def query(self, sql: str, *, settings: Mapping[str, Any] | None = None):
        allowed = {"max_threads", "max_rows_to_read", "read_overflow_mode"}
        if settings and set(settings) - allowed:
            raise ValueError("unsupported CN Applicant backfill query settings")
        suffix = ""
        if settings:
            parts = []
            for key in sorted(settings):
                value = settings[key]
                if key == "read_overflow_mode":
                    if str(value) != "throw":
                        raise ValueError("read_overflow_mode must remain fail-closed")
                    parts.append("read_overflow_mode = 'throw'")
                else:
                    if int(value) < 1:
                        raise ValueError(f"{key} must be positive")
                    parts.append(f"{key} = {int(value)}")
            suffix = " SETTINGS " + ", ".join(parts)
        return self._base.query(sql.rstrip().rstrip(";") + suffix)

    def insert(
        self, table: str, rows: Sequence[Sequence[Any]], *, column_names: Sequence[str]
    ) -> None:
        if table != CN_APPLICANT_NAME_LOOKUP_TABLE:
            raise RuntimeError(
                f"CN Applicant backfill may insert only into {CN_APPLICANT_NAME_LOOKUP_TABLE}"
            )
        self._base.insert(table, rows, column_names=column_names)


def target_schema_state(client: Any) -> dict[str, object]:
    rows = list(
        client.query(
            """
        SELECT name, toString(uuid), engine, sorting_key
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name IN ('cn_case_party_current', 'cn_applicant_name_lookup_current', 'cn_applicant_name_lookup_from_case_party_mv')
        ORDER BY name
        """,
            settings={"max_threads": 1},
        ).result_rows
    )
    tables = {
        str(row[0]): {"uuid": str(row[1]), "engine": str(row[2]), "sorting_key": str(row[3])}
        for row in rows
    }
    if set(tables) != {
        "cn_case_party_current",
        "cn_applicant_name_lookup_current",
        "cn_applicant_name_lookup_from_case_party_mv",
    }:
        raise RuntimeError("CN Applicant lookup target tables are incomplete")
    lookup = tables["cn_applicant_name_lookup_current"]
    key = str(lookup["sorting_key"]).replace("`", "").replace("(", "").replace(")", "").strip()
    if lookup["engine"] != "ReplacingMergeTree" or key != EXPECTED_SORTING_KEY:
        raise RuntimeError("CN Applicant lookup schema does not match the accepted contract")
    if tables["cn_applicant_name_lookup_from_case_party_mv"]["engine"] != "MaterializedView":
        raise RuntimeError("CN Applicant lookup projection is missing")
    marker = list(
        client.query(
            "SELECT version FROM markorbit_facts.schema_version FINAL WHERE component = 'APPLICANT_NAME_LOOKUP' LIMIT 1",
            settings={"max_threads": 1},
        ).result_rows
    )
    if len(marker) != 1 or str(marker[0][0]) != APPLICANT_NAME_LOOKUP_SCHEMA_VERSION:
        raise RuntimeError("Applicant name lookup schema marker is missing or stale")
    return {"schema_marker": APPLICANT_NAME_LOOKUP_SCHEMA_VERSION, "tables": tables}


def prepare_backfill_plan(
    output_path: Path,
    *,
    batch_size: int = 5_000,
    client: Any | None = None,
    epoch_getter: Callable[[], CNApplicantServingEpoch] = current_cn_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, object]:
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    sha = main_sha_getter().lower()
    if not HEX40.fullmatch(sha):
        raise RuntimeError("CN Applicant backfill plan requires exact implementation SHA")
    plan = {
        "version": PLAN_VERSION,
        "expected_main": sha,
        "implementation_sha": sha,
        "source_epoch": epoch_getter().to_dict(),
        "batch_size": batch_size,
        "target_schema": target_schema_state(client or TargetCNApplicantBackfillClient()),
        "mutation_scope": {
            "table": CN_APPLICANT_NAME_LOOKUP_TABLE,
            "operation": "INSERT_ONLY",
            "control_job_type": BACKFILL_JOB_TYPE,
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return envelope


def load_backfill_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    expected_sha = expected_sha.strip().lower()
    if not HEX64.fullmatch(expected_sha):
        raise ValueError("plan SHA must be an exact 64-character SHA-256")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"plan", "plan_sha256"} or not isinstance(envelope["plan"], dict):
        raise RuntimeError("malformed CN Applicant backfill plan envelope")
    computed = _sha256(envelope["plan"])
    if envelope["plan_sha256"] != computed or computed != expected_sha:
        raise RuntimeError("CN Applicant backfill plan SHA-256 mismatch")
    plan = dict(envelope["plan"])
    if set(plan) != PLAN_KEYS or plan.get("version") != PLAN_VERSION:
        raise RuntimeError("unsupported or malformed CN Applicant backfill plan")
    return plan


def validate_live_plan(
    plan: Mapping[str, Any],
    *,
    client: Any,
    epoch_getter: Callable[[], CNApplicantServingEpoch],
    main_sha_getter: Callable[[], str],
) -> CNApplicantServingEpoch:
    sha = main_sha_getter().lower()
    if sha != plan.get("expected_main") or sha != plan.get("implementation_sha"):
        raise RuntimeError("CN Applicant backfill main SHA drifted from the frozen plan")
    epoch = epoch_getter()
    if epoch.to_dict() != dict(plan.get("source_epoch") or {}):
        raise RuntimeError("CN Applicant backfill serving epoch drifted from the frozen plan")
    if target_schema_state(client) != dict(plan.get("target_schema") or {}):
        raise RuntimeError(
            "CN Applicant backfill target schema identity drifted from the frozen plan"
        )
    expected_scope = {
        "table": CN_APPLICANT_NAME_LOOKUP_TABLE,
        "operation": "INSERT_ONLY",
        "control_job_type": BACKFILL_JOB_TYPE,
    }
    if dict(plan.get("mutation_scope") or {}) != expected_scope:
        raise RuntimeError("CN Applicant backfill mutation scope is malformed")
    return epoch


def execute_backfill_plan(
    plan_path: Path,
    *,
    plan_sha: str,
    receipt_path: Path,
    production_mutation_authorized: bool,
    resume_run_id: str | None = None,
    client: Any | None = None,
    epoch_getter: Callable[[], CNApplicantServingEpoch] = current_cn_applicant_serving_epoch,
    main_sha_getter: Callable[[], str] = current_main_sha,
    start_fn: Callable[..., str] = start_backfill_run,
    execute_fn: Callable[..., dict[str, Any]] = execute_backfill_run,
) -> dict[str, Any]:
    if production_mutation_authorized is not True:
        raise PermissionError("explicit production mutation authorization is required")
    run_id, plan = resume_run_id, {}
    try:
        plan = load_backfill_plan(plan_path, plan_sha)
        target = client or TargetCNApplicantBackfillClient()
        epoch = validate_live_plan(
            plan, client=target, epoch_getter=epoch_getter, main_sha_getter=main_sha_getter
        )
        if run_id is None:
            run_id = start_fn(
                epoch=epoch,
                implementation_sha=str(plan["implementation_sha"]),
                production_mutation_authorized=True,
            )
        result = execute_fn(
            run_id,
            client=target,
            serving_epoch_getter=epoch_getter,
            batch_size=int(plan["batch_size"]),
        )
        if dict(result["completeness"]).get("complete") is not True:
            raise RuntimeError("CN Applicant backfill returned incomplete evidence")
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "run_id": run_id,
            "implementation_sha": plan["implementation_sha"],
            "source_epoch": result["source_epoch"],
            "cursor": result["cursor"],
            "completeness": result["completeness"],
        }
    except Exception as exc:
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "FAILED",
            "plan_sha256": plan_sha.strip().lower(),
            "run_id": run_id,
            "implementation_sha": str(plan.get("implementation_sha") or ""),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        raise
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
