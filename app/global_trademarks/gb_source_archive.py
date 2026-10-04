"""Governed F: incoming-to-archive lifecycle for accepted UKIPO sources.

The operator never deletes source evidence.  It moves exact, accepted raw ZIPs
within the F: raw authority after database acceptance, and is resumable across
an interrupted multi-file move.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from app.global_trademarks import gb_domestic_full_resume as domestic
from app.global_trademarks import gb_historical_e_stage as e_stage
from app.global_trademarks import gb_postgres_target as target
from app.global_trademarks import gb_stock_snapshot_v2 as stock

INCOMING_ROOT = Path(r"F:\MarkOrbitData\raw\incoming\uk")
ARCHIVE_ROOT = Path(r"F:\MarkOrbitData\raw\archive\uk")
GOV = Path(r"D:\yoomarks\governed-plans\875")
RECEIPT = GOV / "gb-accepted-raw-f-archive-r1.json"
PLAN_KIND = "GB_UKIPO_ACCEPTED_RAW_F_ARCHIVE_PLAN_V1"
RECEIPT_KIND = "GB_UKIPO_ACCEPTED_RAW_F_ARCHIVE_RECEIPT_V1"
LEGACY_FILE_COUNT = 80


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha(value: dict[str, Any]) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return hashlib.sha256(payload).hexdigest()


def _journal_filename(issue: str) -> str:
    require(re.fullmatch(r"20[0-9]{2}-[0-9]{3}", issue) is not None, "invalid journal issue")
    return f"{issue}.zip"


def read_database_evidence() -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                """
                SELECT source_stream,source_archive_sha256,expected_source_rows,
                       rows_committed,status
                FROM trademark_gb.historical_source_ingest_run_v2
                ORDER BY source_stream
                """
            )
            historical = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT issue,source_zip_sha256,expected_notices,
                       checkpoint_notice_ordinal,status
                FROM trademark_gb.journal_issue_ingest_run_v1
                ORDER BY issue
                """
            )
            journals = [dict(row) for row in cur.fetchall()]
    require(
        len(historical) == 2
        and {row["source_stream"] for row in historical} == {"DOMESTIC", "MADRID_IR"},
        "accepted historical source set drift",
    )
    for row in historical:
        spec = stock.SPECS[row["source_stream"]]
        require(
            row["source_archive_sha256"] == spec.source_sha256
            and row["expected_source_rows"] == spec.expected_rows
            and row["rows_committed"] == spec.expected_rows
            and row["status"] == "COMPLETE",
            f"historical {row['source_stream']} is not accepted complete",
        )
    require(len(journals) == 78, "legacy archive requires exact accepted 78-issue journal set")
    for row in journals:
        require(
            row["status"] == "COMPLETE"
            and row["expected_notices"] > 0
            and row["checkpoint_notice_ordinal"] == row["expected_notices"],
            f"journal {row['issue']} is not accepted complete",
        )
    return {"historical": historical, "journals": journals}


def expected_files(database: dict[str, Any]) -> list[dict[str, Any]]:
    files = [
        {
            "filename": stock.SPECS[row["source_stream"]].zip_name,
            "sha256": row["source_archive_sha256"],
            "source_kind": "HISTORICAL_STOCK",
            "source_key": row["source_stream"],
        }
        for row in database["historical"]
    ]
    files.extend(
        {
            "filename": _journal_filename(row["issue"]),
            "sha256": row["source_zip_sha256"],
            "source_kind": "JOURNAL_OBSERVATION",
            "source_key": row["issue"],
        }
        for row in database["journals"]
    )
    files.sort(key=lambda item: item["filename"])
    require(
        len(files) == LEGACY_FILE_COUNT
        and len({item["filename"] for item in files}) == LEGACY_FILE_COUNT,
        "accepted raw archive file set drift",
    )
    return files


