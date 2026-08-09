from pathlib import Path


def test_cn_ingestion_uses_session_advisory_lock_and_orphan_recovery():
    guard = Path("app/cn/run_guard.py").read_text(encoding="utf-8")
    assert "pg_try_advisory_lock" in guard
    assert "pg_advisory_unlock" in guard
    assert "status = 'INTERRUPTED'" in guard
    assert "status = 'PROCESSING'" in guard
    assert "job_type = 'CN_PACKAGE_INGESTION'" in guard


def test_normal_queue_retries_interrupted_before_newer_source_rank_work():
    jobs = Path("app/jobs.py").read_text(encoding="utf-8")
    repository = Path("app/repository.py").read_text(encoding="utf-8")
    assert '("INTERRUPTED", "REGISTERED")' in jobs
    assert '{"INTERRUPTED", "FAILED", "MISSING_FILE"}' in jobs
    assert "recover_interrupted_cn_ingestions()" in jobs
    assert "ORDER BY source_rank, package_sequence" in repository


def test_concurrent_cn_ingest_returns_busy_instead_of_racing():
    jobs = Path("app/jobs.py").read_text(encoding="utf-8")
    assert 'result["busy"] = True' in jobs
    assert "with cn_ingestion_guard() as acquired" in jobs
