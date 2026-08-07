from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.cn.migrations import ensure_m15_schema
from app.db import clickhouse_client, postgres_conn
from app.jobs import (
    ensure_raw_directories,
    ingest_pending_cn,
    scan_and_ingest_cn,
    scan_cn_incoming,
)
from app.repository import list_recent_packages, list_recent_runs


app = FastAPI(
    title="MarkOrbit Data Engine",
    version="0.3.0",
    description="MarkOrbit CN trademark data engine M1.5",
)


@app.on_event("startup")
def startup() -> None:
    ensure_raw_directories()
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
        "version": "M1.5",
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


@app.post("/api/jobs/cn/scan")
def trigger_cn_scan():
    return scan_cn_incoming(trigger_type="MANUAL")


@app.post("/api/jobs/cn/run")
def trigger_cn_cycle():
    return scan_and_ingest_cn(trigger_type="MANUAL")


@app.post("/api/jobs/cn/retry")
def retry_cn_failed():
    return ingest_pending_cn(
        trigger_type="MANUAL_RETRY",
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
    return {"tables": counts, "goods_status": scope[0] if scope else {}}


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
