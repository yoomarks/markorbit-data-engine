from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.cn.guarded_run_once import build_execution_guard
from app.cn.migrations import ensure_m15_schema
from app.db import clickhouse_client, postgres_conn
from app.jobs import (
    ensure_raw_directories,
    ingest_pending_cn,
    scan_and_ingest_cn,
    scan_cn_incoming,
)
from app.repository import list_recent_packages, list_recent_runs
from app.version import engine_version


ENGINE_VERSION = engine_version()

app = FastAPI(
    title="MarkOrbit Data Engine",
    version="0.4.0",
    description=f"MarkOrbit CN trademark data engine {ENGINE_VERSION}",
)


@app.on_event("startup")
def startup() -> None:
    ensure_raw_directories()
    # M1.6 builds on the frozen M1.5 CN core schema. The M1.6 durable-goods
    # schema is initialized by ClickHouse bootstrap and guarded again by the
    # M1.6 ingestion wrapper before any CN package is accepted.
    ensure_m15_schema()


@app.get("/", include_in_schema=False)
def dashboard():
    index = Path("/app/web/index.html")
    if not index.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(index)


@app.get("/api/health")
def health():
    result = {
        "api": "ok",
        "version": ENGINE_VERSION,
        "postgres": "unknown",
        "clickhouse": "unknown",
    }
    try:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                result["postgres"] = "ok" if cur.fetchone()["ok"] == 1 else "error"
    except Exception as exc:
        result["postgres"] = f"error: {exc}"

    try:
        client = clickhouse_client()
        result["clickhouse"] = "ok" if client.command("SELECT 1") == 1 else "error"
    except Exception as exc:
        result["clickhouse"] = f"error: {exc}"
    return result


def _cn_api_execution_guard(action: str) -> dict[str, Any]:
    """Require guarded registered-continuation mode for mutating CN API actions.

    The API is intentionally not allowed to bootstrap a clean replay. The first
    clean run must use `scripts/run-cn.ps1`, whose wrapper also verifies that the
    persistent worker is stopped before preflight/plan/registration begin.
    """
    try:
        guard = build_execution_guard()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CN_EXECUTION_GUARD_UNAVAILABLE",
                "action": action,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc

    if not guard.get("allowed"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CN_EXECUTION_GUARD_BLOCKED",
                "action": action,
                "guard": guard,
            },
        )

    if guard.get("mode") == "CLEAN_RESET_FIRST_RUN":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CN_CLEAN_REPLAY_MANUAL_BOOTSTRAP_REQUIRED",
                "action": action,
                "instruction": (
                    "Stop the persistent worker and run scripts/run-cn.ps1 for the "
                    "first clean M1.6 replay cycle."
                ),
                "guard": guard,
            },
        )

    if guard.get("mode") != "REGISTERED_REPLAY_CONTINUATION":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CN_EXECUTION_MODE_NOT_API_SAFE",
                "action": action,
                "guard": guard,
            },
        )
    return guard


@app.post("/api/jobs/cn/scan")
def trigger_cn_scan():
    _cn_api_execution_guard("scan")
    return scan_cn_incoming(trigger_type="MANUAL_API_GUARDED_SCAN")


@app.post("/api/jobs/cn/run")
def trigger_cn_cycle():
    _cn_api_execution_guard("run")
    return scan_and_ingest_cn(trigger_type="MANUAL_API_GUARDED")


@app.post("/api/jobs/cn/retry")
def retry_cn_failed():
    # Retry is the explicit repair path for registered FAILED/MISSING_FILE
    # packages. M1.6 schema/replay guards are enforced again inside ingest_m16.
    return ingest_pending_cn(
        trigger_type="MANUAL_API_RETRY",
        include_failed=True,
        limit=1,
    )


@app.get("/api/job-runs")
def job_runs(limit: int = 50):
    return list_recent_runs(max(1, min(limit, 200)))


@app.get("/api/source-packages")
def source_packages(limit: int = 50):
    return list_recent_packages(max(1, min(limit, 200)))


def _query_dicts(sql: str) -> list[dict]:
    result = clickhouse_client().query(sql)
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


@app.get("/api/cn/schema")
def cn_schema():
    return _query_dicts(
        """
        SELECT table, position, name, type, comment
        FROM system.columns
        WHERE database = 'markorbit_facts'
          AND table LIKE 'cn_%'
        ORDER BY table, position
        """
    )


