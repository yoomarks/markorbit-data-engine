"""Governed full Domestic continuation after the accepted first-1,000-row pilot.

Plan/preflight read production metadata only. Apply resumes exactly from a frozen
checkpoint, commits bounded batches, preserves every source/quarantine ordinal,
and never upgrades the historical 2018-era source to current UK registry truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.global_trademarks import gb_historical_source_row_pilot as pilot

STREAM = "DOMESTIC"
START_CHECKPOINT = 1000
TARGET_SOURCE_ROWS = 1188992
BATCH_SIZE = 5000
GOV = Path(r"D:\yoomarks\governed-plans\855")
PILOT_RECEIPT = GOV / "gb-domestic-pg-pilot-r1.json"
PILOT_RECEIPT_SHA = "3d233767b09e1265a0728f264b5806cb0675214acae5d7cd8d3cb790f780c9f1"
PILOT_AUDIT = GOV / "gb-domestic-pg-pilot-independent-audit-r1.json"
PILOT_AUDIT_SHA = "b9aac974a4db02e2813deefa20ec66e2d7fc5977ea7c1074c77925cccc6df947"
PILOT_OPERATOR_SHA = "d086bd0efad9953279d0d788417c0e65496078f1a61ba1a4cc939d64405a918a"


def require(ok: bool, why: str) -> None:
    if not ok:
        raise RuntimeError(why)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_text_sha(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_prior_acceptance() -> dict[str, Any]:
    require(
        PILOT_RECEIPT.is_file() and sha(PILOT_RECEIPT) == PILOT_RECEIPT_SHA,
        "accepted GB pilot receipt SHA drift",
    )
    require(
        PILOT_AUDIT.is_file() and sha(PILOT_AUDIT) == PILOT_AUDIT_SHA,
        "independent GB pilot audit SHA drift",
    )
    receipt = json.loads(PILOT_RECEIPT.read_text(encoding="utf-8"))
    audit = json.loads(PILOT_AUDIT.read_text(encoding="utf-8"))
    require(
        receipt["status"] == "FIRST_1000_HISTORICAL_ROWS_ACCEPTED_NOT_FULL_IMPORT"
        and receipt["stream"] == STREAM
        and receipt["checkpoint_source_row_ordinal"] == START_CHECKPOINT
        and receipt["source_rows_committed"] == START_CHECKPOINT
        and receipt["source_status_current_verified"] is False
        and receipt["full_import_complete"] is False,
        "accepted GB pilot receipt contract drift",
    )
    require(
        audit["status"] == "FIRST_1000_EXACT_SOURCE_ROWS_ACCEPTED_NOT_FULL_IMPORT"
        and audit["source_rows_verified"] == START_CHECKPOINT
        and audit["checkpoint_source_row_ordinal"] == START_CHECKPOINT
        and audit["historical_source_only"] is True
        and audit["current_state_verified"] is False
        and audit["full_import_complete"] is False,
        "independent GB pilot audit contract drift",
    )
    require(
        canonical_text_sha(Path(pilot.__file__)) == PILOT_OPERATOR_SHA,
        "merged first-1,000 pilot dependency SHA drift",
    )
    return {"receipt": receipt, "audit": audit}


def _readonly_live_state(source_sha: str) -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            db = cur.execute("SELECT current_database() AS db").fetchone()["db"]
            require(db == "markorbit", "GB full-resume target must be markorbit")
            run = cur.execute(
                """
                SELECT source_stream,source_member,expected_source_rows,
                       expected_accepted_rows,expected_quarantine_rows,
                       checkpoint_source_ordinal,rows_committed,accepted_committed,
                       quarantine_committed,status
                FROM trademark_gb.historical_source_ingest_run_v2
                WHERE source_archive_sha256=%s
                """,
                (source_sha,),
            ).fetchone()
            require(run is not None, "GB Domestic ingest run missing")
            counts = cur.execute(
                """
                SELECT count(*) AS total,
                       min(source_row_ordinal) AS min_ordinal,
                       max(source_row_ordinal) AS max_ordinal,
                       count(*) FILTER (WHERE record_kind='ACCEPTED') AS accepted,
                       count(*) FILTER (WHERE record_kind='QUARANTINED') AS quarantined,
                       count(*) FILTER (WHERE current_state_verified) AS unapproved_current,
                       count(*) FILTER (WHERE NOT historical_source_only) AS nonhistorical,
                       count(*) FILTER (
                           WHERE source_row_ordinal > %s
                       ) AS rows_past_checkpoint
                FROM trademark_gb.historical_source_row_v2
                WHERE source_archive_sha256=%s
                """,
                (run["checkpoint_source_ordinal"], source_sha),
            ).fetchone()
    state = dict(run)
    state.update({f"live_{key}": value for key, value in dict(counts).items()})
    return state


def verify_live_checkpoint(proof: dict[str, Any]) -> dict[str, Any]:
    spec = proof["spec"]
    state = _readonly_live_state(spec["zip_sha"])
    require(
        state["source_stream"] == STREAM
        and state["source_member"] == spec["member"]
        and state["expected_source_rows"] == spec["total"] == TARGET_SOURCE_ROWS
        and state["expected_accepted_rows"] == spec["accepted"]
        and state["expected_quarantine_rows"] == spec["bad"]
        and state["checkpoint_source_ordinal"] == START_CHECKPOINT
        and state["rows_committed"] == START_CHECKPOINT
        and state["accepted_committed"] == START_CHECKPOINT
        and state["quarantine_committed"] == 0
        and state["status"] == "RUNNING"
        and state["live_total"] == START_CHECKPOINT
        and state["live_min_ordinal"] == 1
        and state["live_max_ordinal"] == START_CHECKPOINT
        and state["live_accepted"] == START_CHECKPOINT
        and state["live_quarantined"] == 0
        and state["live_unapproved_current"] == 0
        and state["live_nonhistorical"] == 0
        and state["live_rows_past_checkpoint"] == 0,
        "GB Domestic production checkpoint/residency drift",
    )
    return state


def make_plan(proof: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    reserve = pilot.require_disk_reserve()
    return {
        "kind": "GB_DOMESTIC_HISTORICAL_FULL_RESUME_PLAN_V1",
        "status": "FROZEN_NO_APPLY",
        "source_stream": STREAM,
        "source_zip_sha256": proof["spec"]["zip_sha"],
        "source_member": proof["spec"]["member"],
        "stage_manifest_sha256": proof["spec"]["manifest_sha"],
        "stage_rows_sha256": proof["manifest"]["accepted_jsonl_sha256"],
        "stage_quarantine_sha256": proof["manifest"]["quarantine_jsonl_sha256"],
        "source_total_rows": proof["spec"]["total"],
        "source_expected_accepted_rows": proof["spec"]["accepted"],
        "source_expected_quarantine_rows": proof["spec"]["bad"],
        "start_checkpoint_source_ordinal": live["checkpoint_source_ordinal"],
        "target_checkpoint_source_ordinal": proof["spec"]["total"],
        "remaining_source_rows": proof["spec"]["total"] - live["checkpoint_source_ordinal"],
        "batch_size": BATCH_SIZE,
        "target_database": "markorbit",
        "pilot_receipt_sha256": PILOT_RECEIPT_SHA,
        "pilot_independent_audit_sha256": PILOT_AUDIT_SHA,
        "pilot_operator_sha256": PILOT_OPERATOR_SHA,
        "full_resume_operator_sha256": canonical_text_sha(Path(__file__)),
        "schema_sql_sha256": hashlib.sha256(pilot.SCHEMA_SQL.encode("utf-8")).hexdigest(),
        "disk_reserve_preflight": reserve,
        "historical_source_only": True,
        "current_state_verified": False,
        "journal_ingest_authorized": False,
        "api_cutover_authorized": False,
        "clickhouse_cutover_authorized": False,
        "source_cleanup_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    require(
        plan["kind"] == "GB_DOMESTIC_HISTORICAL_FULL_RESUME_PLAN_V1"
        and plan["status"] == "FROZEN_NO_APPLY"
        and plan["source_stream"] == STREAM
        and plan["start_checkpoint_source_ordinal"] == START_CHECKPOINT
        and plan["target_checkpoint_source_ordinal"] == TARGET_SOURCE_ROWS
        and plan["remaining_source_rows"] == TARGET_SOURCE_ROWS - START_CHECKPOINT
        and plan["batch_size"] == BATCH_SIZE
        and plan["target_database"] == "markorbit"
        and plan["pilot_receipt_sha256"] == PILOT_RECEIPT_SHA
        and plan["pilot_independent_audit_sha256"] == PILOT_AUDIT_SHA
        and plan["pilot_operator_sha256"] == PILOT_OPERATOR_SHA
        and plan["full_resume_operator_sha256"] == canonical_text_sha(Path(__file__))
        and plan["schema_sql_sha256"]
        == hashlib.sha256(pilot.SCHEMA_SQL.encode("utf-8")).hexdigest()
        and plan["historical_source_only"] is True
        and plan["current_state_verified"] is False
        and plan["journal_ingest_authorized"] is False
        and plan["api_cutover_authorized"] is False
        and plan["clickhouse_cutover_authorized"] is False
        and plan["source_cleanup_authorized"] is False,
        "GB Domestic full-resume frozen plan/operator mismatch",
    )
    expected = (
        f"GO #855 GB-DOMESTIC-FULL-RESUME {plan_sha} "
        f"CHECKPOINT-{START_CHECKPOINT}-TO-{TARGET_SOURCE_ROWS}"
    )
    require(token == expected, "exact GB Domestic full-resume authority required")


def apply_full(proof: dict[str, Any], plan: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    from app.db import postgres_conn

    receipt = GOV / "gb-domestic-full-resume-r1.json"
    require(not receipt.exists(), "GB Domestic full-resume receipt already exists; refuse replay")
    live = verify_live_checkpoint(proof)
    require(
        live["checkpoint_source_ordinal"] == plan["start_checkpoint_source_ordinal"],
        "GB Domestic live checkpoint no longer matches frozen plan",
    )
    reserve = pilot.require_disk_reserve()
    spec = proof["spec"]
    committed = int(live["rows_committed"])
    accepted = int(live["accepted_committed"])
    quarantined = int(live["quarantine_committed"])
    last = int(live["checkpoint_source_ordinal"])

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            db = cur.execute("SELECT current_database() AS db").fetchone()["db"]
            require(db == "markorbit", "GB Domestic full-resume target must be markorbit")
            batch: list[tuple] = []
            for row in pilot.ordered_source_records(proof, STREAM):
                ordinal, kind = row[:2]
                if ordinal <= last:
                    continue
                batch.append(pilot.pg_params(row, STREAM, proof))
                if len(batch) == BATCH_SIZE or ordinal == TARGET_SOURCE_ROWS:
                    old = last
                    cur.executemany(pilot.INSERT_SQL, batch)
                    require(
                        cur.rowcount == len(batch),
                        "GB Domestic full-resume duplicate/partial INSERT detected",
                    )
                    last = ordinal
                    committed += len(batch)
                    accepted += sum(item[4] == "ACCEPTED" for item in batch)
                    quarantined += sum(item[4] == "QUARANTINED" for item in batch)
                    cur.execute(
                        """
                        UPDATE trademark_gb.historical_source_ingest_run_v2
                        SET checkpoint_source_ordinal=%s,rows_committed=%s,
                            accepted_committed=%s,quarantine_committed=%s,updated_at=now()
                        WHERE source_archive_sha256=%s AND checkpoint_source_ordinal=%s
                          AND status='RUNNING'
                        """,
                        (last, committed, accepted, quarantined, spec["zip_sha"], old),
                    )
                    require(cur.rowcount == 1, "GB Domestic full-resume checkpoint failed")
                    conn.commit()
                    batch.clear()
                    if (last - START_CHECKPOINT) % 50000 == 0 or last == TARGET_SOURCE_ROWS:
                        print(
                            "GB_DOMESTIC_FULL_PROGRESS "
                            f"checkpoint={last} accepted={accepted} "
                            f"quarantined={quarantined}",
                            flush=True,
                        )

            require(
                last == spec["total"]
                and committed == spec["total"]
                and accepted == spec["accepted"]
                and quarantined == spec["bad"],
                "GB Domestic full-resume final counts do not match frozen source evidence",
            )
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
            final = cur.fetchone()
            require(
                final["total"] == spec["total"]
                and final["accepted"] == spec["accepted"]
                and final["quarantined"] == spec["bad"]
                and final["unapproved_current"] == 0
                and final["nonhistorical"] == 0
                and final["min_ordinal"] == 1
                and final["max_ordinal"] == spec["total"],
                "GB Domestic full-resume live final residency/currentness drift",
            )
            cur.execute(
                """
                UPDATE trademark_gb.historical_source_ingest_run_v2
                SET status='COMPLETE',completed_at=now(),updated_at=now()
                WHERE source_archive_sha256=%s AND status='RUNNING'
                  AND checkpoint_source_ordinal=%s AND rows_committed=%s
                  AND accepted_committed=%s AND quarantine_committed=%s
                """,
                (spec["zip_sha"], spec["total"], spec["total"], spec["accepted"], spec["bad"]),
            )
            require(cur.rowcount == 1, "GB Domestic full-resume completion transition failed")
            conn.commit()

    payload = {
        "kind": "GB_DOMESTIC_HISTORICAL_FULL_RESUME_V1",
        "status": "DOMESTIC_HISTORICAL_SOURCE_COMPLETE_NOT_CURRENT_REGISTER",
        "plan_sha256": plan_sha,
        "source_zip_sha256": spec["zip_sha"],
        "source_rows_committed": committed,
        "accepted_rows_committed": accepted,
        "quarantined_rows_committed": quarantined,
        "checkpoint_source_row_ordinal": last,
        "target_database": "markorbit",
        "full_import_complete": True,
        "historical_source_only": True,
        "source_status_current_verified": False,
        "journal_ingest_authorized": False,
        "api_cutover_authorized": False,
        "clickhouse_cutover_authorized": False,
        "source_cleanup_authorized": False,
        "disk_reserve_preflight": reserve,
    }
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    payload["receipt_path"] = str(receipt)
    payload["receipt_sha256"] = sha(receipt)
    return payload


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

    verify_prior_acceptance()
    proof = pilot.verify_stage(STREAM)
    live = verify_live_checkpoint(proof)
    proposed = make_plan(proof, live)

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
        print("GB_DOMESTIC_FULL_PLAN_SHA256=" + sha(args.freeze_plan), flush=True)
        print("GB_DOMESTIC_FULL_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        return

    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and sha(args.plan) == args.plan_sha,
        "exact frozen GB Domestic full-resume plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    require(plan == proposed, "GB Domestic full-resume plan differs from live frozen state")
    authorize(plan, args.plan_sha, args.authority_token)
    result = apply_full(proof, plan, args.plan_sha)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
