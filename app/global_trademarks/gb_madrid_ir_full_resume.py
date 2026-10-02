"""Governed Madrid-IR continuation after the accepted first-1,000-row pilot.

Plan/preflight are read-only. Apply resumes bounded batches from the persisted
checkpoint, binds the accepted E-backed PostgreSQL target, and never promotes
the 2018 historical source to current registry truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.global_trademarks import gb_domestic_full_resume as domestic
from app.global_trademarks import gb_historical_e_stage_reader as e_stage
from app.global_trademarks import gb_historical_source_row_pilot as source_rows
from app.global_trademarks import gb_madrid_ir_pilot as pilot
from app.global_trademarks import gb_pg_pilot_safety as safety

STREAM = "MADRID_IR"
START_CHECKPOINT = 1000
TARGET_SOURCE_ROWS = 109695
BATCH_SIZE = 5000
GOV = Path(r"D:\yoomarks\governed-plans\855")
PILOT_PLAN = GOV / "gb-madrid-ir-pg-pilot-plan-r1.json"
PILOT_PLAN_SHA = "aba7e59d7be1db268a00d396438539d513870685f1598964d88d3838ee6a6509"
PILOT_RECEIPT = GOV / "gb-madrid-ir-e-pg-pilot-r1.json"
PILOT_RECEIPT_SHA = "525daefbea682dcc56dd916d00bea89da1968194c4ce12d38f956c50b99bf788"
PILOT_OPERATOR_SHA = "0b34afe0c38a54808cf5ae8d193c977d3a12356a14fa7cbec826d340204c47fb"
RECEIPT = GOV / "gb-madrid-ir-e-full-resume-r1.json"
OPERATION_KIND = "GB_MADRID_IR_HISTORICAL_E_FULL_RESUME_V1"


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def _load_json(path: Path, expected_sha: str, label: str) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), f"{label} missing or symlinked")
    require(domestic.sha(path) == expected_sha, f"{label} SHA drift")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def verify_prior_acceptance() -> dict[str, Any]:
    plan = _load_json(PILOT_PLAN, PILOT_PLAN_SHA, "Madrid-IR pilot plan")
    receipt = _load_json(PILOT_RECEIPT, PILOT_RECEIPT_SHA, "Madrid-IR pilot receipt")
    require(
        plan.get("kind") == "GB_MADRID_IR_HISTORICAL_E_PILOT_PLAN_V1"
        and plan.get("operator_sha256") == PILOT_OPERATOR_SHA
        and plan.get("pilot_source_rows") == START_CHECKPOINT
        and plan.get("source_total_rows") == TARGET_SOURCE_ROWS
        and plan.get("historical_source_only") is True
        and plan.get("current_state_verified") is False
        and receipt.get("kind") == "GB_MADRID_IR_HISTORICAL_E_PG_PILOT_V1"
        and receipt.get("status") == "FIRST_1000_HISTORICAL_ROWS_COMMITTED_NOT_FULL_IMPORT"
        and receipt.get("plan_sha256") == PILOT_PLAN_SHA
        and receipt.get("checkpoint_source_row_ordinal") == START_CHECKPOINT
        and receipt.get("source_rows_committed") == START_CHECKPOINT
        and receipt.get("historical_source_only") is True
        and receipt.get("source_status_current_verified") is False
        and receipt.get("full_import_complete") is False,
        "accepted Madrid-IR pilot evidence drift",
    )
    return {"plan": plan, "receipt": receipt}


def source_summary(proof: dict[str, Any], limit: int) -> dict[str, Any]:
    require(START_CHECKPOINT <= limit <= TARGET_SOURCE_ROWS, "invalid Madrid-IR summary limit")
    digest = hashlib.sha256()
    accepted = 0
    quarantined = 0
    last = 0
    for row in source_rows.ordered_source_records(proof, STREAM):
        ordinal, kind, *_, row_sha = row
        if ordinal > limit:
            break
        last = ordinal
        accepted += kind == "ACCEPTED"
        quarantined += kind == "QUARANTINED"
        digest.update(f"{ordinal}:{kind}:{row_sha}\n".encode())
    require(last == limit, "Madrid-IR stage does not contain requested prefix")
    return {
        "source_rows": limit,
        "accepted_rows": accepted,
        "quarantined_rows": quarantined,
        "ordered_row_identity_sha256": digest.hexdigest(),
    }


def _query_state(cur: Any, source_sha: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT source_stream,source_member,stage_manifest_sha256,stage_rows_sha256,
               stage_quarantine_sha256,expected_source_rows,expected_accepted_rows,
               expected_quarantine_rows,checkpoint_source_ordinal,rows_committed,
               accepted_committed,quarantine_committed,status
        FROM trademark_gb.historical_source_ingest_run_v2
        WHERE source_archive_sha256=%s
        """,
        (source_sha,),
    )
    run = cur.fetchone()
    require(run is not None, "Madrid-IR ingest run missing")
    cur.execute(
        """
        SELECT count(*) AS total,
               min(source_row_ordinal) AS min_ordinal,
               max(source_row_ordinal) AS max_ordinal,
               count(*) FILTER (WHERE record_kind='ACCEPTED') AS accepted,
               count(*) FILTER (WHERE record_kind='QUARANTINED') AS quarantined,
               count(*) FILTER (WHERE current_state_verified) AS unapproved_current,
               count(*) FILTER (WHERE NOT historical_source_only) AS nonhistorical
        FROM trademark_gb.historical_source_row_v2
        WHERE source_archive_sha256=%s
        """,
        (source_sha,),
    )
    counts = dict(cur.fetchone())
    cur.execute(
        """
        SELECT source_row_ordinal,record_kind,source_row_sha256
        FROM trademark_gb.historical_source_row_v2
        WHERE source_archive_sha256=%s
        ORDER BY source_row_ordinal
        """,
        (source_sha,),
    )
    digest = hashlib.sha256()
    for expected, row in enumerate(cur.fetchall(), 1):
        require(row["source_row_ordinal"] == expected, "Madrid-IR live source ordinal drift")
        digest.update(f"{expected}:{row['record_kind']}:{row['source_row_sha256']}\n".encode())
    return {**dict(run), **counts, "ordered_row_identity_sha256": digest.hexdigest()}


