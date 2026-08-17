from __future__ import annotations

from app.contact_ingest import directory_cached as cached


def _reset(monkeypatch, generation: str = "g1") -> None:
    monkeypatch.setattr(cached, "_contact_generation", lambda: generation)
    monkeypatch.setattr(
        cached.analytics_source,
        "invalidate_contact_directory_cache",
        lambda: None,
    )
    with cached._cache_lock:
        cached._cache.clear()


def test_overview_cache_hits_until_force_refresh(monkeypatch) -> None:
    _reset(monkeypatch)
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        return {"totals": {"entities": calls["count"]}, "countries": []}

    monkeypatch.setattr(cached.analytics_source, "contact_directory_analytics", loader)

    first = cached.cached_contact_directory_analytics()
    second = cached.cached_contact_directory_analytics()
    refreshed = cached.cached_contact_directory_analytics(force_refresh=True)

    assert first["_cache"]["hit"] is False
    assert second["_cache"]["hit"] is True
    assert refreshed["_cache"]["hit"] is False
    assert calls["count"] == 2
    assert second["totals"]["entities"] == 1
    assert refreshed["totals"]["entities"] == 2


def test_import_generation_invalidates_cached_view(monkeypatch) -> None:
    generations = iter(["g1", "g1", "g2"])
    monkeypatch.setattr(cached, "_contact_generation", lambda: next(generations))
    monkeypatch.setattr(
        cached.analytics_source,
        "invalidate_contact_directory_cache",
        lambda: None,
    )
    with cached._cache_lock:
        cached._cache.clear()

    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        return {"countries": [{"country_code": "CN", "entities": calls["count"]}]}

    monkeypatch.setattr(cached.runtime_source, "contact_directory_countries", loader)

    first = cached.cached_contact_directory_countries()
    second = cached.cached_contact_directory_countries()
    changed = cached.cached_contact_directory_countries()

    assert first["_cache"]["generation"] == "g1"
    assert second["_cache"]["hit"] is True
    assert changed["_cache"]["generation"] == "g2"
    assert changed["_cache"]["hit"] is False
    assert calls["count"] == 2


def test_directory_cache_key_includes_filters_and_page(monkeypatch) -> None:
    _reset(monkeypatch)
    calls: list[tuple] = []

    def loader(**kwargs):
        calls.append(tuple(sorted(kwargs.items())))
        return {"total": 1, "rows": [{"entity_name": kwargs["query"] or "all"}]}

    monkeypatch.setattr(cached.runtime_source, "contact_directory_list", loader)

    a1 = cached.cached_contact_directory_list(country="cn", query="Alpha", limit=50, offset=0)
    a2 = cached.cached_contact_directory_list(country="CN", query="Alpha", limit=50, offset=0)
    b = cached.cached_contact_directory_list(country="CN", query="Beta", limit=50, offset=0)
    page2 = cached.cached_contact_directory_list(country="CN", query="Alpha", limit=50, offset=50)

    assert a1["_cache"]["hit"] is False
    assert a2["_cache"]["hit"] is True
    assert b["_cache"]["hit"] is False
    assert page2["_cache"]["hit"] is False
    assert len(calls) == 3


def test_manual_invalidation_clears_local_cache(monkeypatch) -> None:
    _reset(monkeypatch)
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        return {"countries": []}

    monkeypatch.setattr(cached.runtime_source, "contact_directory_countries", loader)

    cached.cached_contact_directory_countries()
    cached.cached_contact_directory_countries()
    cached.invalidate_contact_view_cache()
    after = cached.cached_contact_directory_countries()

    assert after["_cache"]["hit"] is False
    assert calls["count"] == 2


def test_contact_admin_pages_expose_cache_refresh_controls() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    overview = (root / "web" / "admin-contacts-overview.html").read_text(encoding="utf-8")
    directory = (root / "web" / "admin-contacts-directory.html").read_text(encoding="utf-8")

    assert "refresh=true" in overview
    assert "重新计算" in overview
    assert "缓存命中" in overview
    assert "refresh-data" in directory
    assert "url.searchParams.set('refresh','true')" in directory
    assert "缓存命中" in directory


def test_contact_directory_read_indexes_are_additive() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "contact_ingest" / "directory_indexes.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE INDEX IF NOT EXISTS ix_contact_relation_person_recent" in source
    assert "CREATE INDEX IF NOT EXISTS ix_contact_raw_record_entity_profile" in source
    assert "CREATE INDEX IF NOT EXISTS ix_contact_import_run_success_finished" in source