def inspect_files(
    files: list[dict[str, Any]],
    *,
    incoming_root: Path = INCOMING_ROOT,
    archive_root: Path = ARCHIVE_ROOT,
    freeze: bool,
) -> list[dict[str, Any]]:
    require(
        incoming_root.drive.upper() == "F:" and archive_root.drive.upper() == "F:",
        "raw lifecycle must remain within F",
    )
    inspected = []
    for item in files:
        incoming = incoming_root / item["filename"]
        archived = archive_root / item["filename"]
        incoming_ok = incoming.is_file() and not incoming.is_symlink()
        archived_ok = archived.is_file() and not archived.is_symlink()
        require(
            not (incoming_ok and archived_ok), f"duplicate raw lifecycle state: {item['filename']}"
        )
        if freeze:
            require(
                incoming_ok and not archived_ok, f"raw file is not incoming: {item['filename']}"
            )
            path = incoming
            state = "INCOMING"
        else:
            require(incoming_ok or archived_ok, f"accepted raw file missing: {item['filename']}")
            path = incoming if incoming_ok else archived
            state = "INCOMING" if incoming_ok else "ARCHIVED"
        require(sha256_file(path) == item["sha256"], f"raw SHA drift: {item['filename']}")
        inspected.append(
            {
                **item,
                "bytes": path.stat().st_size,
                "incoming_path": str(incoming),
                "archive_path": str(archived),
                "state": state,
            }
        )
    return inspected


def make_plan(
    *,
    database: dict[str, Any] | None = None,
    execution_main: str | None = None,
    postgres_target: dict[str, Any] | None = None,
    incoming_root: Path = INCOMING_ROOT,
    archive_root: Path = ARCHIVE_ROOT,
) -> dict[str, Any]:
    database = read_database_evidence() if database is None else database
    execution_main = domestic.current_git_head() if execution_main is None else execution_main
    require(re.fullmatch(r"[0-9a-f]{40}", execution_main) is not None, "invalid main SHA")
    files = inspect_files(
        expected_files(database),
        incoming_root=incoming_root,
        archive_root=archive_root,
        freeze=True,
    )
    if postgres_target is None:
        from app.db import postgres_conn

        with postgres_conn() as conn:
            postgres_target = target.capture(conn)
    return {
        "kind": PLAN_KIND,
        "status": "FROZEN_NO_APPLY",
        "execution_main_sha": execution_main,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "files": files,
        "database_evidence_sha256": canonical_json_sha(database),
        "database_evidence": database,
        **postgres_target,
        "raw_authority_drive": "F",
        "incoming_root": str(incoming_root),
        "archive_root": str(archive_root),
        "operator_sha256": domestic.canonical_text_sha(Path(__file__)),
        "move_within_f_only": True,
        "overwrite_authorized": False,
        "source_delete_authorized": False,
        "serving_cutover_authorized": False,
        "clickhouse_apply_authorized": False,
        "vhdx_operation_authorized": False,
        "current_registry_assertion_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    require(
        plan.get("kind") == PLAN_KIND
        and plan.get("status") == "FROZEN_NO_APPLY"
        and plan.get("file_count") == LEGACY_FILE_COUNT
        and len(plan.get("files", [])) == LEGACY_FILE_COUNT
        and plan.get("total_bytes") == sum(item["bytes"] for item in plan["files"])
        and plan.get("database_evidence_sha256") == canonical_json_sha(plan["database_evidence"])
        and target.validate(
            {
                "accepted_storage_topology_evidence": plan.get(
                    "accepted_storage_topology_evidence"
                ),
                "postgres_target_evidence": plan.get("postgres_target_evidence"),
            }
        )
        and plan.get("raw_authority_drive") == "F"
        and plan.get("incoming_root") == str(INCOMING_ROOT)
        and plan.get("archive_root") == str(ARCHIVE_ROOT)
        and plan.get("operator_sha256") == domestic.canonical_text_sha(Path(__file__))
        and plan.get("move_within_f_only") is True
        and plan.get("overwrite_authorized") is False
        and plan.get("source_delete_authorized") is False
        and plan.get("serving_cutover_authorized") is False
        and plan.get("clickhouse_apply_authorized") is False
        and plan.get("vhdx_operation_authorized") is False
        and plan.get("current_registry_assertion_authorized") is False,
        "GB accepted raw archive frozen plan/operator mismatch",
    )
    expected = f"GO #875 GB-RAW-ARCHIVE {plan_sha} 80-FILES-INCOMING-TO-ARCHIVE"
    require(token == expected, "exact GB accepted raw archive authority required")