def _validate_state(
    proof: dict[str, Any],
    state: dict[str, Any],
    *,
    allow_terminal_running: bool = False,
) -> dict[str, Any]:
    spec = proof["spec"]
    checkpoint = state["checkpoint_source_ordinal"]
    require(
        type(checkpoint) is int and START_CHECKPOINT <= checkpoint <= TARGET_SOURCE_ROWS,
        "Madrid-IR checkpoint outside governed resume range",
    )
    expected = source_summary(proof, checkpoint)
    expected_status = (
        "RUNNING" if checkpoint < TARGET_SOURCE_ROWS or allow_terminal_running else "COMPLETE"
    )
    require(
        state["source_stream"] == STREAM
        and state["source_member"] == spec["member"]
        and state["stage_manifest_sha256"] == spec["manifest_sha"]
        and state["stage_rows_sha256"] == proof["manifest"]["accepted_jsonl_sha256"]
        and state["stage_quarantine_sha256"] == proof["manifest"]["quarantine_jsonl_sha256"]
        and state["expected_source_rows"] == spec["total"] == TARGET_SOURCE_ROWS
        and state["expected_accepted_rows"] == spec["accepted"]
        and state["expected_quarantine_rows"] == spec["bad"]
        and state["rows_committed"] == checkpoint == state["total"]
        and state["accepted_committed"] == expected["accepted_rows"] == state["accepted"]
        and state["quarantine_committed"] == expected["quarantined_rows"] == state["quarantined"]
        and state["status"] == expected_status
        and state["min_ordinal"] == 1
        and state["max_ordinal"] == checkpoint
        and state["unapproved_current"] == 0
        and state["nonhistorical"] == 0
        and state["ordered_row_identity_sha256"] == expected["ordered_row_identity_sha256"],
        "Madrid-IR checkpoint/residency/currentness drift",
    )
    return state


def read_live_state(
    proof: dict[str, Any], *, allow_terminal_running: bool = False
) -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT current_database() AS database")
            require(cur.fetchone()["database"] == "markorbit", "Madrid-IR target must be markorbit")
            return _validate_state(
                proof,
                _query_state(cur, proof["spec"]["zip_sha"]),
                allow_terminal_running=allow_terminal_running,
            )


