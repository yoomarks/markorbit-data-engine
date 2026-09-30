"""Exact-authority, at-most-1,000-original-row GB historical PostgreSQL pilot.

Separate from source staging. Preflight/freeze never connect to production DB;
pilot applies only an additive GB source-row schema and bounded source rows.
No current-register state, API activation, journal import or source cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import shutil
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Iterator

AUDIT = Path(r"D:\yoomarks\governed-plans\855\gb-historical-stock-independent-audit-r1.json")
AUDIT_SHA = "320c40b1cea7d5593ee8dbb99dfcbc1f534b71bd682281ab42fd719873a9d0c5"
STAGE = Path(r"D:\yoomarks\governed-plans\855\historical-stock")
GOV = Path(r"D:\yoomarks\governed-plans\855")
PILOT_SOURCE_ROWS = 1000
REQUIRED_FREE_BUFFER = 64 * 1024**3
SOURCE_SPECS = {
    "DOMESTIC": {
        "zip_sha": "3b6063bed36a78e8a04f10a5383f2881f4072a1be13e706a81ab097fb56ee571",
        "member": "OpenDataDomestic.txt",
        "total": 1188992,
        "accepted": 1188886,
        "bad": 106,
        "manifest_sha": "aa7f52cfed7ff850af4e56060bd196d05010613ec2d930cc82bb1b74c4018dbe",
    },
    "MADRID_IR": {
        "zip_sha": "2a93bb45f0c40c69813628b6f2ab3a6a443f6d0504d8edf009c51027f28f8c85",
        "member": "OpenDataIR.txt",
        "total": 109695,
        "accepted": 109687,
        "bad": 8,
        "manifest_sha": "a56eb8fce40241176a1a353d918a7dfb1dc4bd17ad5cdb21d3bb8bba1a7693c2",
    },
}
SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS trademark_gb;
CREATE TABLE IF NOT EXISTS trademark_gb.historical_source_ingest_run_v2 (
 source_archive_sha256 text PRIMARY KEY CHECK (source_archive_sha256 ~ '^[0-9a-f]{64}$'),
 source_stream text NOT NULL CHECK (source_stream IN ('DOMESTIC','MADRID_IR')),
 source_member text NOT NULL,
 stage_manifest_sha256 text NOT NULL,
 stage_rows_sha256 text NOT NULL,
 stage_quarantine_sha256 text NOT NULL,
 expected_source_rows bigint NOT NULL CHECK (expected_source_rows > 0),
 expected_accepted_rows bigint NOT NULL CHECK (expected_accepted_rows >= 0),
 expected_quarantine_rows bigint NOT NULL CHECK (expected_quarantine_rows >= 0),
 checkpoint_source_ordinal bigint NOT NULL DEFAULT 0 CHECK (checkpoint_source_ordinal >= 0),
 rows_committed bigint NOT NULL DEFAULT 0 CHECK (rows_committed >= 0),
 accepted_committed bigint NOT NULL DEFAULT 0 CHECK (accepted_committed >= 0),
 quarantine_committed bigint NOT NULL DEFAULT 0 CHECK (quarantine_committed >= 0),
 status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','COMPLETE','FAILED')),
 started_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 completed_at timestamptz,
 CHECK (expected_accepted_rows + expected_quarantine_rows = expected_source_rows),
 CHECK (accepted_committed + quarantine_committed = rows_committed),
 CHECK (rows_committed <= expected_source_rows)
);
CREATE TABLE IF NOT EXISTS trademark_gb.historical_source_row_v2 (
 source_archive_sha256 text NOT NULL
  REFERENCES trademark_gb.historical_source_ingest_run_v2(source_archive_sha256),
 source_row_ordinal bigint NOT NULL CHECK (source_row_ordinal > 0),
 source_stream text NOT NULL CHECK (source_stream IN ('DOMESTIC','MADRID_IR')),
 source_member text NOT NULL,
 record_kind text NOT NULL CHECK (record_kind IN ('ACCEPTED','QUARANTINED')),
 application_number text,
 applicant_name_raw text,
 source_status_raw text,
 nice_classes smallint[] NOT NULL DEFAULT '{}',
 source_cells_sha256 text NOT NULL CHECK (source_cells_sha256 ~ '^[0-9a-f]{64}$'),
 source_row_sha256 text NOT NULL CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
 source_payload jsonb NOT NULL,
 historical_source_only boolean NOT NULL DEFAULT true CHECK (historical_source_only),
 current_state_verified boolean NOT NULL DEFAULT false CHECK (NOT current_state_verified),
 ingested_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (source_archive_sha256,source_row_ordinal),
 CHECK ((record_kind='ACCEPTED' AND application_number IS NOT NULL)
     OR (record_kind='QUARANTINED' AND application_number IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_gb_historical_source_row_mark_v2
 ON trademark_gb.historical_source_row_v2(source_stream,application_number)
 WHERE record_kind='ACCEPTED';
"""
INSERT_SQL = """
INSERT INTO trademark_gb.historical_source_row_v2(
 source_archive_sha256,source_row_ordinal,source_stream,source_member,
 record_kind,application_number,applicant_name_raw,source_status_raw,
 nice_classes,source_cells_sha256,source_row_sha256,source_payload,
 historical_source_only,current_state_verified
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,true,false)
ON CONFLICT DO NOTHING
"""