def _move_one(item: dict[str, Any]) -> bool:
    incoming = Path(item["incoming_path"])
    archived = Path(item["archive_path"])
    incoming_ok = incoming.is_file() and not incoming.is_symlink()
    archived_ok = archived.is_file() and not archived.is_symlink()
    require(not (incoming_ok and archived_ok), f"duplicate raw lifecycle state: {item['filename']}")
    if archived_ok:
        require(
            archived.stat().st_size == item["bytes"] and sha256_file(archived) == item["sha256"],
            f"archived raw identity drift: {item['filename']}",
        )
        return True
    require(
        incoming_ok
        and incoming.stat().st_size == item["bytes"]
        and sha256_file(incoming) == item["sha256"],
        f"incoming raw identity drift: {item['filename']}",
    )
    archived.parent.mkdir(parents=True, exist_ok=True)
    for ancestor in (archived.parent, *archived.parent.parents):
        if ancestor.exists():
            require(
                not ancestor.is_symlink()
                and not (os.name == "nt" and os.path.isjunction(ancestor)),
                "raw archive path may not traverse a symlink/junction",
            )
    os.rename(incoming, archived)
    require(
        not incoming.exists()
        and archived.is_file()
        and archived.stat().st_size == item["bytes"]
        and sha256_file(archived) == item["sha256"],
        f"raw archive poststate drift: {item['filename']}",
    )
    return False


def apply(plan: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    domestic.require_live_clean_main(plan["execution_main_sha"])
    observed_database = read_database_evidence()
    require(
        observed_database == plan["database_evidence"],
        "accepted GB database evidence drifted after plan freeze",
    )
    from app.db import postgres_conn

    with postgres_conn() as conn:
        target.verify(
            conn,
            {
                "accepted_storage_topology_evidence": plan["accepted_storage_topology_evidence"],
                "postgres_target_evidence": plan["postgres_target_evidence"],
            },
        )
    states = inspect_files(
        plan["files"], incoming_root=INCOMING_ROOT, archive_root=ARCHIVE_ROOT, freeze=False
    )
    require(
        all(
            state["bytes"] == frozen["bytes"] and state["sha256"] == frozen["sha256"]
            for state, frozen in zip(states, plan["files"], strict=True)
        ),
        "raw lifecycle inventory drifted after plan freeze",
    )
    reconciled = 0
    moved = 0
    for item in plan["files"]:
        if _move_one(item):
            reconciled += 1
        else:
            moved += 1
    receipt = {
        "kind": RECEIPT_KIND,
        "status": "ACCEPTED_RAW_ARCHIVE_COMPLETE",
        "plan_sha256": plan_sha,
        "execution_main_sha": plan["execution_main_sha"],
        "file_count": plan["file_count"],
        "total_bytes": plan["total_bytes"],
        "moved_files": moved,
        "reconciled_files": reconciled,
        "files": [
            {
                "filename": item["filename"],
                "sha256": item["sha256"],
                "bytes": item["bytes"],
                "archive_path": item["archive_path"],
            }
            for item in plan["files"]
        ],
        "database_evidence_sha256": plan["database_evidence_sha256"],
        "accepted_storage_topology_evidence": plan["accepted_storage_topology_evidence"],
        "postgres_target_evidence": plan["postgres_target_evidence"],
        "source_deleted": False,
        "source_archived_within_f": True,
        "serving_cutover_authorized": False,
        "current_registry_assertion_authorized": False,
    }
    GOV.mkdir(parents=True, exist_ok=True)
    require(not RECEIPT.exists(), "GB raw archive receipt already exists")
    e_stage.write_json_exclusive(RECEIPT, receipt)
    return {**receipt, "receipt_path": str(RECEIPT), "receipt_sha256": sha256_file(RECEIPT)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()
    if args.preflight_only:
        require(not args.plan and not args.authority_token, "preflight accepts no Apply arguments")
        print(json.dumps(make_plan(), ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        require(
            not args.plan
            and not args.authority_token
            and args.freeze_plan.parent.resolve() == GOV.resolve(),
            "freeze plan must be under governed #875",
        )
        plan = make_plan()
        e_stage.write_json_exclusive(args.freeze_plan, plan)
        print("GB_RAW_ARCHIVE_PLAN_SHA256=" + sha256_file(args.freeze_plan), flush=True)
        print("GB_RAW_ARCHIVE_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        return
    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and sha256_file(args.plan) == args.plan_sha,
        "exact frozen GB raw archive plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    authorize(plan, args.plan_sha, args.authority_token)
    print(json.dumps(apply(plan, args.plan_sha), ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
