"""Governed issue-atomic admission for one new UKIPO journal issue.

Each run binds an immutable E: structured stage, its exact official F: raw ZIP,
the accepted E-backed PostgreSQL cluster and the complete committed issue set.
After the database transaction is verified, the raw ZIP moves within F: from
incoming to archive. Journal notices remain observations, never current truth.
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
from app.global_trademarks import gb_journal_e_stage as stage
from app.global_trademarks import gb_journal_full as full
from app.global_trademarks import gb_journal_pilot as pilot
from app.global_trademarks import gb_postgres_target as target

INCOMING_ROOT = Path(r"F:\MarkOrbitData\raw\incoming\uk")
ARCHIVE_ROOT = Path(r"F:\MarkOrbitData\raw\archive\uk")
GOV = Path(r"D:\yoomarks\governed-plans\875")
PLAN_KIND = "GB_UKIPO_JOURNAL_WEEKLY_ISSUE_PLAN_V1"
RECEIPT_KIND = "GB_UKIPO_JOURNAL_WEEKLY_ISSUE_RECEIPT_V1"


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def sha256_file(path: Path) -> str:
    return stage.sha256_file(path)


def issue_set_evidence(cur: Any) -> dict[str, Any]:
    cur.execute(
        """
        SELECT issue,source_zip_sha256,stage_records_sha256,status,
               checkpoint_notice_ordinal,expected_notices
        FROM trademark_gb.journal_issue_ingest_run_v1 ORDER BY issue
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    require(
        rows
        and all(
            row["status"] == "COMPLETE"
            and row["checkpoint_notice_ordinal"] == row["expected_notices"]
            for row in rows
        ),
        "journal issue set is not completely accepted",
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            f"{row['issue']}:{row['source_zip_sha256']}:{row['stage_records_sha256']}\n".encode()
        )
    return {
        "issue_count": len(rows),
        "latest_issue": rows[-1]["issue"],
        "issue_set_sha256": digest.hexdigest(),
        "issues": [row["issue"] for row in rows],
    }


def read_issue_set() -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            return issue_set_evidence(cur)


