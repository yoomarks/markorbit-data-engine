from pathlib import Path


def test_clickhouse_final_alias_order_is_24_8_compatible():
    source = Path("app/cn/ingest.py").read_text(encoding="utf-8")
    assert "FINAL AS current" not in source
    assert "FINAL AS cur" not in source
    assert "cn_case_current AS cur FINAL" in source
    assert "cn_case_scope_current AS cur FINAL" in source


def test_failed_package_retry_cleanup_is_present():
    source = Path("app/cn/ingest.py").read_text(encoding="utf-8")
    assert "def _cleanup_partial_outputs" in source
    assert "_cleanup_stage(package_uuid)" in source
    assert "_cleanup_partial_outputs(package_uuid)" in source


def test_retry_cleanup_only_runs_for_failed_packages():
    ingest = Path("app/cn/ingest.py").read_text(encoding="utf-8")
    jobs = Path("app/jobs.py").read_text(encoding="utf-8")
    assert "if retrying:" in ingest
    assert 'retrying=package["status"] == "FAILED"' in jobs
