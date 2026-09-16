from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.us_assignment.validate_corpus_replay_fixture as assignment_fixture
import app.us_ttab.validate_corpus_replay_fixture as ttab_fixture


TTAB_FIXTURE_ID = "11111111-1111-4111-8111-111111111111"
TTAB_FOREIGN_ID = "22222222-2222-4222-8222-222222222222"
ASSIGNMENT_FIXTURE_ID = "33333333-3333-4333-8333-333333333333"
ASSIGNMENT_FOREIGN_ID = "44444444-4444-4444-8444-444444444444"


class _ZeroResult:
    result_rows = [(0,)]


class _ZeroClickHouse:
    def query(self, _sql: str) -> _ZeroResult:
        return _ZeroResult()


def test_ttab_fixture_refuses_foreign_registry_before_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        ttab_fixture, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path)
    )
    monkeypatch.setattr(
        ttab_fixture,
        "list_ttab_packages",
        lambda: [{"file_name": "production.zip", "package_id": TTAB_FOREIGN_ID}],
    )
    monkeypatch.setattr(
        ttab_fixture,
        "ensure_ttab_schema",
        lambda: pytest.fail("schema/write path must not run against foreign registry"),
    )

    with pytest.raises(RuntimeError, match="non-isolated target"):
        ttab_fixture.main()


def test_assignment_fixture_refuses_foreign_registry_before_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        assignment_fixture, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path)
    )
    monkeypatch.setattr(
        assignment_fixture,
        "list_assignment_packages",
        lambda: [{"file_name": "production.zip", "package_id": ASSIGNMENT_FOREIGN_ID}],
    )
    monkeypatch.setattr(
        assignment_fixture,
        "ensure_assignment_schema",
        lambda: pytest.fail("schema/write path must not run against foreign registry"),
    )

    with pytest.raises(RuntimeError, match="non-isolated target"):
        assignment_fixture.main()


def test_ttab_finally_only_cleans_fixture_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls = {"registry": 0, "cleanup": [], "delete": []}

    def packages():
        calls["registry"] += 1
        if calls["registry"] == 1:
            return []
        return [
            {"file_name": ttab_fixture.FILES[0], "package_id": TTAB_FIXTURE_ID},
            {"file_name": "production.zip", "package_id": TTAB_FOREIGN_ID},
        ]

    monkeypatch.setattr(
        ttab_fixture, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path)
    )
    monkeypatch.setattr(ttab_fixture, "list_ttab_packages", packages)
    monkeypatch.setattr(ttab_fixture, "ensure_ttab_schema", lambda: None)
    monkeypatch.setattr(ttab_fixture, "_assert_fixture_facts_isolated", lambda: None)
    monkeypatch.setattr(
        ttab_fixture,
        "execute_replay",
        lambda *_args, **_kwargs: {"status": "BLOCKED", "remaining_count": 0},
    )
    monkeypatch.setattr(
        ttab_fixture,
        "cleanup_ttab_package_outputs",
        lambda package_id: calls["cleanup"].append(str(package_id)),
    )
    monkeypatch.setattr(ttab_fixture, "_delete_registry", lambda ids: calls["delete"].extend(ids))
    monkeypatch.setattr(ttab_fixture, "clickhouse_client", lambda: _ZeroClickHouse())

    with pytest.raises(RuntimeError, match="dry-run mismatch"):
        ttab_fixture.main()

    assert calls["cleanup"] == [TTAB_FIXTURE_ID]
    assert calls["delete"] == [TTAB_FIXTURE_ID]
    assert TTAB_FOREIGN_ID not in calls["cleanup"]
    assert TTAB_FOREIGN_ID not in calls["delete"]


def test_assignment_finally_only_cleans_fixture_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls = {"registry": 0, "cleanup": [], "delete": []}

    def packages():
        calls["registry"] += 1
        if calls["registry"] == 1:
            return []
        return [
            {"file_name": assignment_fixture.FILES[0], "package_id": ASSIGNMENT_FIXTURE_ID},
            {"file_name": "production.zip", "package_id": ASSIGNMENT_FOREIGN_ID},
        ]

    monkeypatch.setattr(
        assignment_fixture, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path)
    )
    monkeypatch.setattr(assignment_fixture, "list_assignment_packages", packages)
    monkeypatch.setattr(assignment_fixture, "ensure_assignment_schema", lambda: None)
    monkeypatch.setattr(assignment_fixture, "_assert_fixture_facts_isolated", lambda: None)
    monkeypatch.setattr(
        assignment_fixture,
        "execute_replay",
        lambda *_args, **_kwargs: {"status": "BLOCKED", "remaining_count": 0},
    )
    monkeypatch.setattr(
        assignment_fixture,
        "cleanup_assignment_package_outputs",
        lambda package_id: calls["cleanup"].append(str(package_id)),
    )
    monkeypatch.setattr(
        assignment_fixture, "_delete_registry", lambda ids: calls["delete"].extend(ids)
    )
    monkeypatch.setattr(assignment_fixture, "clickhouse_client", lambda: _ZeroClickHouse())

    with pytest.raises(RuntimeError, match="dry-run mismatch"):
        assignment_fixture.main()

    assert calls["cleanup"] == [ASSIGNMENT_FIXTURE_ID]
    assert calls["delete"] == [ASSIGNMENT_FIXTURE_ID]
    assert ASSIGNMENT_FOREIGN_ID not in calls["cleanup"]
    assert ASSIGNMENT_FOREIGN_ID not in calls["delete"]