def require(ok: bool, why: str) -> None:
    if not ok:
        raise RuntimeError(why)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def scoped_path(value: str) -> Path:
    path = Path(value)
    require(
        path.parent.resolve() == STAGE.resolve() and path.is_file() and not path.is_symlink(),
        "untrusted GB staging file path",
    )
    return path


def verify_stage(stream: str) -> dict[str, Any]:
    require(stream in SOURCE_SPECS, "unreviewed GB historical stream")
    spec = SOURCE_SPECS[stream]
    require(sha(AUDIT) == AUDIT_SHA, "independent GB full-source audit SHA drift")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    require(
        audit["status"] == "TWO_HISTORICAL_ZIPS_STAGED_NOT_DATABASE_INGESTED"
        and audit["data_engine_ingested"] is False
        and audit["schema_migration_applied"] is False,
        "GB source evidence is not an accepted historical-only stage",
    )
    match = [row for row in audit["streams"] if row["source_stream"] == stream]
    require(
        len(match) == 1
        and match[0]["source_zip_sha256"] == spec["zip_sha"]
        and match[0]["source_rows"] == spec["total"]
        and match[0]["accepted_source_rows"] == spec["accepted"]
        and match[0]["quarantined_source_rows"] == spec["bad"]
        and match[0]["manifest_sha256"] == spec["manifest_sha"],
        "GB independent audit source/row identity drift",
    )
    manifest_path = STAGE / (stream.lower() + "-" + spec["zip_sha"][:12] + "-manifest.json")
    require(sha(manifest_path) == spec["manifest_sha"], "GB immutable manifest SHA drift")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest["source_stream"] == stream
        and manifest["source_archive_sha256"] == spec["zip_sha"]
        and manifest["source_member"] == spec["member"]
        and manifest["row_count"] == spec["total"]
        and manifest["accepted_source_rows"] == spec["accepted"]
        and manifest["quarantined_source_rows"] == spec["bad"]
        and manifest["historical_source_only"] is True
        and manifest["current_state_verified"] is False
        and manifest["data_engine_ingested"] is False,
        "GB manifest field identity drift",
    )
    rows = scoped_path(manifest["rows_path"])
    bad = scoped_path(manifest["quarantine_path"])
    require(
        sha(rows) == manifest["accepted_jsonl_sha256"] == match[0]["rows_sha256"]
        and sha(bad) == manifest["quarantine_jsonl_sha256"] == match[0]["quarantine_sha256"],
        "GB accepted/quarantine JSONL SHA drift",
    )
    return {"manifest": manifest, "rows": rows, "bad": bad, "spec": spec}


