from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.cn.entity_portfolio_activation_operator import (
    _query_rows,
    _sha256,
    current_main_sha,
)
from app.db import clickhouse_client
from app.us.recorded_party_activation_support import (
    _batch_action,
    _normalized_name_expr,
    _target_fingerprint_expr,
    _write_readiness,
    benchmark_exact_name,
    readiness_rows,
    source_stats,
    target_stats,
)
from app.us.recorded_party_history import READY_VERSION, READINESS_TABLE, SOURCE_TABLE
from app.us.recorded_party_schema_activation_operator import (
    EXPECTED_SCHEMA,
    SCHEMA_VERSION,
    source_versions,
    target_schema_state,
)

PLAN_VERSION = "US_RECORDED_PARTY_HISTORY_ACTIVATION_PLAN_V2"
PROGRESS_VERSION = "US_RECORDED_PARTY_HISTORY_ACTIVATION_PROGRESS_V2"
RECEIPT_VERSION = "US_RECORDED_PARTY_HISTORY_ACTIVATION_RECEIPT_V1"
DEFAULT_BATCH_SERIALS = 100_000
MAX_BATCH_SERIALS = 500_000
PREPARE_MIN_FREE_BYTES = 30 * 1024**3
APPLY_MIN_FREE_BYTES = 15 * 1024**3


@dataclass(frozen=True, slots=True)
class ActivationProgress:
    plan_sha256: str
    stage: str = "ASSIGNMENT"
    after_serial_number: str = ""
    serial_started: bool = False
    rows_verified: int = 0
    row_hash_xor: int = 0
    batches_verified: int = 0

    def __post_init__(self) -> None:
        if self.stage not in {"ASSIGNMENT", "TTAB", "COMPLETE"}:
            raise ValueError("US recorded party activation stage is invalid")
        if len(self.plan_sha256) != 64:
            raise ValueError("US recorded party activation plan SHA is invalid")


def free_disk_bytes(client: Any) -> int:
    rows = _query_rows(
        client,
        """
        SELECT free_space
        FROM system.disks
        WHERE name = 'default'
        LIMIT 1
        """,
        settings={"max_threads": 1},
    )
    if len(rows) != 1:
        raise RuntimeError("US recorded party disk capacity is unavailable")
    return int(rows[0][0] or 0)


