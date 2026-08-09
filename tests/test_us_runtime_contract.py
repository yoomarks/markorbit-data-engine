from pathlib import Path

import pytest

import app.us.jobs as us_jobs


def test_run_us_applies_schema_and_uses_one_shot_worker() -> None:
    source = Path("scripts/run-us.ps1").read_text(encoding="utf-8")
    assert "apply-us-m1-schema.ps1" in source
    assert "python -m app.us.run_once" in source
    assert "python -m app.cn" not in source


def test_retry_us_applies_schema_and_uses_retry_runner() -> None:
    source = Path("scripts/retry-us.ps1").read_text(encoding="utf-8")
    assert "apply-us-m1-schema.ps1" in source
    assert "python -m app.us.retry_once" in source
    assert "python -m app.cn" not in source


def test_us_registration_uses_us_package_descriptor() -> None:
    source = Path("app/us/repository.py").read_text(encoding="utf-8")
    assert "infer_us_package_descriptor" in source
    assert 'schema_version = EXCLUDED.schema_version' in source
    assert "app.cn.package_meta" not in source


def test_us_ingest_uses_snapshot_aware_publisher_and_reports_tombstones() -> None:
    source = Path("app/us/ingest.py").read_text(encoding="utf-8")
    assert "SnapshotAwareUSBatchPublisher" in source
    assert "snapshot_tombstone_counts" in source
    assert "publisher.tombstone_counts" in source


def test_us_retry_cleanup_covers_every_published_table() -> None:
    source = Path("app/us/ingest.py").read_text(encoding="utf-8")
    for table in (
        "us_case_current",
        "us_owner_current",
        "us_classification_current",
        "us_event_history",
        "us_statement_current",
    ):
        assert table in source
    assert "_cleanup_package_outputs(package_uuid)" in source
    assert "mutations_sync = 1" in source


def test_normal_us_replay_blocks_when_failed_package_exists(monkeypatch) -> None:
    monkeypatch.setattr(
        us_jobs,
        "list_us_blocking_failures",
        lambda: [
            {
                "file_name": "apc260807.zip",
                "status": "FAILED",
                "source_rank": 1,
            }
        ],
    )
    called: list[str] = []
    monkeypatch.setattr(us_jobs, "scan_us_incoming", lambda **_kwargs: called.append("scan"))
    monkeypatch.setattr(us_jobs, "ingest_pending_us", lambda **_kwargs: called.append("ingest"))

    with pytest.raises(RuntimeError, match="retry-us.ps1"):
        us_jobs.scan_and_ingest_us()
    assert called == []
