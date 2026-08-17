from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import postgres_conn


ADMIN_PROGRESS_VERSION = "MARKORBIT_ADMIN_PROGRESS_V1"
_DOMAIN_JURISDICTION = {
    "CN": "CN",
    "US_APPLICATION": "US",
    "US_ASSIGNMENT": "US_ASSIGNMENT",
    "US_TTAB": "US_TTAB",
}
_ACTIVE_STATUSES = {"QUEUED", "RUNNING"}


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in rows:
        jurisdiction = str(row.get("jurisdiction") or "")
        status = str(row.get("status") or "UNKNOWN").upper()
        result.setdefault(jurisdiction, {})[status] = int(row.get("row_count") or 0)
    return result


def _cn_phase(
    *,
    package: dict[str, Any] | None,
    stage_checkpoint_version: str = "",
    publish_checkpoint_version: str = "",
    current_subtask: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not package:
        return "WAITING_PACKAGE", "等待下一来源包"
    if publish_checkpoint_version:
        group = str((current_subtask or {}).get("task_group") or "").strip()
        if group:
            return "FINAL_PUBLISH", f"最终发布 · {group}"
        return "FINAL_PUBLISH", "最终发布 / 审计"
    if stage_checkpoint_version:
        return "POST_STAGE", "Stage 已完成 · 质量检查 / Materialize"
    if str(package.get("status") or "").upper() == "PROCESSING":
        return "RAW_PARSE_STAGE", "解析原始包并写入 Stage"
    return "PACKAGE_PRECHECK", "来源包安全检查 / 等待处理"


def _corpus_progress(domain: str, counts: dict[str, int]) -> dict[str, Any]:
    total = sum(int(value or 0) for value in counts.values())
    success = int(counts.get("SUCCESS") or 0)
    result: dict[str, Any] = {
        "registered_total": total,
        "success": success,
        "processing": int(counts.get("PROCESSING") or 0),
        "failed": int(counts.get("FAILED") or 0),
        "interrupted": int(counts.get("INTERRUPTED") or 0),
        "missing_file": int(counts.get("MISSING_FILE") or 0),
        "registered_pending": int(counts.get("REGISTERED") or 0),
        "status_counts": dict(sorted(counts.items())),
        "progress_pct": None,
        "progress_basis": "ACTIVITY_ONLY",
    }
    # CN clean replay registers the source corpus up front, so this denominator is
    # stable. Other domains can register manifest/source-plan items incrementally;
    # showing success/registered there would produce a misleading 100% mid-run.
    if domain == "CN" and total:
        result["progress_pct"] = round(success / total * 100.0, 2)
        result["progress_basis"] = "REGISTERED_CN_CORPUS"
    return result


def _cn_detail(package_id: str) -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(stage.checkpoint_version, '') AS stage_checkpoint_version,
                    stage.staged_at AS stage_checkpoint_at,
                    COALESCE(pub.checkpoint_version, '') AS publish_checkpoint_version,
                    pub.updated_at AS publish_checkpoint_at
                FROM control.source_package AS sp
                LEFT JOIN control.cn_package_stage_checkpoint AS stage
                  ON stage.package_id = sp.package_id
                LEFT JOIN control.cn_publish_checkpoint AS pub
                  ON pub.package_id = sp.package_id
                WHERE sp.package_id = %s
                """,
                (package_id,),
            )
            checkpoint = dict(cur.fetchone() or {})

            cur.execute(
                """
                SELECT count(*) AS internal_files_completed,
                       COALESCE(sum(physical_rows), 0) AS physical_rows,
                       COALESCE(sum(logical_rows), 0) AS logical_rows,
                       COALESCE(sum(failed_rows), 0) AS failed_rows
                FROM control.source_package_file
                WHERE package_id = %s
                """,
                (package_id,),
            )
            raw = dict(cur.fetchone() or {})

            cur.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status = 'SUCCESS') AS success_tasks,
                    count(*) FILTER (WHERE status = 'RUNNING') AS running_tasks,
                    count(*) FILTER (WHERE status = 'FAILED') AS failed_tasks,
                    count(*) AS ledger_tasks
                FROM control.cn_publish_subtask
                WHERE package_id = %s
                """,
                (package_id,),
            )
            ledger = dict(cur.fetchone() or {})

            cur.execute(
                """
                SELECT task_group, task_index, task_total, stage_table,
                       range_lower, range_upper, status, attempts,
                       started_at, completed_at, updated_at, last_error
                FROM control.cn_publish_subtask
                WHERE package_id = %s
                ORDER BY
                    CASE status WHEN 'RUNNING' THEN 0 WHEN 'FAILED' THEN 1 ELSE 2 END,
                    updated_at DESC, task_index DESC
                LIMIT 1
                """,
                (package_id,),
            )
            subtask_row = cur.fetchone()
            current_subtask = dict(subtask_row) if subtask_row else None

    stage_version = str(checkpoint.get("stage_checkpoint_version") or "")
    publish_version = str(checkpoint.get("publish_checkpoint_version") or "")
    return {
        "stage_checkpoint_version": stage_version,
        "stage_checkpoint_at": checkpoint.get("stage_checkpoint_at"),
        "publish_checkpoint_version": publish_version,
        "publish_checkpoint_at": checkpoint.get("publish_checkpoint_at"),
        "raw_parse": {
            "internal_files_completed": int(raw.get("internal_files_completed") or 0),
            "physical_rows": int(raw.get("physical_rows") or 0),
            "logical_rows": int(raw.get("logical_rows") or 0),
            "failed_rows": int(raw.get("failed_rows") or 0),
        },
        "final_publish": {
            "success_tasks": int(ledger.get("success_tasks") or 0),
            "running_tasks": int(ledger.get("running_tasks") or 0),
            "failed_tasks": int(ledger.get("failed_tasks") or 0),
            "ledger_tasks": int(ledger.get("ledger_tasks") or 0),
            "current_subtask": current_subtask,
        },
    }