def verify_initial_checkpoint(proof: dict[str, Any]) -> dict[str, Any]:
    state = read_live_state(proof)
    require(
        state["checkpoint_source_ordinal"] == START_CHECKPOINT and state["status"] == "RUNNING",
        "Madrid-IR full plan requires exact accepted pilot checkpoint",
    )
    return state


def make_plan(
    proof: dict[str, Any],
    summary: dict[str, Any],
    *,
    postgres_topology: dict[str, Any],
    execution_main: str | None = None,
) -> dict[str, Any]:
    reserve = domestic.require_e_disk_reserve()["E"]
    execution_main = domestic.current_git_head() if execution_main is None else execution_main
    require(re.fullmatch(r"[0-9a-f]{40}", execution_main) is not None, "invalid main SHA")
    return {
        "kind": "GB_MADRID_IR_HISTORICAL_E_FULL_RESUME_PLAN_V1",
        "status": "FROZEN_NO_APPLY",
        "execution_main_sha": execution_main,
        "source_stream": STREAM,
        "source_zip_sha256": proof["spec"]["zip_sha"],
        "source_member": proof["spec"]["member"],
        "source_total_rows": proof["spec"]["total"],
        "source_expected_accepted_rows": proof["spec"]["accepted"],
        "source_expected_quarantine_rows": proof["spec"]["bad"],
        "start_checkpoint_source_ordinal": START_CHECKPOINT,
        "target_checkpoint_source_ordinal": TARGET_SOURCE_ROWS,
        "remaining_source_rows": TARGET_SOURCE_ROWS - START_CHECKPOINT,
        "batch_size": BATCH_SIZE,
        "full_ordered_row_identity_sha256": summary["ordered_row_identity_sha256"],
        "stage_manifest_sha256": proof["spec"]["manifest_sha"],
        "stage_rows_sha256": proof["manifest"]["accepted_jsonl_sha256"],
        "stage_quarantine_sha256": proof["manifest"]["quarantine_jsonl_sha256"],
        "structured_stage_root": proof["e_stage"]["root"],
        "structured_stage_drive": "E",
        "structured_stage_manifest_sha256": proof["e_stage"]["manifest_sha256"],
        "structured_stage_relocation_plan_sha256": proof["e_stage"]["relocation_plan_sha256"],
        "structured_stage_relocation_receipt_sha256": proof["e_stage"]["relocation_receipt_sha256"],
        "structured_stage_independent_audit_sha256": proof["e_stage"]["independent_audit_sha256"],
        "pilot_plan_sha256": PILOT_PLAN_SHA,
        "pilot_receipt_sha256": PILOT_RECEIPT_SHA,
        "pilot_operator_sha256": PILOT_OPERATOR_SHA,
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "postgres_topology": postgres_topology,
        "operator_sha256": domestic.canonical_text_sha(Path(__file__)),
        "schema_sql_sha256": hashlib.sha256(source_rows.SCHEMA_SQL.encode()).hexdigest(),
        "execution_evidence_schema_sha256": hashlib.sha256(
            safety.EXECUTION_EVIDENCE_SQL.encode()
        ).hexdigest(),
        "disk_reserve_gate": {"E": {"reserve_bytes": int(reserve["reserve_bytes"])}},
        "historical_source_only": True,
        "current_state_verified": False,
        "journal_ingest_authorized": False,
        "api_cutover_authorized": False,
        "clickhouse_cutover_authorized": False,
        "source_cleanup_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    require(
        plan.get("kind") == "GB_MADRID_IR_HISTORICAL_E_FULL_RESUME_PLAN_V1"
        and plan.get("status") == "FROZEN_NO_APPLY"
        and plan.get("source_stream") == STREAM
        and plan.get("source_zip_sha256") == source_rows.SOURCE_SPECS[STREAM]["zip_sha"]
        and plan.get("source_total_rows") == TARGET_SOURCE_ROWS
        and plan.get("source_expected_accepted_rows")
        == source_rows.SOURCE_SPECS[STREAM]["accepted"]
        and plan.get("source_expected_quarantine_rows") == source_rows.SOURCE_SPECS[STREAM]["bad"]
        and plan.get("start_checkpoint_source_ordinal") == START_CHECKPOINT
        and plan.get("target_checkpoint_source_ordinal") == TARGET_SOURCE_ROWS
        and plan.get("remaining_source_rows") == TARGET_SOURCE_ROWS - START_CHECKPOINT
        and plan.get("batch_size") == BATCH_SIZE
        and re.fullmatch(r"[0-9a-f]{64}", plan.get("full_ordered_row_identity_sha256", ""))
        is not None
        and plan.get("structured_stage_root") == str(e_stage.STAGE_ROOT)
        and plan.get("structured_stage_drive") == "E"
        and plan.get("structured_stage_manifest_sha256") == e_stage.STAGE_MANIFEST_SHA
        and plan.get("structured_stage_relocation_plan_sha256") == e_stage.RELOCATION_PLAN_SHA
        and plan.get("structured_stage_relocation_receipt_sha256") == e_stage.RELOCATION_RECEIPT_SHA
        and plan.get("structured_stage_independent_audit_sha256") == e_stage.INDEPENDENT_AUDIT_SHA
        and plan.get("pilot_plan_sha256") == PILOT_PLAN_SHA
        and plan.get("pilot_receipt_sha256") == PILOT_RECEIPT_SHA
        and plan.get("pilot_operator_sha256") == PILOT_OPERATOR_SHA
        and plan.get("target_database") == "markorbit"
        and plan.get("target_database_physical_drive") == "E"
        and plan.get("future_query_storage_placement") == "hot_global"
        and safety.validate_frozen_topology(plan.get("postgres_topology"))
        and plan.get("operator_sha256") == domestic.canonical_text_sha(Path(__file__))
        and plan.get("schema_sql_sha256")
        == hashlib.sha256(source_rows.SCHEMA_SQL.encode()).hexdigest()
        and plan.get("execution_evidence_schema_sha256")
        == hashlib.sha256(safety.EXECUTION_EVIDENCE_SQL.encode()).hexdigest()
        and set(plan.get("disk_reserve_gate", {})) == {"E"}
        and set(plan["disk_reserve_gate"]["E"]) == {"reserve_bytes"}
        and int(plan["disk_reserve_gate"]["E"]["reserve_bytes"]) > 0
        and plan.get("historical_source_only") is True
        and plan.get("current_state_verified") is False
        and plan.get("journal_ingest_authorized") is False
        and plan.get("api_cutover_authorized") is False
        and plan.get("clickhouse_cutover_authorized") is False
        and plan.get("source_cleanup_authorized") is False,
        "Madrid-IR full-resume frozen plan/operator mismatch",
    )
    expected = (
        f"GO #855 GB-MADRID-IR-FULL-RESUME {plan_sha} "
        f"CHECKPOINT-{START_CHECKPOINT}-TO-{TARGET_SOURCE_ROWS}"
    )
    require(token == expected, "exact GB Madrid-IR full-resume authority required")