def require_disk_reserve() -> dict[str, int]:
    result = {}
    for drive in ("D:\\", "E:\\"):
        disk = shutil.disk_usage(drive)
        floor = (disk.total * 30 + 99) // 100 + REQUIRED_FREE_BUFFER
        require(disk.free >= floor + 4 * 1024**3, drive + " physical reserve insufficient")
        result[drive[0]] = {"free_bytes": disk.free, "reserve_bytes": floor}
    return result


def make_plan(stream: str, proof: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "GB_HISTORICAL_PG_PILOT_PLAN_V1",
        "status": "FROZEN_NO_APPLY",
        "source_stream": stream,
        "pilot_source_rows": PILOT_SOURCE_ROWS,
        "target_database": "markorbit",
        "audit_sha256": AUDIT_SHA,
        "operator_sha256": sha(Path(__file__)),
        "schema_sql_sha256": hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest(),
        "source_zip_sha256": proof["spec"]["zip_sha"],
        "stage_manifest_sha256": proof["spec"]["manifest_sha"],
        "stage_rows_sha256": proof["manifest"]["accepted_jsonl_sha256"],
        "stage_quarantine_sha256": proof["manifest"]["quarantine_jsonl_sha256"],
        "source_rows": proof["spec"]["total"],
        "historical_only": True,
        "api_cutover_authorized": False,
        "merge_authorized": False,
        "source_cleanup_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str, stream: str) -> None:
    require(
        plan["kind"] == "GB_HISTORICAL_PG_PILOT_PLAN_V1"
        and plan["status"] == "FROZEN_NO_APPLY"
        and plan["source_stream"] == stream
        and plan["pilot_source_rows"] == PILOT_SOURCE_ROWS
        and plan["target_database"] == "markorbit"
        and plan["audit_sha256"] == AUDIT_SHA
        and plan["operator_sha256"] == sha(Path(__file__))
        and plan["schema_sql_sha256"] == hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest()
        and plan["historical_only"] is True
        and plan["api_cutover_authorized"] is False
        and plan["merge_authorized"] is False
        and plan["source_cleanup_authorized"] is False,
        "GB pilot frozen plan/operator/schema mismatch",
    )
    expected = f"GO #855 GB-SOURCE-ROW-PILOT {plan_sha} {stream} FIRST-1000-ONLY"
    require(token == expected, "exact GB stock PostgreSQL pilot authority required")


def decode_lines(file, kind: str, stream: str, spec: dict[str, Any]) -> Iterator[tuple]:
    prior = 0
    for line in file:
        payload = json.loads(line)
        ordinal = payload["source_row_ordinal"]
        require(type(ordinal) is int and ordinal > prior, "stage source ordinal not increasing")
        prior = ordinal
        require(
            payload["source_stream"] == stream
            and payload["source_archive_sha256"] == spec["zip_sha"]
            and payload["source_member"] == spec["member"],
            "GB stage source identity mismatch",
        )
        if kind == "ACCEPTED":
            key = payload["application_number"]
            regex = r"UK\d+[A-Z]{0,2}" if stream == "DOMESTIC" else r"WO\d+[A-Z]?"
            require(
                re.fullmatch(regex, key) is not None
                and payload["source_fields"]["Trade Mark"].strip() == key
                and payload["applicant_name_raw"] == payload["source_fields"]["Name"].strip()
                and payload["source_status_raw"] == payload["source_fields"]["Status"].strip()
                and payload["historical_source_only"] is True
                and payload["current_state_verified"] is False,
                "GB staged accepted source row identity/owner/status drift",
            )
            nice = payload["nice_classes"]
            require(
                type(nice) is list
                and all(type(n) is int and 1 <= n <= 45 for n in nice)
                and nice == sorted(set(nice)),
                "GB Nice classes not accepted",
            )
            cells_sha = payload["source_cells_sha256"]
        else:
            require(
                payload["reason"] in ("FIELD_COUNT", "MARK_ID", "CLASS_FLAG")
                and type(payload["cells"]) is list,
                "unreviewed GB quarantine reason",
            )
            key = None
            nice = []
            cells_sha = hashlib.sha256(
                json.dumps(payload["cells"], ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
        require(
            re.fullmatch(r"[0-9a-f]{64}", cells_sha) is not None,
            "GB source-cell fingerprint invalid",
        )
        row_sha = hashlib.sha256(line.encode("utf-8")).hexdigest()
        yield (ordinal, kind, key, payload, nice, cells_sha, row_sha)


def ordered_source_records(proof: dict[str, Any], stream: str) -> Iterator[tuple]:
    spec = proof["spec"]
    with ExitStack() as stack:
        good = stack.enter_context(proof["rows"].open("r", encoding="utf-8", newline=""))
        bad = stack.enter_context(proof["bad"].open("r", encoding="utf-8", newline=""))
        merged = heapq.merge(
            decode_lines(good, "ACCEPTED", stream, spec),
            decode_lines(bad, "QUARANTINED", stream, spec),
            key=lambda row: row[0],
        )
        previous = 0
        for row in merged:
            require(row[0] == previous + 1, "GB source ordinal gap or overlap")
            previous = row[0]
            yield row


def pg_params(row: tuple, stream: str, proof: dict[str, Any]) -> tuple:
    ordinal, kind, key, payload, nice, cells_sha, row_sha = row
    return (
        proof["spec"]["zip_sha"],
        ordinal,
        stream,
        proof["spec"]["member"],
        kind,
        key,
        payload.get("applicant_name_raw") if kind == "ACCEPTED" else None,
        payload.get("source_status_raw") if kind == "ACCEPTED" else None,
        nice,
        cells_sha,
        row_sha,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def apply_pilot(stream: str, proof: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    from app.db import postgres_conn

    receipt = GOV / ("gb-" + stream.lower() + "-pg-pilot-r1.json")
    require(not receipt.exists(), "GB pilot receipt already exists; refuse replay")
    reserve = require_disk_reserve()
    expected = proof["spec"]
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            db = cur.fetchone()["db"]
            require(db == "markorbit", "GB pilot target must be the markorbit database")
            cur.execute(SCHEMA_SQL)
            conn.commit()
            cur.execute(
                """
    INSERT INTO trademark_gb.historical_source_ingest_run_v2(
      source_archive_sha256,source_stream,source_member,
      stage_manifest_sha256,stage_rows_sha256,stage_quarantine_sha256,
      expected_source_rows,expected_accepted_rows,expected_quarantine_rows
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (source_archive_sha256) DO UPDATE SET updated_at=now()
    WHERE trademark_gb.historical_source_ingest_run_v2.source_stream=EXCLUDED.source_stream
      AND trademark_gb.historical_source_ingest_run_v2.stage_manifest_sha256=
          EXCLUDED.stage_manifest_sha256
      AND trademark_gb.historical_source_ingest_run_v2.stage_rows_sha256=
          EXCLUDED.stage_rows_sha256
      AND trademark_gb.historical_source_ingest_run_v2.stage_quarantine_sha256=
          EXCLUDED.stage_quarantine_sha256
    RETURNING checkpoint_source_ordinal,rows_committed,accepted_committed,
              quarantine_committed,status
   """,
                (
                    expected["zip_sha"],
                    stream,
                    expected["member"],
                    expected["manifest_sha"],
                    proof["manifest"]["accepted_jsonl_sha256"],
                    proof["manifest"]["quarantine_jsonl_sha256"],
                    expected["total"],
                    expected["accepted"],
                    expected["bad"],
                ),
            )
            state = cur.fetchone()
            require(
                state is not None
                and state["status"] == "RUNNING"
                and 0 <= state["checkpoint_source_ordinal"] <= PILOT_SOURCE_ROWS
                and state["rows_committed"] == state["checkpoint_source_ordinal"],
                "GB pilot run state/manifest/checkpoint drift",
            )
            conn.commit()
            committed = int(state["rows_committed"])
            last = int(state["checkpoint_source_ordinal"])
            accepted = int(state["accepted_committed"])
            quarantined = int(state["quarantine_committed"])
            batch = []
            for row in ordered_source_records(proof, stream):
                ordinal, kind = row[:2]
                if ordinal <= last:
                    continue
                if ordinal > PILOT_SOURCE_ROWS:
                    break
                batch.append(pg_params(row, stream, proof))
                if len(batch) == 100 or ordinal == PILOT_SOURCE_ROWS:
                    old = last
                    cur.executemany(INSERT_SQL, batch)
                    require(
                        cur.rowcount == len(batch), "GB pilot duplicate/partial INSERT detected"
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
                        (last, committed, accepted, quarantined, expected["zip_sha"], old),
                    )
                    require(cur.rowcount == 1, "GB pilot run checkpoint failed")
                    conn.commit()
                    batch.clear()
            require(
                last == PILOT_SOURCE_ROWS and committed == PILOT_SOURCE_ROWS,
                "GB pilot did not reach its exact source-row bound",
            )
            cur.execute(
                """
    SELECT count(*) AS total,
     count(*) FILTER (WHERE record_kind='ACCEPTED') AS accepted,
     count(*) FILTER (WHERE record_kind='QUARANTINED') AS quarantined,
     count(*) FILTER (WHERE current_state_verified) AS unapproved_current
    FROM trademark_gb.historical_source_row_v2
    WHERE source_archive_sha256=%s AND source_row_ordinal<=%s
   """,
                (expected["zip_sha"], PILOT_SOURCE_ROWS),
            )
            check = cur.fetchone()
            require(
                check["total"] == committed
                and check["accepted"] == accepted
                and check["quarantined"] == quarantined
                and check["unapproved_current"] == 0,
                "GB pilot live database residency/currentness drift",
            )
    receipt_payload = {
        "kind": "GB_HISTORICAL_PG_SOURCE_ROW_PILOT_V1",
        "status": "FIRST_1000_HISTORICAL_ROWS_ACCEPTED_NOT_FULL_IMPORT",
        "stream": stream,
        "plan_sha256": plan_sha,
        "audit_sha256": AUDIT_SHA,
        "source_zip_sha256": expected["zip_sha"],
        "source_rows_committed": committed,
        "accepted_rows_committed": accepted,
        "quarantined_rows_committed": quarantined,
        "checkpoint_source_row_ordinal": last,
        "target_database": "markorbit",
        "schema_migration_applied": True,
        "full_import_complete": False,
        "source_status_current_verified": False,
        "api_cutover_authorized": False,
        "source_cleanup_authorized": False,
        "disk_reserve_preflight": reserve,
    }
    with receipt.open("x", encoding="utf-8") as file:
        json.dump(receipt_payload, file, ensure_ascii=False, sort_keys=True, indent=2)
        file.write("\n")
    receipt_payload["receipt_path"] = str(receipt)
    receipt_payload["receipt_sha256"] = sha(receipt)
    return receipt_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", choices=tuple(SOURCE_SPECS), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--apply-pilot", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()
    proof = verify_stage(args.stream)
    proposed = make_plan(args.stream, proof)
    if args.preflight_only:
        require(not args.authority_token and not args.plan, "preflight accepts no Apply arguments")
        print(json.dumps(proposed, ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        require(
            not args.authority_token
            and not args.plan
            and args.freeze_plan.parent.resolve() == GOV.resolve(),
            "freeze plan must be immutable under GB governed plans",
        )
        with args.freeze_plan.open("x", encoding="utf-8") as file:
            json.dump(proposed, file, ensure_ascii=False, sort_keys=True, indent=2)
            file.write("\n")
        print("GB_PILOT_PLAN_SHA256=" + sha(args.freeze_plan), flush=True)
        print("GB_PILOT_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        return
    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and sha(args.plan) == args.plan_sha,
        "exact frozen GB pilot plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    require(plan == proposed, "GB pilot plan differs from live frozen evidence/operator")
    authorize(plan, args.plan_sha, args.authority_token, args.stream)
    result = apply_pilot(args.stream, proof, args.plan_sha)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
