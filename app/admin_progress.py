from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.cn.publish_dag import CN_FINAL_PUBLISH_DAG
from app.db import postgres_conn


ADMIN_PROGRESS_VERSION = "MARKORBIT_ADMIN_PROGRESS_V2"
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


def _elapsed_seconds(value: Any, *, now: datetime | None = None) -> float | None:
    if not isinstance(value, datetime):
        return None
    current = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (current - value).total_seconds())


def _cn_dag_progress(task_group: str) -> dict[str, Any]:
    order = CN_FINAL_PUBLISH_DAG.topological_order()
    if task_group not in order:
        return {
            "dag_version": CN_FINAL_PUBLISH_DAG.version,
            "current_node_index": None,
            "node_total": len(order),
            "remaining_nodes": [],
            "remaining_node_count": None,
        }
    index = order.index(task_group)
    return {
        "dag_version": CN_FINAL_PUBLISH_DAG.version,
        "current_node_index": index + 1,
        "node_total": len(order),
        "remaining_nodes": list(order[index + 1 :]),
        "remaining_node_count": len(order) - index - 1,
    }


def _estimate_group_eta(
    *,
    task_total: int,
    success_tasks: int,
    completed_15m: int,
    completed_30m: int,
    completed_60m: int,
) -> dict[str, Any]:
    remaining = max(int(task_total or 0) - int(success_tasks or 0), 0)
    rates = {
        "15m": round(int(completed_15m or 0) * 4.0, 2),
        "30m": round(int(completed_30m or 0) * 2.0, 2),
        "60m": round(int(completed_60m or 0) * 1.0, 2),
    }
    result: dict[str, Any] = {
        "remaining_tasks": remaining,
        "completed_15m": int(completed_15m or 0),
        "completed_30m": int(completed_30m or 0),
        "completed_60m": int(completed_60m or 0),
        "tasks_per_hour": rates,
        "eta_seconds": None,
        "eta_basis": "INSUFFICIENT_DURABLE_COMPLETIONS",
    }
    if remaining <= 0:
        result["eta_seconds"] = 0.0
        result["eta_basis"] = "CURRENT_GROUP_COMPLETE"
        return result

    # Prefer a longer observation window. Short windows are used only when there
    # are enough durable completions to avoid manufacturing an ETA from one task.
    candidates = (
        ("60m", int(completed_60m or 0), 6),
        ("30m", int(completed_30m or 0), 4),
        ("15m", int(completed_15m or 0), 3),
    )
    for window, completed, minimum in candidates:
        rate = rates[window]
        if completed >= minimum and rate > 0:
            result["eta_seconds"] = round(remaining / rate * 3600.0, 1)
            result["eta_basis"] = f"CURRENT_GROUP_{window.upper()}_DURABLE_COMPLETIONS"
            break
    return result


