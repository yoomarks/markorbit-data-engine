"""Governed issue-atomic import for the accepted UKIPO journal corpus.

The accepted 2026/033 pilot remains immutable.  Every other accepted issue is
committed in its own transaction and may be resumed only from exact persisted
plan, topology, source, row-identity, and receipt evidence.  Journal rows are
observations, never current registry truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.global_trademarks import gb_domestic_full_resume as domestic
from app.global_trademarks import gb_journal_e_stage as stage
from app.global_trademarks import gb_journal_pilot as pilot

PILOT_PLAN = pilot.GOV / "gb-journal-2026-033-pg-pilot-plan-r1.json"
PILOT_PLAN_SHA = "71088c0ce31d3e254e94e52811fae83769d9c1d45f38c9bc1b3c8169c24f4c8c"
PILOT_RECEIPT = pilot.GOV / "gb-journal-2026-033-pg-pilot-r1.json"
PILOT_RECEIPT_SHA = "98f710e57ede5b19a62fe347c84ca46f093a5f47dedbf3d9826c787ba69f6094"
PILOT_OPERATOR_SHA = "1d2bb1a194b44c25b2ca20c485b6a7e31147f87975e6272d11dae0f3bd12622d"
JOURNAL_STAGE_PLAN_SHA = "31d34b4e97319cfd6789e199512b56418b9bcfa77ebfb8ff354a5b5bd133cff7"
JOURNAL_STAGE_RECEIPT_SHA = "dfcc0c240b482e374fb5dbf6a9407b937ae12461edd8fb212e275abe0b084cc1"
ACCEPTED_ISSUES = 78
REMAINING_ISSUES = ACCEPTED_ISSUES - 1
RECEIPT = pilot.GOV / "gb-journal-accepted-issues-pg-full-r1.json"
PLAN_KIND = "GB_UKIPO_JOURNAL_ACCEPTED_ISSUES_PG_FULL_PLAN_V1"
ISSUE_RECEIPT_KIND = "GB_UKIPO_JOURNAL_ISSUE_PG_RECEIPT_V1"


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def _load_exact_json(path: Path, expected_sha: str, label: str) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), f"{label} missing or symlinked")
    require(stage.sha256_file(path) == expected_sha, f"{label} SHA drift")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def verify_pilot_acceptance() -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _load_exact_json(PILOT_PLAN, PILOT_PLAN_SHA, "journal pilot plan")
    receipt = _load_exact_json(PILOT_RECEIPT, PILOT_RECEIPT_SHA, "journal pilot receipt")
    require(
        plan.get("kind") == "GB_UKIPO_JOURNAL_2026_033_PG_PILOT_PLAN_V2"
        and plan.get("issue") == pilot.PILOT_ISSUE
        and plan.get("operator_sha256") == PILOT_OPERATOR_SHA
        and plan.get("journal_observation_only") is True
        and plan.get("current_state_verified") is False
        and receipt.get("kind") == "GB_UKIPO_JOURNAL_2026_033_PG_PILOT_RECEIPT_V2"
        and receipt.get("status") == "ISSUE_ATOMIC_PILOT_COMPLETE_OBSERVATION_ONLY"
        and receipt.get("plan_sha256") == PILOT_PLAN_SHA
        and receipt.get("issue") == pilot.PILOT_ISSUE
        and receipt.get("journal_observation_only") is True
        and receipt.get("current_state_verified") is False
        and receipt.get("full_journal_ingest_authorized") is False,
        "accepted journal pilot evidence drift",
    )
    return plan, receipt


def verify_full_stage(plan_path: Path, plan_sha: str) -> dict[str, Any]:
    require(
        plan_sha == JOURNAL_STAGE_PLAN_SHA,
        "accepted journal E-stage plan SHA required",
    )
    pilot_proof = pilot.verify_journal_stage(plan_path, plan_sha)
    require(
        pilot_proof["receipt_sha256"] == JOURNAL_STAGE_RECEIPT_SHA,
        "accepted journal E-stage receipt SHA drift",
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    issues = sorted(plan["issues"], key=lambda value: value["issue"])
    issue_names = [value["issue"] for value in issues]
    require(
        len(issues) == ACCEPTED_ISSUES
        and len(set(issue_names)) == ACCEPTED_ISSUES
        and issue_names.count(pilot.PILOT_ISSUE) == 1
        and issue_names == sorted(issue_names)
        and "2026-018" in issue_names
        and "2026-035" in issue_names,
        "accepted 78-issue journal set drift",
    )
    issue_035 = next(value for value in issues if value["issue"] == "2026-035")
    issue_039 = next(value for value in issues if value["issue"] == "2026-039")
    require(
        issue_035["source_sha256"] != issue_039["source_sha256"]
        and issue_035["records_sha256"] != issue_039["records_sha256"],
        "corrected 2026/035 is not distinct from 2026/039",
    )
    return {"pilot": pilot_proof, "plan": plan, "issues": issues}


def _issue_records_path(issue: dict[str, Any]) -> Path:
    return stage.TARGET_ROOT / (f"{issue['issue']}-{issue['source_sha256'][:12]}-details.jsonl")


def issue_records(
    issue: dict[str, Any], *, retain_records: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issue_name = issue["issue"]
    records_path = _issue_records_path(issue)
    require(
        records_path.is_file()
        and not records_path.is_symlink()
        and stage.sha256_file(records_path) == issue["records_sha256"],
        f"journal {issue_name} records identity drift",
    )
    records: list[dict[str, Any]] = []
    ordered_digest = hashlib.sha256()
    counts = {
        "notices": 0,
        "goods": 0,
        "parties": 0,
        "visuals": 0,
        "missing_visuals": 0,
    }
    seen_members: set[str] = set()
    with records_path.open("r", encoding="utf-8", newline="") as stream:
        for ordinal, line in enumerate(stream, 1):
            payload = json.loads(line)
            source_member = payload.get("source_member")
            require(
                isinstance(payload, dict)
                and payload.get("kind") == "UKIPO_JOURNAL_DETAIL_STAGE_V1"
                and payload.get("issue") == issue_name
                and payload.get("source_zip_sha256") == issue["source_sha256"]
                and payload.get("mark_family") in {"UK", "WO"}
                and isinstance(payload.get("mark_id"), str)
                and re.fullmatch(r"(?:UK|WO)[0-9A-Z]+", payload["mark_id"]) is not None
                and isinstance(source_member, str)
                and source_member.startswith(issue_name + "/")
                and source_member.endswith(".html")
                and source_member not in seen_members
                and re.fullmatch(r"[0-9a-f]{64}", payload.get("detail_html_sha256", "")) is not None
                and isinstance(payload.get("journal_title_raw"), str)
                and isinstance(payload.get("mark_text"), list)
                and isinstance(payload.get("goods_by_class"), list)
                and isinstance(payload.get("applicants"), list)
                and isinstance(payload.get("representatives"), list)
                and isinstance(payload.get("mark_images"), list),
                f"journal {issue_name} detail contract drift at ordinal {ordinal}",
            )
            seen_members.add(source_member)
            row_sha = hashlib.sha256(line.encode("utf-8")).hexdigest()
            ordered_digest.update(f"{ordinal}:{row_sha}\n".encode())

            goods_rows = []
            for class_ordinal, group in enumerate(payload["goods_by_class"], 1):
                require(
                    isinstance(group, dict)
                    and type(group.get("class")) is int
                    and 1 <= group["class"] <= 45
                    and isinstance(group.get("goods"), list)
                    and all(isinstance(text, str) and text for text in group["goods"]),
                    f"journal {issue_name} goods drift at ordinal {ordinal}",
                )
                goods_rows.extend(
                    (class_ordinal, goods_ordinal, group["class"], text)
                    for goods_ordinal, text in enumerate(group["goods"], 1)
                )

            party_rows = []
            for role, field in (
                ("APPLICANT", "applicants"),
                ("REPRESENTATIVE", "representatives"),
            ):
                require(
                    all(isinstance(name, str) and name for name in payload[field]),
                    f"journal {issue_name} party drift at ordinal {ordinal}",
                )
                party_rows.extend(
                    (role, party_ordinal, name)
                    for party_ordinal, name in enumerate(payload[field], 1)
                )

            visual_rows = []
            for image in payload["mark_images"]:
                image_ordinal = image.get("ordinal")
                require(
                    type(image_ordinal) is int and image_ordinal > 0,
                    f"journal {issue_name} visual ordinal invalid",
                )
                digest = image.get("sha256")
                if digest is None:
                    require(
                        payload.get("image_evidence_complete") is False
                        and image.get("asset_relative_path") is None
                        and image.get("bytes") is None
                        and image.get("source_member") is None
                        and image.get("source_member_resolution") == "MISSING_SOURCE_MEMBER",
                        f"journal {issue_name} unapproved missing visual",
                    )
                    complete = False
                    counts["missing_visuals"] += 1
                else:
                    relative = image.get("asset_relative_path")
                    visual_root = stage.VISUAL_ROOT.resolve()
                    require(
                        re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                        and isinstance(relative, str)
                        and "\\" not in relative
                        and not Path(relative).is_absolute()
                        and Path(relative).stem == digest
                        and type(image.get("bytes")) is int
                        and image["bytes"] > 0
                        and isinstance(image.get("source_member"), str),
                        f"journal {issue_name} visual identity invalid",
                    )
                    candidate = visual_root / relative
                    visual = candidate.resolve()
                    require(
                        visual.is_relative_to(visual_root)
                        and candidate.is_file()
                        and not candidate.is_symlink()
                        and visual.stat().st_size == image["bytes"]
                        and stage.sha256_file(visual) == digest,
                        f"journal {issue_name} F visual identity drift",
                    )
                    complete = True
                visual_rows.append(
                    (
                        image_ordinal,
                        image.get("source_member"),
                        image.get("declared_src"),
                        digest,
                        image.get("bytes"),
                        image.get("asset_relative_path"),
                        image.get("source_member_resolution"),
                        image.get("thumbnail_present") is True,
                        complete,
                    )
                )

            if retain_records:
                records.append(
                    {
                        "ordinal": ordinal,
                        "row_sha256": row_sha,
                        "payload": payload,
                        "goods": goods_rows,
                        "parties": party_rows,
                        "visuals": visual_rows,
                    }
                )
            counts["notices"] += 1
            counts["goods"] += len(goods_rows)
            counts["parties"] += len(party_rows)
            counts["visuals"] += len(visual_rows)
    require(
        counts["notices"] == issue["detail_rows"]
        and counts["visuals"] == issue["image_links"]
        and counts["missing_visuals"] == issue["missing_image_links"],
        f"journal {issue_name} cardinality drift",
    )
    counts["ordered_row_identity_sha256"] = ordered_digest.hexdigest()
    return records, counts


def issue_summaries(proof: dict[str, Any]) -> list[dict[str, Any]]:
    summaries = []
    for issue in proof["issues"]:
        _, counts = issue_records(issue, retain_records=False)
        summaries.append(
            {
                "issue": issue["issue"],
                "source_zip_sha256": issue["source_sha256"],
                "stage_records_sha256": issue["records_sha256"],
                "expected_notices": counts["notices"],
                "expected_goods": counts["goods"],
                "expected_parties": counts["parties"],
                "expected_visuals": counts["visuals"],
                "expected_missing_visuals": counts["missing_visuals"],
                "ordered_row_identity_sha256": counts["ordered_row_identity_sha256"],
            }
        )
    return summaries


def _aggregate(summaries: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(item[key] for item in summaries)
        for key in (
            "expected_notices",
            "expected_goods",
            "expected_parties",
            "expected_visuals",
            "expected_missing_visuals",
        )
    }


def _issue_set_sha(summaries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in summaries:
        digest.update(
            (
                f"{item['issue']}:{item['source_zip_sha256']}:"
                f"{item['stage_records_sha256']}:{item['expected_notices']}:"
                f"{item['expected_goods']}:{item['expected_parties']}:"
                f"{item['expected_visuals']}:{item['expected_missing_visuals']}:"
                f"{item['ordered_row_identity_sha256']}\n"
            ).encode()
        )
    return digest.hexdigest()


def make_plan(
    proof: dict[str, Any],
    summaries: list[dict[str, Any]],
    *,
    execution_main: str | None = None,
    storage_topology: dict[str, Any] | None = None,
    postgres_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require(len(summaries) == ACCEPTED_ISSUES, "journal summary issue count drift")
    reserve = domestic.require_e_disk_reserve()["E"]
    execution_main = domestic.current_git_head() if execution_main is None else execution_main
    require(re.fullmatch(r"[0-9a-f]{40}", execution_main) is not None, "invalid main SHA")
    storage_topology = (
        pilot.verify_accepted_storage_topology() if storage_topology is None else storage_topology
    )
    postgres_target = (
        pilot.read_live_postgres_target() if postgres_target is None else postgres_target
    )
    aggregate = _aggregate(summaries)
    return {
        "kind": PLAN_KIND,
        "status": "FROZEN_NO_APPLY",
        "execution_main_sha": execution_main,
        "accepted_issue_count": ACCEPTED_ISSUES,
        "pilot_issue": pilot.PILOT_ISSUE,
        "remaining_issue_count": REMAINING_ISSUES,
        "issues": summaries,
        "accepted_issue_set_sha256": _issue_set_sha(summaries),
        **aggregate,
        "journal_e_stage_plan_sha256": proof["pilot"]["plan_sha256"],
        "journal_e_stage_receipt_sha256": proof["pilot"]["receipt_sha256"],
        "journal_e_stage_manifest_sha256": proof["pilot"]["manifest_sha256"],
        "pilot_plan_sha256": PILOT_PLAN_SHA,
        "pilot_receipt_sha256": PILOT_RECEIPT_SHA,
        "pilot_operator_sha256": PILOT_OPERATOR_SHA,
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "accepted_storage_topology_evidence": storage_topology,
        "postgres_target_evidence": postgres_target,
        "original_visual_authority_drive": "F",
        "operator_sha256": domestic.canonical_text_sha(Path(__file__)),
        "schema_sql_sha256": hashlib.sha256(pilot.SCHEMA_SQL.encode()).hexdigest(),
        "disk_reserve_gate": {"E": {"reserve_bytes": int(reserve["reserve_bytes"])}},
        "issue_atomic_transactions": True,
        "pilot_issue_immutable": True,
        "journal_observation_only": True,
        "current_state_verified": False,
        "historical_stock_mutation_authorized": False,
        "serving_cutover_authorized": False,
        "clickhouse_apply_authorized": False,
        "source_cleanup_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    issues = plan.get("issues", [])
    require(
        plan.get("kind") == PLAN_KIND
        and plan.get("status") == "FROZEN_NO_APPLY"
        and plan.get("accepted_issue_count") == ACCEPTED_ISSUES
        and plan.get("pilot_issue") == pilot.PILOT_ISSUE
        and plan.get("remaining_issue_count") == REMAINING_ISSUES
        and isinstance(issues, list)
        and len(issues) == ACCEPTED_ISSUES
        and [item.get("issue") for item in issues] == sorted(item.get("issue") for item in issues)
        and len({item.get("issue") for item in issues}) == ACCEPTED_ISSUES
        and _issue_set_sha(issues) == plan.get("accepted_issue_set_sha256")
        and _aggregate(issues)
        == {
            key: plan.get(key)
            for key in (
                "expected_notices",
                "expected_goods",
                "expected_parties",
                "expected_visuals",
                "expected_missing_visuals",
            )
        }
        and plan.get("journal_e_stage_plan_sha256") == JOURNAL_STAGE_PLAN_SHA
        and plan.get("journal_e_stage_receipt_sha256") == JOURNAL_STAGE_RECEIPT_SHA
        and plan.get("pilot_plan_sha256") == PILOT_PLAN_SHA
        and plan.get("pilot_receipt_sha256") == PILOT_RECEIPT_SHA
        and plan.get("pilot_operator_sha256") == PILOT_OPERATOR_SHA
        and plan.get("target_database") == "markorbit"
        and plan.get("target_database_physical_drive") == "E"
        and plan.get("future_query_storage_placement") == "hot_global"
        and plan.get("original_visual_authority_drive") == "F"
        and plan.get("operator_sha256") == domestic.canonical_text_sha(Path(__file__))
        and plan.get("schema_sql_sha256") == hashlib.sha256(pilot.SCHEMA_SQL.encode()).hexdigest()
        and set(plan.get("disk_reserve_gate", {})) == {"E"}
        and int(plan["disk_reserve_gate"]["E"]["reserve_bytes"]) > 0
        and plan.get("issue_atomic_transactions") is True
        and plan.get("pilot_issue_immutable") is True
        and plan.get("journal_observation_only") is True
        and plan.get("current_state_verified") is False
        and plan.get("historical_stock_mutation_authorized") is False
        and plan.get("serving_cutover_authorized") is False
        and plan.get("clickhouse_apply_authorized") is False
        and plan.get("source_cleanup_authorized") is False,
        "journal full frozen plan/operator mismatch",
    )
    expected = (
        f"GO #855 GB-JOURNAL-FULL-ACCEPTED {plan_sha} REMAINING-{REMAINING_ISSUES}-ISSUE-ATOMIC"
    )
    require(token == expected, "exact GB journal full authority required")


def _row_sets(
    issue: str, records: list[dict[str, Any]]
) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
    notice_rows = []
    goods_rows = []
    party_rows = []
    visual_rows = []
    for record in records:
        ordinal = record["ordinal"]
        payload = record["payload"]
        notice_rows.append(
            (
                issue,
                ordinal,
                payload["source_zip_sha256"],
                payload["source_member"],
                payload["detail_html_sha256"],
                record["row_sha256"],
                payload["mark_family"],
                payload["mark_id"],
                payload["journal_title_raw"],
                payload.get("regdate_raw"),
                json.dumps(payload["mark_text"], ensure_ascii=False, sort_keys=True),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        )
        goods_rows.extend((issue, ordinal, *row) for row in record["goods"])
        party_rows.extend((issue, ordinal, *row) for row in record["parties"])
        visual_rows.extend((issue, ordinal, *row) for row in record["visuals"])
    return notice_rows, goods_rows, party_rows, visual_rows


def _issue_receipt(
    plan: dict[str, Any], plan_sha: str, issue: dict[str, Any], reserve: dict[str, Any]
) -> dict[str, Any]:
    return {
        "kind": ISSUE_RECEIPT_KIND,
        "status": "ISSUE_ATOMIC_COMPLETE_OBSERVATION_ONLY",
        "full_plan_sha256": plan_sha,
        "execution_main_sha": plan["execution_main_sha"],
        **issue,
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "accepted_storage_topology_evidence": plan["accepted_storage_topology_evidence"],
        "postgres_target_evidence": plan["postgres_target_evidence"],
        "original_visual_authority_drive": "F",
        "journal_observation_only": True,
        "current_state_verified": False,
        "serving_cutover_authorized": False,
        "source_cleanup_authorized": False,
        "disk_reserve_apply_snapshot": reserve,
    }


def _verify_issue_rows(cur: Any, issue: dict[str, Any]) -> None:
    name = issue["issue"]
    cur.execute(
        """
        SELECT
         (SELECT count(*) FROM trademark_gb.journal_notice_v1 WHERE issue=%s) notices,
         (SELECT count(*) FROM trademark_gb.journal_goods_v1 WHERE issue=%s) goods,
         (SELECT count(*) FROM trademark_gb.journal_party_v1 WHERE issue=%s) parties,
         (SELECT count(*) FROM trademark_gb.journal_visual_v1 WHERE issue=%s) visuals,
         (SELECT count(*) FROM trademark_gb.journal_visual_v1
           WHERE issue=%s AND NOT evidence_complete) missing_visuals,
         (SELECT count(*) FROM trademark_gb.journal_notice_v1
           WHERE issue=%s AND current_state_verified) unapproved_current,
         (SELECT count(*) FROM trademark_gb.journal_notice_v1
           WHERE issue=%s AND NOT journal_observation_only) nonobservation
        """,
        (name,) * 7,
    )
    counts = cur.fetchone()
    require(
        all(
            counts[key] == issue[f"expected_{key}"]
            for key in ("notices", "goods", "parties", "visuals", "missing_visuals")
        )
        and counts["unapproved_current"] == 0
        and counts["nonobservation"] == 0,
        f"journal {name} live count/currentness drift",
    )
    cur.execute(
        """
        SELECT notice_ordinal,source_row_sha256
        FROM trademark_gb.journal_notice_v1 WHERE issue=%s ORDER BY notice_ordinal
        """,
        (name,),
    )
    digest = hashlib.sha256()
    for expected, row in enumerate(cur.fetchall(), 1):
        require(row["notice_ordinal"] == expected, f"journal {name} ordinal drift")
        digest.update(f"{expected}:{row['source_row_sha256']}\n".encode())
    require(
        digest.hexdigest() == issue["ordered_row_identity_sha256"],
        f"journal {name} ordered row identity drift",
    )


def _as_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _reconcile_issue(
    cur: Any, plan: dict[str, Any], plan_sha: str, issue: dict[str, Any]
) -> dict[str, Any]:
    cur.execute(
        """
        SELECT source_zip_sha256,stage_records_sha256,expected_notices,expected_goods,
               expected_parties,expected_visuals,expected_missing_visuals,plan_sha256,
               execution_main_sha,plan_evidence,storage_topology_evidence,
               postgres_target_evidence,receipt_evidence,receipt_sha256,status,
               checkpoint_notice_ordinal
        FROM trademark_gb.journal_issue_ingest_run_v1 WHERE issue=%s
        """,
        (issue["issue"],),
    )
    row = cur.fetchone()
    require(row is not None, f"journal {issue['issue']} run evidence missing")
    payload = _as_object(row["receipt_evidence"], "persisted issue receipt")
    reserve = _as_object(payload.get("disk_reserve_apply_snapshot"), "persisted E reserve snapshot")
    require(
        row["source_zip_sha256"] == issue["source_zip_sha256"]
        and row["stage_records_sha256"] == issue["stage_records_sha256"]
        and all(
            row[f"expected_{key}"] == issue[f"expected_{key}"]
            for key in ("notices", "goods", "parties", "visuals", "missing_visuals")
        )
        and row["plan_sha256"] == plan_sha
        and row["execution_main_sha"] == plan["execution_main_sha"]
        and _as_object(row["plan_evidence"], "persisted full plan") == plan
        and _as_object(row["storage_topology_evidence"], "persisted topology")
        == plan["accepted_storage_topology_evidence"]
        and _as_object(row["postgres_target_evidence"], "persisted target")
        == plan["postgres_target_evidence"]
        and payload == _issue_receipt(plan, plan_sha, issue, reserve)
        and row["receipt_sha256"] == pilot._canonical_json_sha(payload)
        and row["status"] == "COMPLETE"
        and row["checkpoint_notice_ordinal"] == issue["expected_notices"],
        f"journal {issue['issue']} committed evidence drift",
    )
    _verify_issue_rows(cur, issue)
    return payload


def _insert_issue(
    cur: Any,
    records: list[dict[str, Any]],
    plan: dict[str, Any],
    plan_sha: str,
    issue: dict[str, Any],
    reserve: dict[str, Any],
) -> dict[str, Any]:
    payload = _issue_receipt(plan, plan_sha, issue, reserve)
    notice_rows, goods_rows, party_rows, visual_rows = _row_sets(issue["issue"], records)
    cur.execute(
        """
        INSERT INTO trademark_gb.journal_issue_ingest_run_v1(
         issue,source_zip_sha256,stage_records_sha256,expected_notices,
         expected_goods,expected_parties,expected_visuals,expected_missing_visuals,
         plan_sha256,execution_main_sha,plan_evidence,storage_topology_evidence,
         postgres_target_evidence,receipt_evidence,receipt_sha256,status
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,
                  %s::jsonb,%s,'RUNNING')
        """,
        (
            issue["issue"],
            issue["source_zip_sha256"],
            issue["stage_records_sha256"],
            issue["expected_notices"],
            issue["expected_goods"],
            issue["expected_parties"],
            issue["expected_visuals"],
            issue["expected_missing_visuals"],
            plan_sha,
            plan["execution_main_sha"],
            json.dumps(plan, ensure_ascii=False, sort_keys=True),
            json.dumps(plan["accepted_storage_topology_evidence"], sort_keys=True),
            json.dumps(plan["postgres_target_evidence"], sort_keys=True),
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            pilot._canonical_json_sha(payload),
        ),
    )
    for sql, rows, label in (
        (pilot.NOTICE_SQL, notice_rows, "notice"),
        (pilot.GOODS_SQL, goods_rows, "goods"),
        (pilot.PARTY_SQL, party_rows, "party"),
        (pilot.VISUAL_SQL, visual_rows, "visual"),
    ):
        cur.executemany(sql, rows)
        require(cur.rowcount == len(rows), f"journal {issue['issue']} {label} insert drift")
    _verify_issue_rows(cur, issue)
    cur.execute(
        """
        UPDATE trademark_gb.journal_issue_ingest_run_v1
        SET status='COMPLETE',checkpoint_notice_ordinal=%s,completed_at=now()
        WHERE issue=%s AND status='RUNNING' AND checkpoint_notice_ordinal=0
        """,
        (issue["expected_notices"], issue["issue"]),
    )
    require(cur.rowcount == 1, f"journal {issue['issue']} completion transition failed")
    return payload


def _existing_issues(cur: Any) -> list[str]:
    cur.execute("SELECT issue FROM trademark_gb.journal_issue_ingest_run_v1 ORDER BY issue")
    return [row["issue"] for row in cur.fetchall()]


def validate_committed_order(existing: list[str], planned: list[str]) -> list[str]:
    require(
        existing
        and existing[0] <= pilot.PILOT_ISSUE
        and pilot.PILOT_ISSUE in existing
        and set(existing).issubset(set(planned)),
        "journal database contains unplanned issue state",
    )
    remaining = [issue for issue in planned if issue != pilot.PILOT_ISSUE]
    committed = [issue for issue in existing if issue != pilot.PILOT_ISSUE]
    require(
        committed == remaining[: len(committed)],
        "journal committed issues are not an exact resumable prefix",
    )
    return committed


def verify_initial_prestate(plan: dict[str, Any], pilot_plan: dict[str, Any]) -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            require(
                all(value is not None for value in pilot._relation_presence(cur)),
                "journal pilot schema is incomplete",
            )
            pilot._reconcile_committed_pilot(cur, pilot_plan, PILOT_PLAN_SHA)
            existing = _existing_issues(cur)
            require(
                existing == [pilot.PILOT_ISSUE],
                "full-journal plan freeze requires exact pilot-only prestate",
            )
    return {"issues": existing, "status": "PILOT_ONLY_ACCEPTED"}


def _apply_or_reconcile_issue(
    records: list[dict[str, Any]],
    plan: dict[str, Any],
    plan_sha: str,
    issue: dict[str, Any],
    storage: dict[str, Any],
    reserve: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            live_target = pilot._docker_postgres_identity(pilot._postgres_database_identity(cur))
            pilot.require_plan_target_matches_live(plan, storage, live_target)
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"gb-journal-full:{plan_sha}",),
            )
            cur.execute(
                "SELECT 1 FROM trademark_gb.journal_issue_ingest_run_v1 WHERE issue=%s",
                (issue["issue"],),
            )
            if cur.fetchone() is not None:
                payload = _reconcile_issue(cur, plan, plan_sha, issue)
                conn.commit()
                return payload, True
            require(reserve is not None, "new journal issue requires live E reserve proof")
            payload = _insert_issue(cur, records, plan, plan_sha, issue, reserve)
            conn.commit()
            return payload, False


def _verify_full_poststate(
    plan: dict[str, Any], plan_sha: str, pilot_plan: dict[str, Any]
) -> list[str]:
    from app.db import postgres_conn

    issue_receipt_shas: list[str] = []
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            pilot._reconcile_committed_pilot(cur, pilot_plan, PILOT_PLAN_SHA)
            existing = _existing_issues(cur)
            planned = [item["issue"] for item in plan["issues"]]
            require(existing == planned, "journal full issue set incomplete")
            for issue in plan["issues"]:
                if issue["issue"] == pilot.PILOT_ISSUE:
                    issue_receipt_shas.append(PILOT_RECEIPT_SHA)
                else:
                    payload = _reconcile_issue(cur, plan, plan_sha, issue)
                    issue_receipt_shas.append(pilot._canonical_json_sha(payload))
            cur.execute(
                """
                SELECT
                 (SELECT count(*) FROM trademark_gb.journal_notice_v1) notices,
                 (SELECT count(*) FROM trademark_gb.journal_goods_v1) goods,
                 (SELECT count(*) FROM trademark_gb.journal_party_v1) parties,
                 (SELECT count(*) FROM trademark_gb.journal_visual_v1) visuals,
                 (SELECT count(*) FROM trademark_gb.journal_visual_v1
                   WHERE NOT evidence_complete) missing_visuals,
                 (SELECT count(*) FROM trademark_gb.journal_notice_v1
                   WHERE current_state_verified) unapproved_current,
                 (SELECT count(*) FROM trademark_gb.journal_notice_v1
                   WHERE NOT journal_observation_only) nonobservation
                """
            )
            counts = cur.fetchone()
            require(
                all(
                    counts[key] == plan[f"expected_{key}"]
                    for key in (
                        "notices",
                        "goods",
                        "parties",
                        "visuals",
                        "missing_visuals",
                    )
                )
                and counts["unapproved_current"] == 0
                and counts["nonobservation"] == 0,
                "journal full aggregate count/currentness drift",
            )
    return issue_receipt_shas


def _full_receipt(
    plan: dict[str, Any],
    plan_sha: str,
    issue_receipt_shas: list[str],
) -> dict[str, Any]:
    return {
        "kind": "GB_UKIPO_JOURNAL_ACCEPTED_ISSUES_PG_FULL_RECEIPT_V1",
        "status": "ALL_ACCEPTED_ISSUES_COMPLETE_OBSERVATION_ONLY",
        "plan_sha256": plan_sha,
        "execution_main_sha": plan["execution_main_sha"],
        "accepted_issue_count": ACCEPTED_ISSUES,
        "pilot_issue": pilot.PILOT_ISSUE,
        "remaining_issues_committed": REMAINING_ISSUES,
        "accepted_issue_set_sha256": plan["accepted_issue_set_sha256"],
        "issue_receipt_sha256s": issue_receipt_shas,
        **{
            key.removeprefix("expected_") + "_committed": plan[key]
            for key in (
                "expected_notices",
                "expected_goods",
                "expected_parties",
                "expected_visuals",
                "expected_missing_visuals",
            )
        },
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "accepted_storage_topology_evidence": plan["accepted_storage_topology_evidence"],
        "postgres_target_evidence": plan["postgres_target_evidence"],
        "original_visual_authority_drive": "F",
        "journal_observation_only": True,
        "current_state_verified": False,
        "serving_cutover_authorized": False,
        "source_cleanup_authorized": False,
    }


def apply_full(
    proof: dict[str, Any],
    plan: dict[str, Any],
    plan_sha: str,
    pilot_plan: dict[str, Any],
) -> dict[str, Any]:
    from app.db import postgres_conn

    domestic.require_live_clean_main(plan["execution_main_sha"])
    storage = pilot.verify_accepted_storage_topology()
    postgres = pilot.read_live_postgres_target()
    pilot.require_plan_target_matches_live(plan, storage, postgres)

    summary_by_issue = {item["issue"]: item for item in plan["issues"]}
    planned = list(summary_by_issue)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            pilot._reconcile_committed_pilot(cur, pilot_plan, PILOT_PLAN_SHA)
            committed = validate_committed_order(_existing_issues(cur), planned)
    reserve = (
        None
        if len(committed) == REMAINING_ISSUES
        else domestic.verify_apply_disk_reserve(plan["disk_reserve_gate"])
    )

    for accepted in proof["issues"]:
        name = accepted["issue"]
        if name == pilot.PILOT_ISSUE:
            continue
        records, counts = issue_records(accepted, retain_records=True)
        expected = summary_by_issue[name]
        require(
            expected
            == {
                "issue": name,
                "source_zip_sha256": accepted["source_sha256"],
                "stage_records_sha256": accepted["records_sha256"],
                "expected_notices": counts["notices"],
                "expected_goods": counts["goods"],
                "expected_parties": counts["parties"],
                "expected_visuals": counts["visuals"],
                "expected_missing_visuals": counts["missing_visuals"],
                "ordered_row_identity_sha256": counts["ordered_row_identity_sha256"],
            },
            f"journal {name} differs from frozen plan",
        )
        _, reconciled = _apply_or_reconcile_issue(
            records, plan, plan_sha, expected, storage, reserve
        )
        print(
            f"GB_JOURNAL_FULL_PROGRESS issue={name} reconciled={str(reconciled).lower()}",
            flush=True,
        )

    issue_receipt_shas = _verify_full_poststate(plan, plan_sha, pilot_plan)
    payload = _full_receipt(plan, plan_sha, issue_receipt_shas)
    receipt_sha = pilot._atomic_publish_receipt(RECEIPT, payload)
    return {**payload, "receipt_path": str(RECEIPT), "receipt_sha256": receipt_sha}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-plan", type=Path, required=True)
    parser.add_argument("--stage-plan-sha", required=True)
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
    pilot_plan, _ = verify_pilot_acceptance()
    proof = verify_full_stage(args.stage_plan, args.stage_plan_sha)
    summaries = issue_summaries(proof)
    proposed = make_plan(proof, summaries, execution_main=execution_main)

    if args.preflight_only:
        verify_initial_prestate(proposed, pilot_plan)
        require(not args.plan and not args.authority_token, "preflight accepts no Apply arguments")
        print(json.dumps(proposed, ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        verify_initial_prestate(proposed, pilot_plan)
        require(
            not args.plan
            and not args.authority_token
            and args.freeze_plan.parent.resolve() == pilot.GOV.resolve(),
            "freeze plan must be immutable under GB governed plans",
        )
        plan_sha = pilot._atomic_publish_receipt(args.freeze_plan, proposed)
        print("GB_JOURNAL_FULL_PLAN_SHA256=" + plan_sha, flush=True)
        print("GB_JOURNAL_FULL_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        return

    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and stage.sha256_file(args.plan) == args.plan_sha,
        "exact frozen GB journal full plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    require(plan == proposed, "journal full plan differs from live frozen state")
    authorize(plan, args.plan_sha, args.authority_token)
    result = apply_full(proof, plan, args.plan_sha, pilot_plan)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
