from __future__ import annotations

from pathlib import Path

from app.contact_ingest import directory_api


ROOT = Path(__file__).resolve().parents[1]


class _FakeCursor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executions: list[tuple[str, list[object]]] = []
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, list(params or [])))
        self.current = self.responses.pop(0)

    def fetchone(self):
        return self.current

    def fetchall(self):
        return self.current


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


def test_directory_analytics_returns_country_agent_direct_and_channel_counts(monkeypatch) -> None:
    cursor = _FakeCursor([
        {
            "entities": 12,
            "agents": 7,
            "direct_clients": 6,
            "both": 1,
            "unknown": 0,
            "people": 15,
            "phones": 9,
            "emails": 10,
            "websites": 8,
            "whatsapps": 2,
            "countries": 2,
        },
        [
            {
                "country_code": "US",
                "entities": 8,
                "agents": 5,
                "direct_clients": 4,
                "both": 1,
                "unknown": 0,
                "people": 10,
                "phones": 7,
                "emails": 7,
                "websites": 5,
                "whatsapps": 1,
            },
            {
                "country_code": "CN",
                "entities": 4,
                "agents": 2,
                "direct_clients": 2,
                "both": 0,
                "unknown": 0,
                "people": 5,
                "phones": 2,
                "emails": 3,
                "websites": 3,
                "whatsapps": 1,
            },
        ],
    ])
    monkeypatch.setattr(directory_api, "postgres_conn", lambda: _FakeConn(cursor))

    result = directory_api.contact_directory_analytics()

    assert result["totals"]["entities"] == 12
    assert result["totals"]["agents"] == 7
    assert result["totals"]["direct_clients"] == 6
    assert result["totals"]["phones"] == 9
    assert result["totals"]["emails"] == 10
    assert result["totals"]["websites"] == 8
    assert result["totals"]["countries"] == 2
    assert result["countries"][0]["country_code"] == "US"
    assert "AGENT_CONTACT_LIST" in result["classification"]["agent"]
    assert "QCC_COMPANY_EXPORT" in result["classification"]["direct_client"]


def test_directory_list_supports_country_segment_channel_search_and_paging(monkeypatch) -> None:
    cursor = _FakeCursor([[
        {
            "entity_id": "e1",
            "entity_name": "Example IP LLC",
            "entity_type": "ORGANIZATION",
            "country_code": "US",
            "region_code": "CA",
            "city": "Los Angeles",
            "segment": "AGENT",
            "is_agent": True,
            "is_direct": False,
            "person_count": 1,
            "phone_count": 1,
            "email_count": 1,
            "website_count": 1,
            "whatsapp_count": 0,
            "people": ["Jane Example"],
            "phones": ["+1 202 555 0100"],
            "emails": ["jane@example.test"],
            "websites": ["https://example.test"],
            "whatsapps": [],
            "source_profiles": ["AGENT_CONTACT_LIST"],
            "applicant_mentions": 0,
            "agent_mentions": 5,
            "filtered_total": 23,
        }
    ]])
    monkeypatch.setattr(directory_api, "postgres_conn", lambda: _FakeConn(cursor))

    result = directory_api.contact_directory_list(
        country="US",
        segment="AGENT",
        channel="EMAIL",
        query="Jane",
        limit=50,
        offset=10,
    )

    sql, params = cursor.executions[0]
    assert "country_code = %s" in sql
    assert "is_agent" in sql
    assert "email_count > 0" in sql
    assert "entity_name ILIKE %s" in sql
    assert params == ["US", "%Jane%", "%Jane%", "%Jane%", "%Jane%", "%Jane%", "%Jane%", 50, 10]
    assert result["total"] == 23
    assert result["rows"][0]["segment"] == "AGENT"
    assert result["rows"][0]["people"] == ["Jane Example"]


def test_directory_classification_stays_evidence_based() -> None:
    source = (ROOT / "app" / "contact_ingest" / "directory_api.py").read_text(encoding="utf-8")
    assert "OWNER', 'CO_OWNER', 'APPLICANT" in source
    assert "AGENT', 'ATTORNEY', 'CORRESPONDENT" in source
    assert "source_profile = 'AGENT_CONTACT_LIST'" in source
    assert "source_profile = 'QCC_COMPANY_EXPORT'" in source
    assert "NOT is_agent AND NOT is_direct" in source


def test_contacts_page_exposes_country_summary_and_filterable_directory() -> None:
    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/api/admin/contacts/directory/analytics" in routes
    assert "/api/admin/contacts/directory" in routes

    markup = (ROOT / "web" / "contacts.html").read_text(encoding="utf-8")
    assert "联系人数据总览" in markup
    assert "各国联系人分布" in markup
    assert "联系人明细" in markup
    assert "代理人 / 机构" in markup
    assert "直客主体" in markup
    assert "有电话" in markup
    assert "有邮箱" in markup
    assert "有网站" in markup
    assert "directory-country" in markup
    assert "directory-segment" in markup
    assert "directory-channel" in markup
    assert "/api/admin/contacts/directory/analytics" in markup
    assert "/api/admin/contacts/directory?" in markup
