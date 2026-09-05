from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _markup() -> str:
    return (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")


def test_us_bulk_console_prepares_before_any_approval() -> None:
    markup = _markup()
    assert "准备批量导入" in markup
    assert "只读 frozen plan" in markup
    assert "NEEDS_OPERATOR" in markup
    assert "此状态本身不写 hot_us" in markup
    assert "queueTask('US_APPLICATION','CONTINUE')" in markup
    assert "n=91" in markup


def test_us_bulk_console_approval_is_bound_to_exact_plan_sha() -> None:
    markup = _markup()
    assert "function approveUsBulk(runId,planSha)" in markup
    assert "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/${encodeURIComponent(runId)}/APPROVE" in markup
    assert "url.searchParams.set('plan_sha256',planSha)" in markup
    assert "Plan SHA：${planSha}" in markup
    assert "批准并开始" in markup
    assert "frozen master plan" in markup


def test_us_bulk_console_uses_durable_resume_and_boundary_stop() -> None:
    markup = _markup()
    assert "function resumeUsBulk(runId)" in markup
    assert "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/${encodeURIComponent(runId)}/RESUME" in markup
    assert "按原计划恢复" in markup
    assert "queueTask('US_APPLICATION','STOP')" in markup
    assert "durable 包边界" in markup
    assert "不会 blind retry" in markup


def test_us_bulk_console_polls_dedicated_bulk_status_and_durable_metrics() -> None:
    markup = _markup()
    assert "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/ACTIVE" in markup
    assert "loadUsBulkState" in markup
    assert "current_sequence" in markup
    assert "current_file" in markup
    assert "completed_suffix_count" in markup
    assert "remaining_to_accepted_corpus" in markup
    assert "last_safe_checkpoint_sequence" in markup
    assert "current_package_state" in markup
    assert "current_canary_state" in markup
    assert "inventory_sha256" in markup


def test_existing_task_center_controls_remain_present() -> None:
    markup = _markup()
    for domain in ("CN", "US_ASSIGNMENT", "US_TTAB"):
        assert f"queueTask('{domain}','CONTINUE')" in markup
        assert f"queueTask('{domain}','STOP')" in markup
        assert f"queueTask('{domain}','RUN')" in markup
        assert f"queueTask('{domain}','RETRY')" in markup
    assert "/api/admin/v2/system/domain-progress" in markup
    assert "/api/admin/v2/jobs" in markup
