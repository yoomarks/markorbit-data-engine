from __future__ import annotations

from pathlib import Path

from app.admin_progress import _cn_phase, _corpus_progress


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


def test_admin_progress_route_and_task_center_are_wired() -> None:
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/api/admin/v2/system/domain-progress" in routes

    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert "实时执行进度" in markup
    assert "/api/admin/v2/system/domain-progress" in markup
    assert "current_subtask" in markup
    assert "internal_files_completed" in markup
    assert "setInterval(loadProgress,3000)" in markup
    assert "不显示伪百分比" in markup


def test_admin_progress_reads_durable_cn_ledgers() -> None:
    source = (ROOT / "app" / "admin_progress.py").read_text(encoding="utf-8")
    assert "control.cn_package_stage_checkpoint" in source
    assert "control.cn_publish_checkpoint" in source
    assert "control.cn_publish_subtask" in source
    assert "control.source_package_file" in source
    assert "status = 'PROCESSING'" in source
    assert "read_only" in source
