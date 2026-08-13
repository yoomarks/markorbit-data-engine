from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app import db, jobs


def _settings(raw_data_root: Path | None = None):
    return SimpleNamespace(
        postgres_dsn="postgresql://unused",
        clickhouse_host="clickhouse",
        clickhouse_http_port=8123,
        clickhouse_db="markorbit_facts",
        clickhouse_user="markorbit",
        clickhouse_password="secret",
        clickhouse_max_threads=4,
        clickhouse_external_group_by_bytes=536_870_912,
        clickhouse_external_sort_bytes=536_870_912,
        clickhouse_join_algorithm="",
        clickhouse_grace_hash_join_initial_buckets=32,
        clickhouse_send_receive_timeout=300,
        raw_data_root=raw_data_root or Path("/tmp/raw"),
    )


def test_clickhouse_execution_settings_are_scoped(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(db, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(
        db.clickhouse_connect,
        "get_client",
        lambda **kwargs: calls.append(kwargs) or object(),
    )

    with db.clickhouse_execution_settings(
        join_algorithm="grace_hash",
        grace_hash_join_initial_buckets=32,
        send_receive_timeout=3600,
    ):
        db.clickhouse_client()
    db.clickhouse_client()

    assert calls[0]["settings"]["join_algorithm"] == "grace_hash"
    assert calls[0]["settings"]["grace_hash_join_initial_buckets"] == 32
    assert calls[0]["send_receive_timeout"] == 3600
    assert "join_algorithm" not in calls[1]["settings"]
    assert calls[1]["send_receive_timeout"] == 300


def test_cn_retry_uses_proven_spill_safe_profile(monkeypatch, tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    package_path = raw_root / "incoming" / "cn" / "2022_3.zip"
    package_path.parent.mkdir(parents=True)
    package_path.write_bytes(b"fixture")

    monkeypatch.setattr(jobs, "get_settings", lambda: _settings(raw_root))

    @contextmanager
    def acquired_guard():
        yield True

    monkeypatch.setattr(jobs, "cn_ingestion_guard", acquired_guard)
    monkeypatch.setattr(jobs, "recover_interrupted_cn_ingestions", lambda: [])
    monkeypatch.setattr(
        jobs,
        "pending_packages",
        lambda *args, **kwargs: [
            {
                "package_id": "bb075784-a983-479f-9646-5584919c3f17",
                "file_name": "2022_3.zip",
                "file_path": str(package_path),
                "sha256": "",
                "status": "FAILED",
            }
        ],
    )

    profile: dict[str, object] = {"active": False}

    @contextmanager
    def fake_profile(**kwargs):
        profile.update(kwargs)
        profile["active"] = True
        try:
            yield
        finally:
            profile["active"] = False

    monkeypatch.setattr(jobs, "clickhouse_execution_settings", fake_profile)

    def fake_ingest(*args, **kwargs):
        assert profile["active"] is True
        assert kwargs["retrying"] is True
        return {"rows": 1}

    monkeypatch.setattr(jobs, "ingest_cn_package", fake_ingest)

    result = jobs.ingest_pending_cn(
        trigger_type="ADMIN_UI_RETRY",
        include_failed=True,
        limit=1,
    )

    assert result["success"] == 1
    assert result["failed"] == 0
    assert profile["join_algorithm"] == "grace_hash"
    assert profile["grace_hash_join_initial_buckets"] == 32
    assert profile["send_receive_timeout"] == 3600