def _receipt_payload(
    plan: dict[str, Any], plan_sha: str, reserve: dict[str, Any]
) -> dict[str, Any]:
    return {
        "kind": OPERATION_KIND,
        "status": "MADRID_IR_HISTORICAL_SOURCE_COMPLETE_NOT_CURRENT_REGISTER",
        "plan_sha256": plan_sha,
        "execution_main_sha": plan["execution_main_sha"],
        "source_zip_sha256": plan["source_zip_sha256"],
        "source_rows_committed": plan["source_total_rows"],
        "accepted_rows_committed": plan["source_expected_accepted_rows"],
        "quarantined_rows_committed": plan["source_expected_quarantine_rows"],
        "checkpoint_source_row_ordinal": plan["target_checkpoint_source_ordinal"],
        "full_ordered_row_identity_sha256": plan["full_ordered_row_identity_sha256"],
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "postgres_topology": plan["postgres_topology"],
        "structured_stage_root": str(e_stage.STAGE_ROOT),
        "future_query_storage_placement": "hot_global",
        "full_import_complete": True,
        "historical_source_only": True,
        "source_status_current_verified": False,
        "journal_ingest_authorized": False,
        "serving_cutover_authorized": False,
        "source_cleanup_authorized": False,
        "disk_reserve_apply_snapshot": reserve,
    }