def _progress_health(
    *,
    running_tasks: int,
    failed_tasks: int,
    last_progress_age_seconds: float | None,
) -> str:
    if failed_tasks:
        return "FAILED_SUBTASK_PRESENT"
    if not running_tasks:
        return "IDLE_OR_AUDIT"
    if last_progress_age_seconds is None:
        return "WARMING_UP"
    if last_progress_age_seconds <= 15 * 60:
        return "ACTIVE"
    if last_progress_age_seconds <= 60 * 60:
        return "QUIET_LONG_TASK"
    return "NO_RECENT_DURABLE_PROGRESS"


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
            publish_version = str(checkpoint.get("publish_checkpoint_version") or "")

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
                    count(*) AS ledger_tasks,
                    max(completed_at) FILTER (WHERE status = 'SUCCESS')
                        AS last_durable_progress_at
                FROM control.cn_publish_subtask
                WHERE package_id = %s
                  AND (%s = '' OR checkpoint_version = %s)
                """,
                (package_id, publish_version, publish_version),
            )
            ledger = dict(cur.fetchone() or {})

            cur.execute(
                """
                SELECT task_group, task_index, task_total, stage_table,
                       range_lower, range_upper, status, attempts,
                       started_at, completed_at, updated_at, last_error
                FROM control.cn_publish_subtask
                WHERE package_id = %s
                  AND (%s = '' OR checkpoint_version = %s)
                ORDER BY
                    CASE status WHEN 'RUNNING' THEN 0 WHEN 'FAILED' THEN 1 ELSE 2 END,
                    updated_at DESC, task_index DESC
                LIMIT 1
                """,
                (package_id, publish_version, publish_version),
            )
            subtask_row = cur.fetchone()
            current_subtask = dict(subtask_row) if subtask_row else None

            group_metrics: dict[str, Any] = {}
            task_group = str((current_subtask or {}).get("task_group") or "")
            if task_group:
                cur.execute(
                    """
                    SELECT
                        count(*) FILTER (WHERE status = 'SUCCESS') AS success_tasks,
                        count(*) FILTER (WHERE status = 'RUNNING') AS running_tasks,
                        count(*) FILTER (WHERE status = 'FAILED') AS failed_tasks,
                        COALESCE(max(task_total), 0) AS task_total,
                        min(started_at) AS group_started_at,
                        max(completed_at) FILTER (WHERE status = 'SUCCESS')
                            AS last_durable_progress_at,
                        count(*) FILTER (
                            WHERE status = 'SUCCESS'
                              AND completed_at >= now() - interval '15 minutes'
                        ) AS completed_15m,
                        count(*) FILTER (
                            WHERE status = 'SUCCESS'
                              AND completed_at >= now() - interval '30 minutes'
                        ) AS completed_30m,
                        count(*) FILTER (
                            WHERE status = 'SUCCESS'
                              AND completed_at >= now() - interval '60 minutes'
                        ) AS completed_60m
                    FROM control.cn_publish_subtask
                    WHERE package_id = %s
                      AND (%s = '' OR checkpoint_version = %s)
                      AND task_group = %s
                    """,
                    (package_id, publish_version, publish_version, task_group),
                )
                group_metrics = dict(cur.fetchone() or {})

    now = datetime.now(timezone.utc)
    stage_version = str(checkpoint.get("stage_checkpoint_version") or "")
    last_progress_at = group_metrics.get("last_durable_progress_at") or ledger.get(
        "last_durable_progress_at"
    )
    current_task_elapsed = _elapsed_seconds((current_subtask or {}).get("started_at"), now=now)
    last_progress_age = _elapsed_seconds(last_progress_at, now=now)
    group_eta = _estimate_group_eta(
        task_total=int(group_metrics.get("task_total") or 0),
        success_tasks=int(group_metrics.get("success_tasks") or 0),
        completed_15m=int(group_metrics.get("completed_15m") or 0),
        completed_30m=int(group_metrics.get("completed_30m") or 0),
        completed_60m=int(group_metrics.get("completed_60m") or 0),
    )
    group_eta.update(
        {
            "task_group": str((current_subtask or {}).get("task_group") or ""),
            "success_tasks": int(group_metrics.get("success_tasks") or 0),
            "running_tasks": int(group_metrics.get("running_tasks") or 0),
            "failed_tasks": int(group_metrics.get("failed_tasks") or 0),
            "group_started_at": group_metrics.get("group_started_at"),
            "last_durable_progress_at": last_progress_at,
            "last_durable_progress_age_seconds": last_progress_age,
            "current_task_elapsed_seconds": current_task_elapsed,
            "health": _progress_health(
                running_tasks=int(group_metrics.get("running_tasks") or 0),
                failed_tasks=int(group_metrics.get("failed_tasks") or 0),
                last_progress_age_seconds=last_progress_age,
            ),
        }
    )
    dag_progress = _cn_dag_progress(str((current_subtask or {}).get("task_group") or ""))

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
            "last_durable_progress_at": ledger.get("last_durable_progress_at"),
            "current_subtask": current_subtask,
            "current_group": group_eta,
            "dag": dag_progress,
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
