from __future__ import annotations

from pathlib import Path

from app.contact_ingest import directory_runtime


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


def test_runtime_directory_pages_before_trademark_evidence(monkeypatch) -> None:
    cursor = _FakeCursor([
        [
            {
                "entity_id": "11111111-1111-1111-1111-111111111111",
                "entity_name": "Example IP LLC",
                "entity_type": "AGENT_FIRM",
                "country_code": "US",
                "region_code": "CA",
                "city": "Los Angeles",
                "is_agent": True,
                "is_direct": False,
            }
        ],
        [
            {
                "entity_id": "11111111-1111-1111-1111-111111111111",
                "people": ["Jane Example"],
                "phones": ["+1 202 555 0100"],
                "emails": ["jane@example.test"],
                "websites": ["https://example.test"],
                "whatsapps": [],
                "source_profiles": ["AGENT_CONTACT_LIST"],
                "source_segments": ["AGENT"],
                "source_scopes": ["US agents"],
            }
        ],
        [
            {
                "entity_id": "11111111-1111-1111-1111-111111111111",
                "applicant_mentions": 0,
                "agent_mentions": 5,
                "single_mention_country": "US",
            }
        ],
    ])
    monkeypatch.setattr(directory_runtime, "postgres_conn", lambda: _FakeConn(cursor))

    result = directory_runtime.contact_directory_list(
        country="US",
        segment="AGENT",
        channel="EMAIL",
        query="Jane",
        limit=50,
        offset=10,
    )

    first_sql, first_params = cursor.executions[0]
    assert "FROM contact_base" in first_sql
    assert "entity.entity_mention" not in first_sql
    assert "count(*) OVER()" not in first_sql
    assert "fc.channel_type IN ('EMAIL')" in first_sql
    assert first_params == ["US", "%Jane%", "%Jane%", "%Jane%", "%Jane%", 51, 10]

    assert "array_agg" in cursor.executions[1][0]
    assert "entity.entity_mention" in cursor.executions[2][0]
    assert "JOIN requested" in cursor.executions[2][0]
    assert result["total"] == 11
    assert result["has_more"] is False
    assert result["total_is_exact"] is True
    assert result["rows"][0]["segment"] == "AGENT"
    assert result["rows"][0]["agent_mentions"] == 5
    assert result["rows"][0]["people"] == ["Jane Example"]


def test_runtime_directory_lookahead_hydrates_only_returned_page(monkeypatch) -> None:
    rows = [
        {
            "entity_id": entity_id,
            "entity_name": name,
            "entity_type": "ORGANIZATION",
            "country_code": "US",
            "country_source": "SOURCE_ENTITY",
            "inferred_country_confidence": 0,
            "region_code": "",
            "city": "",
            "is_agent": False,
            "is_direct": True,
        }
        for entity_id, name in (
            ("11111111-1111-1111-1111-111111111111", "Alpha"),
            ("22222222-2222-2222-2222-222222222222", "Beta"),
        )
    ]
    cursor = _FakeCursor([rows, [], []])
    monkeypatch.setattr(directory_runtime, "postgres_conn", lambda: _FakeConn(cursor))

    result = directory_runtime.contact_directory_list(limit=1, offset=0)

    assert cursor.executions[0][1] == [2, 0]
    assert cursor.executions[1][1] == [[rows[0]["entity_id"]]]
    assert cursor.executions[2][1] == [[rows[0]["entity_id"]]]
    assert [row["entity_id"] for row in result["rows"]] == [rows[0]["entity_id"]]
    assert result["total"] == 2
    assert result["has_more"] is True
    assert result["total_is_exact"] is False
    assert result["total_semantics"] == "LOWER_BOUND"


def test_runtime_country_selector_avoids_trademark_rollup(monkeypatch) -> None:
    cursor = _FakeCursor([[
        {"country_code": "US", "entities": 8},
        {"country_code": "CN", "entities": 4},
        {"country_code": "", "entities": 2},
    ]])
    monkeypatch.setattr(directory_runtime, "postgres_conn", lambda: _FakeConn(cursor))

    result = directory_runtime.contact_directory_countries()

    sql, params = cursor.executions[0]
    assert params == []
    assert "entity.entity_mention" not in sql
    assert result["countries"][0] == {"country_code": "US", "entities": 8}


def test_admin_directory_page_does_not_wait_for_full_analytics() -> None:
    markup = (ROOT / "web" / "admin-contacts-directory.html").read_text(encoding="utf-8")
    assert "/api/admin/contacts/directory/countries" in markup
    assert "load();loadCountries();" in markup
    assert "loadCountries().then(load)" not in markup

    import app.main as main

    routes = {route.path for route in main.app.routes}
    assert "/api/admin/contacts/directory/countries" in routes
    assert "/api/admin/contacts/directory" in routes
