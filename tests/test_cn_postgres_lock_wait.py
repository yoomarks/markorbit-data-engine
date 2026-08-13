from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app import db, jobs


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_postgres_lock_timeout_override_is_scoped(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_connect(*args, **kwargs):
        calls.append(dict(kwargs))
        return _FakeConnection()

    monkeypatch.setattr(db, "get_settings", lambda: SimpleNamespace(postgres_dsn="postgresql://test"))
    monkeypatch.setattr(db.psycopg, "connect", fake_connect)

    with db.postgres_conn():
        pass
    with db.postgres_execution_settings(lock_timeout="0"):
        with db.postgres_conn():
            pass
    with db.postgres_conn():
        pass

    assert "lock_timeout=15s" in str(calls[0]["options"])
    assert "lock_timeout=0" in str(calls[1]["options"])
    assert "lock_timeout=15s" in str(calls[2]["options"])
    assert all("idle_in_transaction_session_timeout=60s" in str(call["options"]) for call in calls)


def test_cn_ingestion_applies_no_lock_timeout_only_around_package(monkeypatch, tmp_path: Path) -> None:
    postgres_options: list[str] = []

    def fake_connect(*args, **kwargs):
        postgres_options.append(str(kwargs["options"]))
        return _FakeConnection()

    @contextmanager
    def fake_cn_guard():
        yield True

    package_path = tmp_path / "2022_3.zip"
    package = {
        "package_id": "bb075784-a983-479f-9646-5584919c3f17",
        "file_name": "2022_3.zip",
        "file_path": str(package_path),
        "sha256": "deadbeef",
        "status": "FAILED",
    }

    monkeypatch.setattr(db, "get_settings", lambda: SimpleNamespace(postgres_dsn="postgresql://test"))
    monkeypatch.setattr(db.psycopg, "connect", fake_connect)
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path))
    monkeypatch.setattr(jobs, "cn_ingestion_guard", fake_cn_guard)
    monkeypatch.setattr(jobs, "recover_interrupted_cn_ingestions", lambda: [])
    monkeypatch.setattr(jobs, "pending_packages", lambda *args, **kwargs: [package])
    monkeypatch.setattr(jobs, "_resolve_package_path", lambda *args, **kwargs: package_path)

    def fake_ingest(*args, **kwargs):
        # The real package path opens many short-lived repository connections.
        # Verify those connections inherit the CN-specific wait policy.
        with db.postgres_conn():
            pass
        return {"publish": {"ok": True}}

    monkeypatch.setattr(jobs, "ingest_cn_package", fake_ingest)

    result = jobs.ingest_pending_cn(trigger_type="ADMIN_UI_RETRY", include_failed=True, limit=1)

    assert result["success"] == 1
    assert result["failed"] == 0
    assert postgres_options == [
        "-c lock_timeout=0 -c idle_in_transaction_session_timeout=60s"
    ]

    # The override must not leak to later API/contact/Postgres work.
    with db.postgres_conn():
        pass
    assert "lock_timeout=15s" in postgres_options[-1]