def load_stage(manifest_path: Path, manifest_sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = stage.TARGET_ROOT.resolve()
    resolved = manifest_path.resolve()
    require(
        manifest_path.is_file()
        and not manifest_path.is_symlink()
        and resolved.is_relative_to(root)
        and resolved.parent == root
        and re.fullmatch(r"[0-9a-f]{64}", manifest_sha) is not None
        and sha256_file(manifest_path) == manifest_sha,
        "exact direct E journal manifest identity required",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    issue = manifest.get("issue")
    source_sha = manifest.get("zip_sha256")
    require(
        manifest.get("kind") == "UKIPO_JOURNAL_PILOT_MANIFEST_V1"
        and re.fullmatch(r"20[0-9]{2}-[0-9]{3}", issue or "") is not None
        and re.fullmatch(r"[0-9a-f]{64}", source_sha or "") is not None
        and manifest.get("assets_staged") is True
        and manifest.get("data_engine_ingested") is False
        and manifest.get("canonical_raw_artifact_published") is False
        and manifest.get("image_evidence_complete") is True
        and manifest.get("missing_original_image_links") == 0
        and manifest.get("asset_root") == str(stage.VISUAL_ROOT),
        "weekly journal manifest contract drift",
    )
    expected_records = root / f"{issue}-{source_sha[:12]}-details.jsonl"
    require(
        Path(manifest.get("records_path", "")).resolve() == expected_records
        and re.fullmatch(r"[0-9a-f]{64}", manifest.get("records_sha256", "")) is not None,
        "weekly journal records path/identity drift",
    )
    issue_input = {
        "issue": issue,
        "source_sha256": source_sha,
        "records_sha256": manifest["records_sha256"],
        "detail_rows": manifest["detail_count"],
        "image_links": manifest["mark_image_links"],
        "missing_image_links": 0,
    }
    _, counts = full.issue_records(issue_input, retain_records=False)
    summary = {
        "issue": issue,
        "source_zip_sha256": source_sha,
        "stage_records_sha256": manifest["records_sha256"],
        "expected_notices": counts["notices"],
        "expected_goods": counts["goods"],
        "expected_parties": counts["parties"],
        "expected_visuals": counts["visuals"],
        "expected_missing_visuals": counts["missing_visuals"],
        "ordered_row_identity_sha256": counts["ordered_row_identity_sha256"],
    }
    return manifest, summary


def raw_evidence(issue: str, source_sha: str) -> dict[str, Any]:
    incoming = INCOMING_ROOT / f"{issue}.zip"
    archived = ARCHIVE_ROOT / f"{issue}.zip"
    require(
        incoming.is_file() and not incoming.is_symlink() and not archived.exists(),
        "weekly freeze requires exact raw ZIP in F incoming only",
    )
    for ancestor in (incoming.parent, *incoming.parent.parents):
        if ancestor.exists():
            require(
                not ancestor.is_symlink()
                and not (os.name == "nt" and os.path.isjunction(ancestor)),
                "weekly incoming path may not traverse a symlink/junction",
            )
    require(sha256_file(incoming) == source_sha, "weekly raw ZIP SHA drift")
    return {
        "incoming_path": str(incoming),
        "archive_path": str(archived),
        "bytes": incoming.stat().st_size,
        "sha256": source_sha,
    }


def make_plan(manifest_path: Path, manifest_sha: str) -> dict[str, Any]:
    manifest, summary = load_stage(manifest_path, manifest_sha)
    prestate = read_issue_set()
    require(
        summary["issue"] > prestate["latest_issue"], "weekly issue is not newer than accepted set"
    )
    require(summary["issue"] not in prestate["issues"], "weekly issue already exists")
    execution_main = domestic.current_git_head()
    require(re.fullmatch(r"[0-9a-f]{40}", execution_main) is not None, "invalid main SHA")
    from app.db import postgres_conn

    with postgres_conn() as conn:
        postgres = target.capture(conn)
    reserve = domestic.require_e_disk_reserve()["E"]
    return {
        "kind": PLAN_KIND,
        "status": "FROZEN_NO_APPLY",
        "execution_main_sha": execution_main,
        "issue": summary,
        "prestate": prestate,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha,
        "raw": raw_evidence(summary["issue"], summary["source_zip_sha256"]),
        **postgres,
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "original_visual_authority_drive": "F",
        "operator_sha256": domestic.canonical_text_sha(Path(__file__)),
        "schema_sql_sha256": hashlib.sha256(pilot.SCHEMA_SQL.encode()).hexdigest(),
        "disk_reserve_gate": {"E": {"reserve_bytes": int(reserve["reserve_bytes"])}},
        "issue_atomic_transaction": True,
        "archive_after_commit": True,
        "journal_observation_only": True,
        "current_state_verified": False,
        "serving_cutover_authorized": False,
        "clickhouse_apply_authorized": False,
        "source_delete_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    issue = plan.get("issue", {})
    require(
        plan.get("kind") == PLAN_KIND
        and plan.get("status") == "FROZEN_NO_APPLY"
        and re.fullmatch(r"20[0-9]{2}-[0-9]{3}", issue.get("issue", "")) is not None
        and issue.get("issue") > plan.get("prestate", {}).get("latest_issue", "")
        and issue.get("issue") not in plan.get("prestate", {}).get("issues", [])
        and target.validate(
            {
                "accepted_storage_topology_evidence": plan.get(
                    "accepted_storage_topology_evidence"
                ),
                "postgres_target_evidence": plan.get("postgres_target_evidence"),
            }
        )
        and plan.get("target_database") == "markorbit"
        and plan.get("target_database_physical_drive") == "E"
        and plan.get("future_query_storage_placement") == "hot_global"
        and plan.get("original_visual_authority_drive") == "F"
        and plan.get("operator_sha256") == domestic.canonical_text_sha(Path(__file__))
        and plan.get("schema_sql_sha256") == hashlib.sha256(pilot.SCHEMA_SQL.encode()).hexdigest()
        and plan.get("issue_atomic_transaction") is True
        and plan.get("archive_after_commit") is True
        and plan.get("journal_observation_only") is True
        and plan.get("current_state_verified") is False
        and plan.get("serving_cutover_authorized") is False
        and plan.get("clickhouse_apply_authorized") is False
        and plan.get("source_delete_authorized") is False,
        "weekly journal frozen plan/operator mismatch",
    )
    expected = f"GO #875 GB-JOURNAL-WEEKLY {plan_sha} ISSUE-{issue['issue']}-ATOMIC-ARCHIVE"
    require(token == expected, "exact weekly journal authority required")


def _records_for(plan: dict[str, Any]) -> list[dict[str, Any]]:
    manifest, summary = load_stage(Path(plan["manifest_path"]), plan["manifest_sha256"])
    del manifest
    require(summary == plan["issue"], "weekly staged issue changed after freeze")
    issue_input = {
        "issue": summary["issue"],
        "source_sha256": summary["source_zip_sha256"],
        "records_sha256": summary["stage_records_sha256"],
        "detail_rows": summary["expected_notices"],
        "image_links": summary["expected_visuals"],
        "missing_image_links": summary["expected_missing_visuals"],
    }
    records, counts = full.issue_records(issue_input, retain_records=True)
    require(counts["notices"] == summary["expected_notices"], "weekly record count drift")
    return records


def _archive_raw(plan: dict[str, Any]) -> bool:
    raw = plan["raw"]
    incoming, archived = Path(raw["incoming_path"]), Path(raw["archive_path"])
    incoming_ok = incoming.is_file() and not incoming.is_symlink()
    archived_ok = archived.is_file() and not archived.is_symlink()
    require(not (incoming_ok and archived_ok), "weekly raw exists in incoming and archive")
    path = incoming if incoming_ok else archived
    require(
        path.is_file()
        and path.stat().st_size == raw["bytes"]
        and sha256_file(path) == raw["sha256"],
        "weekly raw lifecycle identity drift",
    )
    if archived_ok:
        return True
    archived.parent.mkdir(parents=True, exist_ok=True)
    for ancestor in (archived.parent, *archived.parent.parents):
        if ancestor.exists():
            require(
                not ancestor.is_symlink()
                and not (os.name == "nt" and os.path.isjunction(ancestor)),
                "weekly archive path may not traverse a symlink/junction",
            )
    os.rename(incoming, archived)
    require(
        not incoming.exists() and sha256_file(archived) == raw["sha256"],
        "weekly raw archive poststate drift",
    )
    return False


def _verify_committed_poststate(plan: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            target.verify(
                conn,
                {
                    "accepted_storage_topology_evidence": plan[
                        "accepted_storage_topology_evidence"
                    ],
                    "postgres_target_evidence": plan["postgres_target_evidence"],
                },
            )
            observed = issue_set_evidence(cur)
            issue = plan["issue"]
            require(
                observed["issue_count"] == plan["prestate"]["issue_count"] + 1
                and observed["issues"] == plan["prestate"]["issues"] + [issue["issue"]],
                "weekly read-only database poststate drift",
            )
            return full._reconcile_issue(cur, plan, plan_sha, issue)


def apply(plan: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    domestic.require_live_clean_main(plan["execution_main_sha"])
    records = _records_for(plan)
    issue = plan["issue"]
    from app.db import postgres_conn

    reconciled_db = False
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            target.verify(
                conn,
                {
                    "accepted_storage_topology_evidence": plan[
                        "accepted_storage_topology_evidence"
                    ],
                    "postgres_target_evidence": plan["postgres_target_evidence"],
                },
            )
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"gb-journal-weekly:{issue['issue']}",),
            )
            observed = issue_set_evidence(cur)
            if issue["issue"] in observed["issues"]:
                require(
                    observed["issue_count"] == plan["prestate"]["issue_count"] + 1
                    and observed["issues"] == plan["prestate"]["issues"] + [issue["issue"]],
                    "weekly committed set is not exact resumable poststate",
                )
                payload = full._reconcile_issue(cur, plan, plan_sha, issue)
                reconciled_db = True
            else:
                require(observed == plan["prestate"], "weekly database prestate drift")
                reserve = domestic.require_e_disk_reserve()["E"]
                payload = full._insert_issue(cur, records, plan, plan_sha, issue, reserve)
            conn.commit()
    payload = _verify_committed_poststate(plan, plan_sha)
    reconciled_archive = _archive_raw(plan)
    receipt = {
        "kind": RECEIPT_KIND,
        "status": "ISSUE_COMMITTED_AND_RAW_ARCHIVED_OBSERVATION_ONLY",
        "plan_sha256": plan_sha,
        "execution_main_sha": plan["execution_main_sha"],
        "issue": issue["issue"],
        "database_issue_receipt_sha256": pilot._canonical_json_sha(payload),
        "raw_archive_path": plan["raw"]["archive_path"],
        "raw_sha256": plan["raw"]["sha256"],
        "database_reconciled": reconciled_db,
        "archive_reconciled": reconciled_archive,
        "journal_observation_only": True,
        "current_state_verified": False,
        "source_deleted": False,
    }
    path = GOV / f"gb-journal-{issue['issue']}-weekly-receipt-v1.json"
    GOV.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        require(
            existing["plan_sha256"] == plan_sha and existing["raw_sha256"] == plan["raw"]["sha256"],
            "weekly external receipt drift",
        )
        receipt = existing
    else:
        domestic.write_json_exclusive(path, receipt)
    return {**receipt, "receipt_path": str(path), "receipt_sha256": sha256_file(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha", default="")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()
    if args.preflight_only:
        require(args.manifest is not None and not args.plan, "preflight requires one manifest")
        print(json.dumps(make_plan(args.manifest, args.manifest_sha), sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        require(
            args.manifest is not None and args.freeze_plan.parent.resolve() == GOV.resolve(),
            "freeze requires manifest and governed #875 destination",
        )
        domestic.write_json_exclusive(args.freeze_plan, make_plan(args.manifest, args.manifest_sha))
        print("GB_JOURNAL_WEEKLY_PLAN_SHA256=" + sha256_file(args.freeze_plan), flush=True)
        return
    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and sha256_file(args.plan) == args.plan_sha,
        "exact frozen weekly plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    authorize(plan, args.plan_sha, args.authority_token)
    print(json.dumps(apply(plan, args.plan_sha), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
