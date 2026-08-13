from pathlib import Path

from app.admin_paging_api import _normalize_page, _page_result


ROOT = Path(__file__).resolve().parents[1]


def test_admin_v1_routes_split_monolithic_control_center() -> None:
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert {
        "/admin",
        "/admin/raw",
        "/admin/packages",
        "/admin/jobs",
        "/admin/domains/{domain}",
        "/admin/contacts",
        "/admin/contacts/directory",
        "/admin/contacts/imports",
        "/admin/search",
        "/admin/system",
        "/admin/assets/{asset_name}",
        "/api/admin/v2/raw",
        "/api/admin/v2/packages",
        "/api/admin/v2/jobs",
        "/api/admin/v2/contact-tasks",
        "/api/admin/v2/system/components",
    } <= routes


def test_admin_v1_blue_white_design_tokens_and_shared_shell() -> None:
    css = (ROOT / "web" / "admin.css").read_text(encoding="utf-8")
    js = (ROOT / "web" / "admin.js").read_text(encoding="utf-8")
    assert "--mo-blue:#0b63f6" in css
    assert "--mo-cyan:#1cc8ff" in css
    assert "--mo-card:#ffffff" in css
    assert "background:var(--mo-bg)" in css
    assert "Data Engine Admin" in js
    assert "/admin/contacts/directory" in js
    assert "/admin/domains/us-assignment" in js


def test_admin_v1_pages_share_shell_and_keep_lists_separate() -> None:
    pages = {
        "index.html": "总览 Dashboard",
        "admin-raw.html": "/api/admin/v2/raw",
        "admin-packages.html": "/api/admin/v2/packages",
        "admin-jobs.html": "/api/admin/v2/jobs",
        "admin-contacts-overview.html": "各国联系人分布",
        "admin-contacts-directory.html": "联系人库",
        "admin-contacts-imports.html": "/api/admin/v2/contact-tasks",
        "admin-search.html": "/api/cn/cases/",
        "admin-system.html": "/api/admin/v2/system/components",
    }
    for filename, marker in pages.items():
        markup = (ROOT / "web" / filename).read_text(encoding="utf-8")
        assert "/admin/assets/admin.css" in markup
        assert "/admin/assets/admin.js" in markup
        assert marker in markup


def test_admin_v1_pagination_contract_is_uniform() -> None:
    assert _normalize_page(0, 500) == (1, 200, 0)
    assert _normalize_page(3, 50) == (3, 50, 100)
    result = _page_result([{"id": 1}], page=3, page_size=50, total=121)
    assert result == {
        "items": [{"id": 1}],
        "page": 3,
        "page_size": 50,
        "total": 121,
        "pages": 3,
    }


def test_admin_jobs_query_avoids_literal_percent_placeholder_conflicts() -> None:
    source = (ROOT / "app" / "admin_paging_api.py").read_text(encoding="utf-8")
    job_sql = source[source.index("_JOB_DOMAIN_SQL") : source.index('@router.get("/jobs")')]
    assert "position('ASSIGNMENT'" in job_sql
    assert "position('TTAB'" in job_sql
    assert "LIKE '%ASSIGNMENT%'" not in job_sql
