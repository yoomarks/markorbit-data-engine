from __future__ import annotations

from pathlib import Path

from app.admin_progress import (
    _cn_dag_progress,
    _cn_phase,
    _corpus_progress,
    _estimate_group_eta,
    _progress_health,
)


ROOT = Path(__file__).resolve().parents[1]


def test_cn_progress_uses_registered_corpus_denominator_only() -> None:
    progress = _corpus_progress(
        "CN",
        {"SUCCESS": 8, "PROCESSING": 1, "REGISTERED": 1},
    )
    assert progress["registered_total"] == 10
    assert progress["success"] == 8
    assert progress["progress_pct"] == 80.0
    assert progress["progress_basis"] == "REGISTERED_CN_CORPUS"


def test_us_progress_refuses_misleading_registered_percentage() -> None:
    progress = _corpus_progress(
        "US_APPLICATION",
        {"SUCCESS": 8, "PROCESSING": 1, "REGISTERED": 1},
    )
    assert progress["registered_total"] == 10
    assert progress["progress_pct"] is None
    assert progress["progress_basis"] == "ACTIVITY_ONLY"


def test_cn_phase_moves_from_raw_to_stage_to_final_publish() -> None:
    package = {"status": "PROCESSING"}
    assert _cn_phase(package=package)[0] == "RAW_PARSE_STAGE"
    assert (
        _cn_phase(package=package, stage_checkpoint_version="CN_STAGE_CHECKPOINT_V1")[0]
        == "POST_STAGE"
    )
    phase, label = _cn_phase(
        package=package,
        stage_checkpoint_version="CN_STAGE_CHECKPOINT_V1",
        publish_checkpoint_version="CN_FINAL_PUBLISH_V1",
        current_subtask={"task_group": "CASE_PARTY_CURRENT"},
    )
    assert phase == "FINAL_PUBLISH"
    assert "CASE_PARTY_CURRENT" in label


def test_cn_dag_progress_reports_remaining_semantic_nodes() -> None:
    progress = _cn_dag_progress("CASE_PARTY_CURRENT")
    assert progress["dag_version"] == "CN_FINAL_PUBLISH_DAG_V1"
    assert progress["current_node_index"] is not None
    assert progress["remaining_node_count"] == len(progress["remaining_nodes"])
    assert progress["remaining_nodes"][:3] == [
        "AGENT_CURRENT",
        "PRIORITY_CURRENT",
        "MADRID_CURRENT",
    ]
    assert "SCOPE_CARVE_OUT_CURRENT" in progress["remaining_nodes"]


def test_eta_uses_durable_completion_window_and_refuses_sparse_evidence() -> None:
    estimated = _estimate_group_eta(
        task_total=100,
        success_tasks=40,
        completed_15m=8,
        completed_30m=14,
        completed_60m=20,
    )
    assert estimated["remaining_tasks"] == 60
    assert estimated["tasks_per_hour"]["60m"] == 20.0
    assert estimated["eta_seconds"] == 10800.0
    assert estimated["eta_basis"] == "CURRENT_GROUP_60M_DURABLE_COMPLETIONS"

    sparse = _estimate_group_eta(
        task_total=100,
        success_tasks=40,
        completed_15m=1,
        completed_30m=1,
        completed_60m=1,
    )
    assert sparse["eta_seconds"] is None
    assert sparse["eta_basis"] == "INSUFFICIENT_DURABLE_COMPLETIONS"


def test_progress_health_distinguishes_quiet_work_from_failure() -> None:
    assert (
        _progress_health(
            running_tasks=1,
            failed_tasks=0,
            last_progress_age_seconds=10 * 60,
        )
        == "ACTIVE"
    )
    assert (
        _progress_health(
            running_tasks=1,
            failed_tasks=0,
            last_progress_age_seconds=35 * 60,
        )
        == "QUIET_LONG_TASK"
    )
    assert (
        _progress_health(
            running_tasks=1,
            failed_tasks=0,
            last_progress_age_seconds=75 * 60,
        )
        == "NO_RECENT_DURABLE_PROGRESS"
    )
    assert (
        _progress_health(
            running_tasks=1,
            failed_tasks=1,
            last_progress_age_seconds=1,
        )
        == "FAILED_SUBTASK_PRESENT"
    )


def test_admin_progress_route_and_task_center_are_wired() -> None:
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/api/admin/v2/system/domain-progress" in routes

    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert "实时执行进度" in markup
    assert "/api/admin/v2/system/domain-progress" in markup
    assert "current_subtask" in markup
    assert "internal_files_completed" in markup
    assert "current_group" in markup
    assert "last_durable_progress_at" in markup
    assert "eta_basis" in markup
    assert "remaining_nodes" in markup
    assert "setInterval(loadProgress,3000)" in markup
    assert "不显示伪百分比" in markup


def test_admin_progress_reads_durable_cn_ledgers() -> None:
    source = (ROOT / "app" / "admin_progress.py").read_text(encoding="utf-8")
    assert 'ADMIN_PROGRESS_VERSION = "MARKORBIT_ADMIN_PROGRESS_V2"' in source
    assert "control.cn_package_stage_checkpoint" in source
    assert "control.cn_publish_checkpoint" in source
    assert "control.cn_publish_subtask" in source
    assert "control.source_package_file" in source
    assert "completed_at >= now() - interval '15 minutes'" in source
    assert "completed_at >= now() - interval '60 minutes'" in source
    assert "CN_FINAL_PUBLISH_DAG.topological_order()" in source
    assert "status = 'PROCESSING'" in source
    assert "read_only" in source
