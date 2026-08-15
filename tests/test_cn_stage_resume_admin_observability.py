from pathlib import Path


def test_admin_api_exposes_lightweight_cn_stage_resume_candidates():
    source = Path("app/admin_paging_api.py").read_text(encoding="utf-8")

    assert "from app.cn.stage_resume import (" in source
    assert "CHECKPOINT_MAX_AGE" in source
    assert "CHECKPOINT_VERSION" in source
    assert "ensure_stage_checkpoint_schema" in source
    assert "@cache" in source
    assert "def _ensure_stage_checkpoint_schema_once()" in source
    assert source.count("_ensure_stage_checkpoint_schema_once()") >= 3
    assert '@router.get("/cn-recovery")' in source
    assert "cn_stage_resume_candidate" in source
    assert "control.cn_package_stage_checkpoint" in source
    assert "POSTGRES_CANDIDATE_ONLY_CLICKHOUSE_EXACT_COUNTS_ON_RETRY" in source
    assert "sp.status IN ('FAILED', 'INTERRUPTED')" in source
    assert "sp.status IN ('INTERRUPTED', 'FAILED', 'MISSING_FILE')" in source


def test_admin_pages_surface_checkpoint_as_candidate_not_guaranteed_resume():
    jobs = Path("web/admin-jobs.html").read_text(encoding="utf-8")
    packages = Path("web/admin-packages.html").read_text(encoding="utf-8")

    assert "/api/admin/v2/cn-recovery" in jobs
    assert "Stage 断点候选" in jobs
    assert "精确校验 7 张 Stage 表" in jobs
    assert "cn_stage_resume_candidate" in packages
    assert "Stage 断点候选" in packages
    assert "实际恢复前仍会校验完整 Stage 行数" in packages


def test_cn_recovery_route_is_registered():
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/api/admin/v2/cn-recovery" in routes