def domain_progress_snapshot() -> dict[str, Any]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id::text, job_type, status, started_at, payload, metrics,
                       COALESCE(error_message, '') AS error_message
                FROM control.job_run
                WHERE trigger_type = 'ADMIN_UI'
                  AND payload->>'task_kind' = 'DOMAIN_CONTROL'
                  AND status IN ('QUEUED', 'RUNNING')
                ORDER BY started_at, run_id
                """
            )
            tasks = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT jurisdiction, status, count(*) AS row_count
                FROM control.source_package
                WHERE jurisdiction IN ('CN', 'US', 'US_ASSIGNMENT', 'US_TTAB')
                GROUP BY jurisdiction, status
                """
            )
            counts = _status_counts([dict(row) for row in cur.fetchall()])

            cur.execute(
                """
                SELECT DISTINCT ON (jurisdiction)
                       jurisdiction, package_id::text, file_name, package_kind,
                       partition_value, status, source_rank, package_sequence,
                       first_seen_at, last_seen_at, error_message
                FROM control.source_package
                WHERE jurisdiction IN ('CN', 'US', 'US_ASSIGNMENT', 'US_TTAB')
                  AND status = 'PROCESSING'
                ORDER BY jurisdiction, source_rank, package_sequence
                """
            )
            active_packages = {
                str(row["jurisdiction"]): dict(row) for row in cur.fetchall()
            }

    items: list[dict[str, Any]] = []
    for task in tasks:
        payload = dict(task.get("payload") or {})
        domain = str(payload.get("domain") or "OTHER").upper()
        jurisdiction = _DOMAIN_JURISDICTION.get(domain, domain)
        task_status = str(task.get("status") or "UNKNOWN").upper()
        package = active_packages.get(jurisdiction)
        detail: dict[str, Any] = {}
        phase = "QUEUED" if task_status == "QUEUED" else "PACKAGE_ACTIVITY"
        phase_label = "等待 worker 接管" if task_status == "QUEUED" else "正在处理来源包"
        if domain == "CN" and package:
            detail = _cn_detail(str(package["package_id"]))
            phase, phase_label = _cn_phase(
                package=package,
                stage_checkpoint_version=str(detail.get("stage_checkpoint_version") or ""),
                publish_checkpoint_version=str(detail.get("publish_checkpoint_version") or ""),
                current_subtask=(detail.get("final_publish") or {}).get("current_subtask"),
            )

        stop_requested = bool(payload.get("stop_requested") or False)
        if stop_requested and task_status == "RUNNING":
            phase = "STOPPING"
            phase_label = "已请求停止 · 当前包结束后安全停止"

        items.append(
            {
                "run_id": str(task.get("run_id") or ""),
                "domain": domain,
                "action": str(payload.get("action") or ""),
                "status": task_status,
                "started_at": task.get("started_at"),
                "stop_requested": stop_requested,
                "phase": phase,
                "phase_label": phase_label,
                "current_package": package,
                "corpus": _corpus_progress(domain, counts.get(jurisdiction, {})),
                "detail": detail,
                "live_metrics": dict(task.get("metrics") or {}),
                "error_message": str(task.get("error_message") or ""),
            }
        )

    return {
        "version": ADMIN_PROGRESS_VERSION,
        "read_only": True,
        "generated_at": datetime.now(timezone.utc),
        "active_count": len(items),
        "items": items,
    }