@app.get("/api/cn/summary")
def cn_summary():
    counts = _query_dicts(
        """
        SELECT table_name, row_count
        FROM
        (
            SELECT 'cn_case_current' AS table_name, count() AS row_count
            FROM markorbit_facts.cn_case_current FINAL WHERE is_deleted = 0
            UNION ALL
            SELECT 'cn_case_scope_current', count()
            FROM markorbit_facts.cn_case_scope_current FINAL WHERE is_deleted = 0
            UNION ALL
            SELECT 'cn_case_party_current', count()
            FROM markorbit_facts.cn_case_party_current FINAL
            WHERE is_deleted = 0 AND is_current = 1
            UNION ALL
            SELECT 'cn_observed_event', count()
            FROM markorbit_facts.cn_observed_event FINAL
            UNION ALL
            SELECT 'cn_case_relation_current', count()
            FROM markorbit_facts.cn_case_relation_current FINAL WHERE is_deleted = 0
            UNION ALL
            SELECT 'cn_scope_carve_out_current', count()
            FROM markorbit_facts.cn_scope_carve_out_current FINAL WHERE is_deleted = 0
            UNION ALL
            SELECT 'cn_goods_item_current', count()
            FROM markorbit_facts.cn_goods_item_current FINAL WHERE is_deleted = 0
            UNION ALL
            SELECT 'cn_goods_item_observation', count()
            FROM markorbit_facts.cn_goods_item_observation FINAL
            UNION ALL
            SELECT 'cn_goods_scope_lifecycle_current', count()
            FROM markorbit_facts.cn_goods_scope_lifecycle_current FINAL WHERE is_deleted = 0
        )
        ORDER BY table_name
        """
    )
    scope = _query_dicts(
        """
        SELECT
            sum(source_item_count) AS source_goods_items,
            sum(interpreted_active_item_count) AS interpreted_active_items,
            sum(interpreted_inactive_item_count) AS interpreted_inactive_items,
            sum(unmapped_status_item_count) AS unmapped_status_items,
            countIf(interpretation_complete = 1) AS fully_interpreted_scopes,
            countIf(interpretation_complete = 0) AS incomplete_scopes
        FROM markorbit_facts.cn_case_scope_current FINAL
        WHERE is_deleted = 0
        """
    )
    lifecycle = _query_dicts(
        """
        SELECT
            sum(known_item_count) AS durable_known_items,
            sum(operational_effective_item_count) AS operational_effective_items,
            sum(risk_item_count) AS risk_items,
            sum(inactive_high_confidence_item_count) AS inactive_high_confidence_items,
            sum(final_inactive_item_count) AS final_inactive_items,
            sum(unknown_item_count) AS unknown_items,
            countIf(all_known_goods_inactive = 1) AS all_known_goods_inactive_scopes,
            countIf(all_known_goods_final_inactive = 1) AS all_known_goods_final_inactive_scopes
        FROM markorbit_facts.cn_goods_scope_lifecycle_current FINAL
        WHERE is_deleted = 0
        """
    )
    return {
        "version": ENGINE_VERSION,
        "tables": counts,
        "goods_status": scope[0] if scope else {},
        "goods_lifecycle": lifecycle[0] if lifecycle else {},
    }


@app.get("/api/cn/cases/{application_number}")
def cn_case(application_number: str):
    safe = application_number.strip().upper().replace("'", "''")
    case_rows = _query_dicts(
        f"""
        SELECT *
        FROM markorbit_facts.cn_case_current FINAL
        WHERE application_number = '{safe}' AND is_deleted = 0
        LIMIT 1
        """
    )
    if not case_rows:
        raise HTTPException(status_code=404, detail="CN trademark case not found")
    return {
        "case": case_rows[0],
        "scopes": _query_dicts(
            f"""
            SELECT *
            FROM markorbit_facts.cn_case_scope_current FINAL
            WHERE application_number = '{safe}' AND is_deleted = 0
            ORDER BY class_no
            """
        ),
        "goods_items": _query_dicts(
            f"""
            SELECT *
            FROM markorbit_facts.cn_goods_item_current FINAL
            WHERE application_number = '{safe}' AND is_deleted = 0
            ORDER BY class_no, goods_sequence, goods_item_key
            """
        ),
        "goods_lifecycle": _query_dicts(
            f"""
            SELECT *
            FROM markorbit_facts.cn_goods_scope_lifecycle_current FINAL
            WHERE application_number = '{safe}' AND is_deleted = 0
            ORDER BY class_no
            """
        ),
        "parties": _query_dicts(
            f"""
            SELECT *
            FROM markorbit_facts.cn_case_party_current FINAL
            WHERE application_number = '{safe}'
              AND is_deleted = 0 AND is_current = 1
            ORDER BY role, raw_name
            """
        ),
        "events": _query_dicts(
            f"""
            SELECT *
            FROM markorbit_facts.cn_observed_event FINAL
            WHERE application_number = '{safe}'
            ORDER BY event_date, observed_at, event_type
            """
        ),
        "relations": _query_dicts(
            f"""
            SELECT *
            FROM markorbit_facts.cn_case_relation_current FINAL
            WHERE (source_application_number = '{safe}'
                OR target_application_number = '{safe}')
              AND is_deleted = 0
            ORDER BY source_application_number, target_application_number
            """
        ),
    }