def _reconcile_evidence(
    plan: dict[str, Any], plan_sha: str, operation_key: str
) -> dict[str, Any] | None:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            safety.verify_e_postgres_topology(conn, plan["postgres_topology"])
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (operation_key,))
            evidence = safety.read_execution_evidence(cur, operation_key)
            if evidence is None:
                conn.commit()
                return None
            receipt = safety.validate_execution_evidence(
                evidence,
                operation_key=operation_key,
                operation_kind=OPERATION_KIND,
                plan=plan,
                plan_sha=plan_sha,
            )
            conn.commit()
    return receipt


def _commit_batch(
    proof: dict[str, Any],
    plan: dict[str, Any],
    operation_key: str,
    batch: list[tuple[Any, ...]],
    *,
    prior_checkpoint: int,
    prior_accepted: int,
    prior_quarantined: int,
    checkpoint: int,
    accepted: int,
    quarantined: int,
) -> None:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            safety.verify_e_postgres_topology(conn, plan["postgres_topology"])
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (operation_key,))
            cur.executemany(source_rows.INSERT_SQL, batch)
            require(cur.rowcount == len(batch), "Madrid-IR full-resume duplicate/partial INSERT")
            cur.execute(
                """
                UPDATE trademark_gb.historical_source_ingest_run_v2
                SET checkpoint_source_ordinal=%s,rows_committed=%s,
                    accepted_committed=%s,quarantine_committed=%s,updated_at=now()
                WHERE source_archive_sha256=%s AND checkpoint_source_ordinal=%s
                  AND rows_committed=%s AND accepted_committed=%s
                  AND quarantine_committed=%s AND status='RUNNING'
                """,
                (
                    checkpoint,
                    checkpoint,
                    accepted,
                    quarantined,
                    proof["spec"]["zip_sha"],
                    prior_checkpoint,
                    prior_checkpoint,
                    prior_accepted,
                    prior_quarantined,
                ),
            )
            require(cur.rowcount == 1, "Madrid-IR full-resume checkpoint transition failed")
            conn.commit()


def _finalize(
    proof: dict[str, Any],
    plan: dict[str, Any],
    plan_sha: str,
    operation_key: str,
    payload: dict[str, Any],
) -> None:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            safety.verify_e_postgres_topology(conn, plan["postgres_topology"])
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (operation_key,))
            state = _validate_state(
                proof,
                _query_state(cur, proof["spec"]["zip_sha"]),
                allow_terminal_running=True,
            )
            require(state["status"] == "RUNNING", "Madrid-IR finalization state is not RUNNING")
            cur.execute(
                """
                UPDATE trademark_gb.historical_source_ingest_run_v2
                SET status='COMPLETE',completed_at=now(),updated_at=now()
                WHERE source_archive_sha256=%s AND status='RUNNING'
                  AND checkpoint_source_ordinal=%s AND rows_committed=%s
                  AND accepted_committed=%s AND quarantine_committed=%s
                """,
                (
                    proof["spec"]["zip_sha"],
                    TARGET_SOURCE_ROWS,
                    TARGET_SOURCE_ROWS,
                    proof["spec"]["accepted"],
                    proof["spec"]["bad"],
                ),
            )
            require(cur.rowcount == 1, "Madrid-IR completion transition failed")
            safety.insert_execution_evidence(
                cur,
                operation_key=operation_key,
                operation_kind=OPERATION_KIND,
                plan=plan,
                plan_sha=plan_sha,
                receipt=payload,
            )
            conn.commit()


