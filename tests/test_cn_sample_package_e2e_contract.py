from __future__ import annotations

import inspect
from pathlib import Path

import app.cn.validate_sample_package_e2e as sample_e2e


def test_sample_e2e_uses_real_package_control_and_ingest_path() -> None:
    source = inspect.getsource(sample_e2e)
    assert 'FIXTURE_VERSION = "CN_BOUNDED_REAL_ZIP_E2E_V1"' in source
    assert "register_package(" in source
    assert "ingest_cn_package(" in source
    assert "zipfile.ZipFile(" in source
    assert 'base_path = incoming / "2099.zip"' in source
    assert 'patch_path = incoming / "2099_1.zip"' in source
    assert "duplicate_inserted" in source
    assert '"full_corpus_scale_claimed": False' in source


def test_local_receipt_operator_never_starts_docker() -> None:
    script = Path("scripts/check-cn-acceptance-receipt.ps1").read_text(encoding="utf-8")
    lowered = script.lower()
    assert "docker compose" not in lowered
    assert "docker desktop" not in lowered
    assert "postgres" not in lowered
    assert "clickhouse" not in lowered
    assert "app.cn.acceptance_receipt" in script
