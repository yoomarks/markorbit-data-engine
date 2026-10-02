"""Governed first-1,000-row Madrid-IR admission from the accepted E stage.

Preflight is read-only. Plan freeze and Apply require clean live origin/main.
Apply is an additive PostgreSQL pilot only; it never promotes 2018 source
status to current registry truth or authorizes journal/serving work.
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

STREAM = "MADRID_IR"
PILOT_SOURCE_ROWS = 1000
GOV = Path(r"D:\yoomarks\governed-plans\855")
RECEIPT = GOV / "gb-madrid-ir-e-pg-pilot-r1.json"


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def prefix_summary(proof: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256()
    accepted = 0
    quarantined = 0
    last = 0
    for row in source_rows.ordered_source_records(proof, STREAM):
        ordinal, kind, *_, row_sha = row
        if ordinal > PILOT_SOURCE_ROWS:
            break
        last = ordinal
        accepted += kind == "ACCEPTED"
        quarantined += kind == "QUARANTINED"
        digest.update(f"{ordinal}:{kind}:{row_sha}\n".encode())
    require(last == PILOT_SOURCE_ROWS, "Madrid-IR stage does not contain exact pilot prefix")
    require(
        accepted + quarantined == PILOT_SOURCE_ROWS,
        "Madrid-IR pilot prefix count drift",
    )
    return {
        "source_rows": PILOT_SOURCE_ROWS,
        "accepted_rows": accepted,
        "quarantined_rows": quarantined,
        "ordered_row_identity_sha256": digest.hexdigest(),
    }


def _readonly_live_prestate(source_sha: str) -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT current_database() AS db")
            database = cur.fetchone()["db"]
            require(database == "markorbit", "Madrid-IR preflight target must be markorbit")
            cur.execute(
                """
                SELECT source_stream,checkpoint_source_ordinal,rows_committed,
                       accepted_committed,quarantine_committed,status
                FROM trademark_gb.historical_source_ingest_run_v2
                WHERE source_archive_sha256=%s
                """,
                (source_sha,),
            )
            run = cur.fetchone()
            cur.execute(
                """
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE current_state_verified) AS unapproved_current,
                       count(*) FILTER (WHERE NOT historical_source_only) AS nonhistorical
                FROM trademark_gb.historical_source_row_v2
                WHERE source_archive_sha256=%s
                """,
                (source_sha,),
            )
            rows = cur.fetchone()
    return {
        "database": database,
        "run": dict(run) if run is not None else None,
        "rows": dict(rows),
    }


def verify_live_prestate(proof: dict[str, Any]) -> dict[str, Any]:
    state = _readonly_live_prestate(proof["spec"]["zip_sha"])
    require(
        state["database"] == "markorbit"
        and state["run"] is None
        and state["rows"]["total"] == 0
        and state["rows"]["unapproved_current"] == 0
        and state["rows"]["nonhistorical"] == 0,
        "Madrid-IR production prestate is not empty and historical-safe",
    )
    return state


def make_plan(
    proof: dict[str, Any],
    prefix: dict[str, Any],
    *,
    execution_main: str | None = None,
) -> dict[str, Any]:
    reserve = domestic.require_e_disk_reserve()["E"]
    execution_main = domestic.current_git_head() if execution_main is None else execution_main
    require(
        re.fullmatch(r"[0-9a-f]{40}", execution_main) is not None,
        "invalid Madrid-IR execution main identity",
    )
    return {
        "kind": "GB_MADRID_IR_HISTORICAL_E_PILOT_PLAN_V1",
        "status": "FROZEN_NO_APPLY",
        "execution_main_sha": execution_main,
        "source_stream": STREAM,
        "source_zip_sha256": proof["spec"]["zip_sha"],
        "source_member": proof["spec"]["member"],
        "source_total_rows": proof["spec"]["total"],
        "source_expected_accepted_rows": proof["spec"]["accepted"],
        "source_expected_quarantine_rows": proof["spec"]["bad"],
        "pilot_source_rows": PILOT_SOURCE_ROWS,
        "pilot_expected_accepted_rows": prefix["accepted_rows"],
        "pilot_expected_quarantine_rows": prefix["quarantined_rows"],
        "pilot_ordered_row_identity_sha256": prefix["ordered_row_identity_sha256"],
        "stage_manifest_sha256": proof["spec"]["manifest_sha"],
        "stage_rows_sha256": proof["manifest"]["accepted_jsonl_sha256"],
        "stage_quarantine_sha256": proof["manifest"]["quarantine_jsonl_sha256"],
        "structured_stage_root": proof["e_stage"]["root"],
        "structured_stage_drive": "E",
        "structured_stage_manifest_sha256": proof["e_stage"]["manifest_sha256"],
        "structured_stage_relocation_plan_sha256": proof["e_stage"]["relocation_plan_sha256"],
        "structured_stage_relocation_receipt_sha256": proof["e_stage"]["relocation_receipt_sha256"],
        "structured_stage_independent_audit_sha256": proof["e_stage"]["independent_audit_sha256"],
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "operator_sha256": domestic.canonical_text_sha(Path(__file__)),
        "schema_sql_sha256": hashlib.sha256(source_rows.SCHEMA_SQL.encode()).hexdigest(),
        "disk_reserve_gate": {"E": {"reserve_bytes": int(reserve["reserve_bytes"])}},
        "historical_source_only": True,
        "current_state_verified": False,
        "domestic_resume_authorized": False,
        "full_madrid_import_authorized": False,
        "journal_ingest_authorized": False,
        "api_cutover_authorized": False,
        "clickhouse_cutover_authorized": False,
        "source_cleanup_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    require(
        plan.get("kind") == "GB_MADRID_IR_HISTORICAL_E_PILOT_PLAN_V1"
        and plan.get("status") == "FROZEN_NO_APPLY"
        and re.fullmatch(r"[0-9a-f]{40}", plan.get("execution_main_sha", "")) is not None
        and plan.get("source_stream") == STREAM
        and plan.get("source_zip_sha256") == source_rows.SOURCE_SPECS[STREAM]["zip_sha"]
        and plan.get("source_total_rows") == source_rows.SOURCE_SPECS[STREAM]["total"]
        and plan.get("pilot_source_rows") == PILOT_SOURCE_ROWS
        and plan.get("pilot_expected_accepted_rows", 0)
        + plan.get("pilot_expected_quarantine_rows", 0)
        == PILOT_SOURCE_ROWS
        and re.fullmatch(r"[0-9a-f]{64}", plan.get("pilot_ordered_row_identity_sha256", ""))
        is not None
        and plan.get("structured_stage_root") == str(e_stage.STAGE_ROOT)
        and plan.get("structured_stage_drive") == "E"
        and plan.get("structured_stage_manifest_sha256") == e_stage.STAGE_MANIFEST_SHA
        and plan.get("structured_stage_relocation_plan_sha256") == e_stage.RELOCATION_PLAN_SHA
        and plan.get("structured_stage_relocation_receipt_sha256") == e_stage.RELOCATION_RECEIPT_SHA
        and plan.get("structured_stage_independent_audit_sha256") == e_stage.INDEPENDENT_AUDIT_SHA
        and plan.get("target_database") == "markorbit"
        and plan.get("target_database_physical_drive") == "E"
        and plan.get("future_query_storage_placement") == "hot_global"
        and plan.get("operator_sha256") == domestic.canonical_text_sha(Path(__file__))
        and plan.get("schema_sql_sha256")
        == hashlib.sha256(source_rows.SCHEMA_SQL.encode()).hexdigest()
        and set(plan.get("disk_reserve_gate", {})) == {"E"}
        and set(plan["disk_reserve_gate"]["E"]) == {"reserve_bytes"}
        and int(plan["disk_reserve_gate"]["E"]["reserve_bytes"]) > 0
        and plan.get("historical_source_only") is True
        and plan.get("current_state_verified") is False
        and plan.get("domestic_resume_authorized") is False
        and plan.get("full_madrid_import_authorized") is False
        and plan.get("journal_ingest_authorized") is False
        and plan.get("api_cutover_authorized") is False
        and plan.get("clickhouse_cutover_authorized") is False
        and plan.get("source_cleanup_authorized") is False,
        "Madrid-IR frozen pilot plan/operator mismatch",
    )
    expected = f"GO #855 GB-MADRID-IR-PILOT {plan_sha} FIRST-1000-ONLY"
    require(token == expected, "exact GB Madrid-IR pilot authority required")


def apply_pilot(proof: dict[str, Any], plan: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    from app.db import postgres_conn

    require(not RECEIPT.exists(), "Madrid-IR pilot receipt already exists; refuse replay")
    domestic.require_live_clean_main(plan["execution_main_sha"])
    verify_live_prestate(proof)
    reserve = domestic.verify_apply_disk_reserve(plan["disk_reserve_gate"])
    spec = proof["spec"]
    rows = []
    for row in source_rows.ordered_source_records(proof, STREAM):
        if row[0] > PILOT_SOURCE_ROWS:
            break
        rows.append(source_rows.pg_params(row, STREAM, proof))
    require(len(rows) == PILOT_SOURCE_ROWS, "Madrid-IR pilot source bound drift")

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            require(cur.fetchone()["db"] == "markorbit", "Madrid-IR target must be markorbit")
            cur.execute(
                """
                INSERT INTO trademark_gb.historical_source_ingest_run_v2(
                  source_archive_sha256,source_stream,source_member,
                  stage_manifest_sha256,stage_rows_sha256,stage_quarantine_sha256,
                  expected_source_rows,expected_accepted_rows,expected_quarantine_rows
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    spec["zip_sha"],
                    STREAM,
                    spec["member"],
                    spec["manifest_sha"],
                    proof["manifest"]["accepted_jsonl_sha256"],
                    proof["manifest"]["quarantine_jsonl_sha256"],
                    spec["total"],
                    spec["accepted"],
                    spec["bad"],
                ),
            )
            require(cur.rowcount == 1, "Madrid-IR ingest run insert failed")
            cur.executemany(source_rows.INSERT_SQL, rows)
            require(cur.rowcount == len(rows), "Madrid-IR pilot duplicate/partial INSERT detected")
            accepted = sum(row[4] == "ACCEPTED" for row in rows)
            quarantined = len(rows) - accepted
            cur.execute(
                """
                UPDATE trademark_gb.historical_source_ingest_run_v2
                SET checkpoint_source_ordinal=%s,rows_committed=%s,
                    accepted_committed=%s,quarantine_committed=%s,updated_at=now()
                WHERE source_archive_sha256=%s AND checkpoint_source_ordinal=0
                  AND rows_committed=0 AND status='RUNNING'
                """,
                (
                    PILOT_SOURCE_ROWS,
                    PILOT_SOURCE_ROWS,
                    accepted,
                    quarantined,
                    spec["zip_sha"],
                ),
            )
            require(cur.rowcount == 1, "Madrid-IR pilot checkpoint transition failed")
            cur.execute(
                """
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE record_kind='ACCEPTED') AS accepted,
                       count(*) FILTER (WHERE record_kind='QUARANTINED') AS quarantined,
                       count(*) FILTER (WHERE current_state_verified) AS unapproved_current,
                       count(*) FILTER (WHERE NOT historical_source_only) AS nonhistorical,
                       min(source_row_ordinal) AS min_ordinal,
                       max(source_row_ordinal) AS max_ordinal
                FROM trademark_gb.historical_source_row_v2
                WHERE source_archive_sha256=%s
                """,
                (spec["zip_sha"],),
            )
            check = cur.fetchone()
            require(
                check["total"] == PILOT_SOURCE_ROWS
                and check["accepted"] == plan["pilot_expected_accepted_rows"]
                and check["quarantined"] == plan["pilot_expected_quarantine_rows"]
                and check["unapproved_current"] == 0
                and check["nonhistorical"] == 0
                and check["min_ordinal"] == 1
                and check["max_ordinal"] == PILOT_SOURCE_ROWS,
                "Madrid-IR pilot live residency/currentness drift",
            )
            conn.commit()

    payload = {
        "kind": "GB_MADRID_IR_HISTORICAL_E_PG_PILOT_V1",
        "status": "FIRST_1000_HISTORICAL_ROWS_COMMITTED_NOT_FULL_IMPORT",
        "plan_sha256": plan_sha,
        "execution_main_sha": plan["execution_main_sha"],
        "source_zip_sha256": spec["zip_sha"],
        "source_rows_committed": PILOT_SOURCE_ROWS,
        "accepted_rows_committed": plan["pilot_expected_accepted_rows"],
        "quarantined_rows_committed": plan["pilot_expected_quarantine_rows"],
        "checkpoint_source_row_ordinal": PILOT_SOURCE_ROWS,
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "structured_stage_root": str(e_stage.STAGE_ROOT),
        "future_query_storage_placement": "hot_global",
        "full_import_complete": False,
        "historical_source_only": True,
        "source_status_current_verified": False,
        "journal_ingest_authorized": False,
        "serving_cutover_authorized": False,
        "source_cleanup_authorized": False,
        "disk_reserve_apply_snapshot": reserve,
    }
    with RECEIPT.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    payload["receipt_path"] = str(RECEIPT)
    payload["receipt_sha256"] = domestic.sha(RECEIPT)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--apply-pilot", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()

    execution_main = (
        domestic.require_live_clean_main()
        if args.freeze_plan is not None or args.apply_pilot
        else domestic.current_git_head()
    )
    proof = e_stage.verify_e_stage(STREAM)
    verify_live_prestate(proof)
    prefix = prefix_summary(proof)
    proposed = make_plan(proof, prefix, execution_main=execution_main)

    if args.preflight_only:
        require(not args.plan and not args.authority_token, "preflight accepts no Apply arguments")
        print(json.dumps(proposed, ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        require(
            not args.plan
            and not args.authority_token
            and args.freeze_plan.parent.resolve() == GOV.resolve(),
            "freeze plan must be immutable under GB governed plans",
        )
        with args.freeze_plan.open("x", encoding="utf-8") as stream:
            json.dump(proposed, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        print("GB_MADRID_IR_PILOT_PLAN_SHA256=" + domestic.sha(args.freeze_plan), flush=True)
        print("GB_MADRID_IR_PILOT_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        return

    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and domestic.sha(args.plan) == args.plan_sha,
        "exact frozen GB Madrid-IR pilot plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    require(plan == proposed, "Madrid-IR pilot plan differs from live frozen state")
    authorize(plan, args.plan_sha, args.authority_token)
    result = apply_pilot(proof, plan, args.plan_sha)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