def apply_full(proof: dict[str, Any], plan: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    domestic.require_live_clean_main(plan["execution_main_sha"])
    operation_key = f"gb-madrid-ir:{plan['source_zip_sha256']}:full-resume"
    committed = _reconcile_evidence(plan, plan_sha, operation_key)
    if committed is not None:
        state = read_live_state(proof)
        require(state["status"] == "COMPLETE", "Madrid-IR evidence exists without COMPLETE state")
        receipt_sha = safety.atomic_publish_receipt(
            RECEIPT, committed, repair_from_db_evidence=True
        )
        return {**committed, "receipt_path": str(RECEIPT), "receipt_sha256": receipt_sha}

    require(not RECEIPT.exists(), "receipt exists without committed Madrid-IR full evidence")
    reserve = domestic.verify_apply_disk_reserve(plan["disk_reserve_gate"])
    state = read_live_state(proof, allow_terminal_running=True)
    require(state["status"] == "RUNNING", "Madrid-IR full resume is not resumable")
    checkpoint = state["checkpoint_source_ordinal"]
    accepted = state["accepted_committed"]
    quarantined = state["quarantine_committed"]
    batch: list[tuple[Any, ...]] = []

    for row in source_rows.ordered_source_records(proof, STREAM):
        ordinal, kind = row[0], row[1]
        if ordinal <= checkpoint:
            continue
        batch.append(source_rows.pg_params(row, STREAM, proof))
        accepted += kind == "ACCEPTED"
        quarantined += kind == "QUARANTINED"
        if len(batch) == BATCH_SIZE or ordinal == TARGET_SOURCE_ROWS:
            prior = checkpoint
            prior_accepted = accepted - sum(item[4] == "ACCEPTED" for item in batch)
            prior_quarantined = quarantined - sum(item[4] == "QUARANTINED" for item in batch)
            checkpoint = ordinal
            _commit_batch(
                proof,
                plan,
                operation_key,
                batch,
                prior_checkpoint=prior,
                prior_accepted=prior_accepted,
                prior_quarantined=prior_quarantined,
                checkpoint=checkpoint,
                accepted=accepted,
                quarantined=quarantined,
            )
            batch = []
            print(
                "GB_MADRID_IR_FULL_PROGRESS "
                f"checkpoint={checkpoint} accepted={accepted} quarantined={quarantined}",
                flush=True,
            )

    require(
        checkpoint == TARGET_SOURCE_ROWS
        and accepted == proof["spec"]["accepted"]
        and quarantined == proof["spec"]["bad"],
        "Madrid-IR full-resume final source counts drift",
    )
    payload = _receipt_payload(plan, plan_sha, reserve)
    try:
        _finalize(proof, plan, plan_sha, operation_key, payload)
    except Exception as error:
        committed = _reconcile_evidence(plan, plan_sha, operation_key)
        if committed is None:
            raise error
        payload = committed
    final = read_live_state(proof)
    require(final["status"] == "COMPLETE", "Madrid-IR final COMPLETE verification failed")
    receipt_sha = safety.atomic_publish_receipt(RECEIPT, payload, repair_from_db_evidence=True)
    return {**payload, "receipt_path": str(RECEIPT), "receipt_sha256": receipt_sha}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--apply-full", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()

    execution_main = (
        domestic.require_live_clean_main()
        if args.freeze_plan is not None or args.apply_full
        else domestic.current_git_head()
    )
    verify_prior_acceptance()
    proof = e_stage.verify_e_stage(STREAM)
    summary = source_summary(proof, TARGET_SOURCE_ROWS)
    topology = pilot.live_postgres_topology()
    proposed = make_plan(
        proof,
        summary,
        postgres_topology=topology,
        execution_main=execution_main,
    )

    if args.preflight_only:
        verify_initial_checkpoint(proof)
        require(not args.plan and not args.authority_token, "preflight accepts no Apply arguments")
        print(json.dumps(proposed, ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        verify_initial_checkpoint(proof)
        require(
            not args.plan
            and not args.authority_token
            and args.freeze_plan.parent.resolve() == GOV.resolve(),
            "freeze plan must be immutable under GB governed plans",
        )
        plan_sha = safety.atomic_freeze_plan(args.freeze_plan, proposed)
        print("GB_MADRID_IR_FULL_PLAN_SHA256=" + plan_sha, flush=True)
        print("GB_MADRID_IR_FULL_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        return

    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and domestic.sha(args.plan) == args.plan_sha,
        "exact frozen GB Madrid-IR full-resume plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    require(plan == proposed, "Madrid-IR full-resume plan differs from live frozen state")
    authorize(plan, args.plan_sha, args.authority_token)
    result = apply_full(proof, plan, args.plan_sha)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