def require_free_disk(
    client: Any,
    *,
    minimum_bytes: int,
    stage: str,
) -> int:
    free = free_disk_bytes(client)
    if free < int(minimum_bytes):
        raise RuntimeError(
            "US recorded party disk headroom is below the guarded minimum: "
            f"stage={stage} free_bytes={free} minimum_bytes={int(minimum_bytes)}"
        )
    return free


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def prepare_activation_plan(
    output_path: Path,
    *,
    batch_serials: int = DEFAULT_BATCH_SERIALS,
    client: Any | None = None,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> dict[str, Any]:
    if not 1 <= batch_serials <= MAX_BATCH_SERIALS:
        raise ValueError("batch serial count is outside accepted bounds")
    target = client or clickhouse_client()
    prepare_free_bytes = require_free_disk(
        target,
        minimum_bytes=PREPARE_MIN_FREE_BYTES,
        stage="PREPARE",
    )
    if target_schema_state(target) != EXPECTED_SCHEMA:
        raise RuntimeError("US recorded party target schema is incomplete")
    if readiness_rows(target):
        raise RuntimeError("US recorded party history is already accepted")
    current_target = target_stats(target)
    if current_target != {"row_count": 0, "row_hash_xor": 0}:
        raise RuntimeError(
            "US recorded party activation requires an empty target pre-state"
        )
    frozen_sources = source_stats(target)
    implementation_sha = main_sha_getter().lower()
    plan = {
        "version": PLAN_VERSION,
        "expected_main": implementation_sha,
        "implementation_sha": implementation_sha,
        "schema_version": SCHEMA_VERSION,
        "source_versions": source_versions(target),
        "source_stats": frozen_sources,
        "target_schema": target_schema_state(target),
        "target_pre_state": current_target,
        "batch_serials": batch_serials,
        "capacity_gate": {
            "prepare_min_free_bytes": PREPARE_MIN_FREE_BYTES,
            "apply_min_free_bytes": APPLY_MIN_FREE_BYTES,
            "prepare_observed_free_bytes": prepare_free_bytes,
        },
        "assignment_max_rank": frozen_sources[
            "us_assignment_property_history"
        ]["max_source_rank"],
        "ttab_max_rank": frozen_sources[
            "us_ttab_property_history"
        ]["max_source_rank"],
        "mutation_scope": {
            "insert_only_tables": [SOURCE_TABLE, READINESS_TABLE],
            "destructive_operations": False,
        },
    }
    envelope = {"plan": plan, "plan_sha256": _sha256(plan)}
    _write_json_atomic(output_path, envelope)
    return envelope


def load_plan(path: Path, expected_sha: str) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    computed = _sha256(envelope["plan"])
    if (
        envelope.get("plan_sha256") != computed
        or computed != expected_sha.strip().lower()
    ):
        raise RuntimeError("US recorded party V2 activation plan SHA mismatch")
    plan = dict(envelope["plan"])
    if plan.get("version") != PLAN_VERSION:
        raise RuntimeError("US recorded party V2 activation plan version is unsupported")
    return plan


def validate_live_plan(
    plan: Mapping[str, Any],
    *,
    client: Any,
    main_sha_getter: Callable[[], str] = current_main_sha,
) -> None:
    sha = main_sha_getter().lower()
    if sha != str(plan["expected_main"]) or sha != str(plan["implementation_sha"]):
        raise RuntimeError("US recorded party V2 activation main SHA drifted")
    if source_versions(client) != dict(plan["source_versions"]):
        raise RuntimeError("US recorded party source schema versions drifted")
    if target_schema_state(client) != dict(plan["target_schema"]):
        raise RuntimeError("US recorded party target schema drifted")
    if source_stats(client) != dict(plan["source_stats"]):
        raise RuntimeError("US recorded party frozen source snapshot drifted")
    if readiness_rows(client):
        raise RuntimeError("US recorded party readiness unexpectedly exists")
    capacity = dict(plan.get("capacity_gate") or {})
    if capacity.get("prepare_min_free_bytes") != PREPARE_MIN_FREE_BYTES:
        raise RuntimeError("US recorded party prepare disk gate drifted")
    if capacity.get("apply_min_free_bytes") != APPLY_MIN_FREE_BYTES:
        raise RuntimeError("US recorded party apply disk gate drifted")
    require_free_disk(
        client,
        minimum_bytes=APPLY_MIN_FREE_BYTES,
        stage="APPLY_START",
    )


def load_progress(
    path: Path,
    plan_sha256: str,
) -> ActivationProgress:
    if not path.exists():
        return ActivationProgress(plan_sha256=plan_sha256)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != PROGRESS_VERSION:
        raise RuntimeError("US recorded party V2 progress version is unsupported")
    progress = ActivationProgress(**dict(payload["progress"]))
    if progress.plan_sha256 != plan_sha256:
        raise RuntimeError("US recorded party V2 progress belongs to another plan")
    return progress


def save_progress(path: Path, progress: ActivationProgress) -> None:
    _write_json_atomic(
        path,
        {"version": PROGRESS_VERSION, "progress": asdict(progress)},
    )


def serial_boundary(
    client: Any,
    *,
    stage: str,
    after_serial: str,
    started: bool,
    max_rank: int,
    batch_serials: int,
) -> str | None:
    table = (
        "us_assignment_property_history"
        if stage == "ASSIGNMENT"
        else "us_ttab_property_history"
    )
    after = (
        f"AND serial_number > {_sql_text(after_serial)}"
        if started
        else ""
    )
    rows = _query_rows(
        client,
        f"""
        SELECT serial_number
        FROM markorbit_facts.{table}
        WHERE source_rank <= {int(max_rank)}
          {after}
        GROUP BY serial_number
        ORDER BY serial_number
        LIMIT {int(batch_serials)}
        """,
        settings={"max_threads": 1, "optimize_aggregation_in_order": 1},
    )
    if not rows:
        return None
    return _text(rows[-1][0])


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8").rstrip("\x00")
    return str(value or "")


def _sql_text(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _serial_range(
    *, after_serial: str, boundary_serial: str, started: bool
) -> str:
    clauses = [f"serial_number <= {_sql_text(boundary_serial)}"]
    if started:
        clauses.append(f"serial_number > {_sql_text(after_serial)}")
    return " AND ".join(clauses)


def assignment_projection(
    *,
    after_serial: str,
    boundary_serial: str,
    started: bool,
    max_rank: int,
) -> str:
    normalized = _normalized_name_expr("p.party_name")
    serial_range = _serial_range(
        after_serial=after_serial,
        boundary_serial=boundary_serial,
        started=started,
    )
    return f"""
        SELECT
            {normalized} AS normalized_name,
            'US_ASSIGNMENT' AS source_domain,
            p.relationship_type,
            p.party_key,
            p.party_name,
            '' AS party_side,
            '' AS source_role,
            prop.serial_number,
            prop.registration_number,
            'ASSIGNMENT' AS resource_type,
            prop.reel_frame_id AS resource_id,
            rec.recorded_date AS event_date,
            greatest(p.observed_at, prop.observed_at, rec.observed_at) AS observed_at,
            prop.source_file AS source_file,
            prop.source_package_id AS source_package_id,
            greatest(p.source_rank, prop.source_rank, rec.source_rank) AS source_rank,
            p.observation_key AS party_observation_key,
            prop.observation_key AS resource_observation_key,
            hex(SHA256(concat(
                'US_ASSIGNMENT|',
                toString(prop.source_package_id), '|',
                toString(p.party_key), '|',
                p.relationship_type, '|',
                prop.serial_number, '|',
                prop.reel_frame_id
            ))) AS relationship_observation_hash
        FROM markorbit_facts.us_assignment_property_history AS prop
        INNER JOIN
        (
            SELECT
                observation_key, party_key, reel_frame_id, party_name,
                source_package_id, source_rank, observed_at,
                'ASSIGNOR' AS relationship_type
            FROM markorbit_facts.us_assignment_assignor_history
            WHERE source_rank <= {int(max_rank)}
            UNION ALL
            SELECT
                observation_key, party_key, reel_frame_id, party_name,
                source_package_id, source_rank, observed_at,
                'ASSIGNEE' AS relationship_type
            FROM markorbit_facts.us_assignment_assignee_history
            WHERE source_rank <= {int(max_rank)}
        ) AS p
            ON p.reel_frame_id = prop.reel_frame_id
           AND p.source_package_id = prop.source_package_id
        INNER JOIN markorbit_facts.us_assignment_record_history AS rec
            ON rec.reel_frame_id = prop.reel_frame_id
           AND rec.source_package_id = prop.source_package_id
           AND rec.source_rank <= {int(max_rank)}
        WHERE prop.source_rank <= {int(max_rank)}
          AND {serial_range}
          AND {normalized} != ''
    """


def ttab_projection(
    *,
    after_serial: str,
    boundary_serial: str,
    started: bool,
    max_rank: int,
) -> str:
    normalized = _normalized_name_expr("party.party_name")
    serial_range = _serial_range(
        after_serial=after_serial,
        boundary_serial=boundary_serial,
        started=started,
    )
    relationship = """
        multiIf(
            proceeding.proceeding_type_code = 'OPP' AND party.side = 'PLAINTIFF',
                'OPPOSITION_PLAINTIFF',
            proceeding.proceeding_type_code = 'OPP' AND party.side = 'DEFENDANT',
                'OPPOSITION_DEFENDANT',
            proceeding.proceeding_type_code = 'CAN' AND party.side = 'PLAINTIFF',
                'CANCELLATION_PETITIONER',
            proceeding.proceeding_type_code = 'CAN' AND party.side = 'DEFENDANT',
                'CANCELLATION_RESPONDENT',
            proceeding.proceeding_type_code = 'EXA',
                'EX_PARTE_APPEAL_PARTY',
            'TTAB_PARTY'
        )
    """.strip()
    return f"""
        SELECT
            {normalized} AS normalized_name,
            'US_TTAB' AS source_domain,
            {relationship} AS relationship_type,
            party.party_key,
            party.party_name,
            party.side AS party_side,
            party.role AS source_role,
            prop.serial_number,
            prop.registration_number,
            'PROCEEDING' AS resource_type,
            prop.proceeding_number AS resource_id,
            proceeding.filing_date AS event_date,
            greatest(
                party.observed_at,
                prop.observed_at,
                proceeding.observed_at
            ) AS observed_at,
            prop.source_file AS source_file,
            prop.source_package_id AS source_package_id,
            greatest(
                party.source_rank,
                prop.source_rank,
                proceeding.source_rank
            ) AS source_rank,
            party.observation_key AS party_observation_key,
            prop.observation_key AS resource_observation_key,
            hex(SHA256(concat(
                'US_TTAB|',
                toString(prop.source_package_id), '|',
                toString(party.party_key), '|',
                relationship_type, '|',
                prop.serial_number, '|',
                prop.proceeding_number
            ))) AS relationship_observation_hash
        FROM markorbit_facts.us_ttab_property_history AS prop
        INNER JOIN markorbit_facts.us_ttab_party_history AS party
            ON party.proceeding_number = prop.proceeding_number
           AND party.source_package_id = prop.source_package_id
           AND party.side = prop.party_side
           AND party.ordinal = prop.party_ordinal
           AND party.source_rank <= {int(max_rank)}
        INNER JOIN markorbit_facts.us_ttab_proceeding_history AS proceeding
            ON proceeding.proceeding_number = prop.proceeding_number
           AND proceeding.source_package_id = prop.source_package_id
           AND proceeding.source_rank <= {int(max_rank)}
        WHERE prop.source_rank <= {int(max_rank)}
          AND {serial_range}
          AND {normalized} != ''
    """


def expected_batch_stats(
    client: Any,
    *,
    stage: str,
    after_serial: str,
    boundary_serial: str,
    started: bool,
    max_rank: int,
) -> dict[str, int]:
    projection = (
        assignment_projection(
            after_serial=after_serial,
            boundary_serial=boundary_serial,
            started=started,
            max_rank=max_rank,
        )
        if stage == "ASSIGNMENT"
        else ttab_projection(
            after_serial=after_serial,
            boundary_serial=boundary_serial,
            started=started,
            max_rank=max_rank,
        )
    )
    rows = _query_rows(
        client,
        f"""
        SELECT count(), groupBitXor({_target_fingerprint_expr()})
        FROM ({projection})
        """,
        settings={"max_threads": 4},
    )
    if len(rows) != 1:
        raise RuntimeError("US recorded party V2 expected batch statistics failed")
    return {
        "row_count": int(rows[0][0] or 0),
        "row_hash_xor": int(rows[0][1] or 0),
    }


def actual_batch_stats(
    client: Any,
    *,
    stage: str,
    after_serial: str,
    boundary_serial: str,
    started: bool,
    max_rank: int,
) -> dict[str, int]:
    domain = "US_ASSIGNMENT" if stage == "ASSIGNMENT" else "US_TTAB"
    serial_range = _serial_range(
        after_serial=after_serial,
        boundary_serial=boundary_serial,
        started=started,
    )
    rows = _query_rows(
        client,
        f"""
        SELECT count(), groupBitXor({_target_fingerprint_expr()})
        FROM {SOURCE_TABLE} FINAL
        WHERE source_domain = '{domain}'
          AND source_rank <= {int(max_rank)}
          AND {serial_range}
        """,
        settings={"max_threads": 4},
    )
    if len(rows) != 1:
        raise RuntimeError("US recorded party V2 actual batch statistics failed")
    return {
        "row_count": int(rows[0][0] or 0),
        "row_hash_xor": int(rows[0][1] or 0),
    }


def insert_batch(
    client: Any,
    *,
    stage: str,
    after_serial: str,
    boundary_serial: str,
    started: bool,
    max_rank: int,
) -> None:
    projection = (
        assignment_projection(
            after_serial=after_serial,
            boundary_serial=boundary_serial,
            started=started,
            max_rank=max_rank,
        )
        if stage == "ASSIGNMENT"
        else ttab_projection(
            after_serial=after_serial,
            boundary_serial=boundary_serial,
            started=started,
            max_rank=max_rank,
        )
    )
    client.command(f"INSERT INTO {SOURCE_TABLE} {projection}")


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
    expected_token = (
        f"GO #741 US recorded party history activation {plan_sha.lower()}"
    )
    if authority_token.strip() != expected_token:
        raise PermissionError(
            "fresh exact US recorded party history authority token is required"
        )
    target = client or clickhouse_client()
    validate_live_plan(
        plan,
        client=target,
        main_sha_getter=main_sha_getter,
    )
    progress = load_progress(progress_path, plan_sha.lower())
    batch_serials = int(plan["batch_serials"])
    try:
        while progress.stage != "COMPLETE":
            stage = progress.stage
            max_rank = int(
                plan[
                    "assignment_max_rank"
                    if stage == "ASSIGNMENT"
                    else "ttab_max_rank"
                ]
            )
            boundary = serial_boundary(
                target,
                stage=stage,
                after_serial=progress.after_serial_number,
                started=progress.serial_started,
                max_rank=max_rank,
                batch_serials=batch_serials,
            )
            if boundary is None:
                if stage == "ASSIGNMENT":
                    progress = ActivationProgress(
                        plan_sha256=progress.plan_sha256,
                        stage="TTAB",
                        after_serial_number="",
                        serial_started=False,
                        rows_verified=progress.rows_verified,
                        row_hash_xor=progress.row_hash_xor,
                        batches_verified=progress.batches_verified,
                    )
                else:
                    progress = ActivationProgress(
                        plan_sha256=progress.plan_sha256,
                        stage="COMPLETE",
                        after_serial_number=progress.after_serial_number,
                        serial_started=progress.serial_started,
                        rows_verified=progress.rows_verified,
                        row_hash_xor=progress.row_hash_xor,
                        batches_verified=progress.batches_verified,
                    )
                save_progress(progress_path, progress)
                continue

            expected = expected_batch_stats(
                target,
                stage=stage,
                after_serial=progress.after_serial_number,
                boundary_serial=boundary,
                started=progress.serial_started,
                max_rank=max_rank,
            )
            actual = actual_batch_stats(
                target,
                stage=stage,
                after_serial=progress.after_serial_number,
                boundary_serial=boundary,
                started=progress.serial_started,
                max_rank=max_rank,
            )
            action = _batch_action(expected, actual)
            if action == "MISMATCH":
                raise RuntimeError(
                    f"US recorded party {stage} batch pre-state mismatch: "
                    f"expected={expected!r} actual={actual!r}"
                )
            if action == "INSERT":
                require_free_disk(
                    target,
                    minimum_bytes=APPLY_MIN_FREE_BYTES,
                    stage=f"{stage}_BEFORE_INSERT",
                )
                insert_batch(
                    target,
                    stage=stage,
                    after_serial=progress.after_serial_number,
                    boundary_serial=boundary,
                    started=progress.serial_started,
                    max_rank=max_rank,
                )
                actual = actual_batch_stats(
                    target,
                    stage=stage,
                    after_serial=progress.after_serial_number,
                    boundary_serial=boundary,
                    started=progress.serial_started,
                    max_rank=max_rank,
                )
                if dict(actual) != dict(expected):
                    raise RuntimeError(
                        f"US recorded party {stage} batch completeness mismatch: "
                        f"expected={expected!r} actual={actual!r}"
                    )
            progress = ActivationProgress(
                plan_sha256=progress.plan_sha256,
                stage=stage,
                after_serial_number=boundary,
                serial_started=True,
                rows_verified=progress.rows_verified + expected["row_count"],
                row_hash_xor=progress.row_hash_xor ^ expected["row_hash_xor"],
                batches_verified=progress.batches_verified + 1,
            )
            save_progress(progress_path, progress)

        validate_live_plan(
            plan,
            client=target,
            main_sha_getter=main_sha_getter,
        )
        actual_total = target_stats(target)
        expected_total = {
            "row_count": progress.rows_verified,
            "row_hash_xor": progress.row_hash_xor,
        }
        if actual_total != expected_total:
            raise RuntimeError(
                "US recorded party V2 final completeness mismatch: "
                f"expected={expected_total!r} actual={actual_total!r}"
            )
        benchmark = benchmark_exact_name(target)
        implementation_sha = str(plan["implementation_sha"])
        _write_readiness(
            target,
            plan_sha256=plan_sha.lower(),
            implementation_sha=implementation_sha,
            assignment_max_rank=int(plan["assignment_max_rank"]),
            ttab_max_rank=int(plan["ttab_max_rank"]),
        )
        receipt = {
            "version": RECEIPT_VERSION,
            "status": "SUCCESS",
            "plan_sha256": plan_sha.lower(),
            "implementation_sha": implementation_sha,
            "source_stats": plan["source_stats"],
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serial-bounded US recorded party history activation"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument(
        "--batch-serials",
        type=int,
        default=DEFAULT_BATCH_SERIALS,
    )
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
            batch_serials=args.batch_serials,
        )
        print(
            json.dumps(
                {"plan_path": str(args.output), **result},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    result = execute_activation_plan(
        args.plan,
        plan_sha=args.plan_sha,
        authority_token=args.authority_token,
        progress_path=args.progress,
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
