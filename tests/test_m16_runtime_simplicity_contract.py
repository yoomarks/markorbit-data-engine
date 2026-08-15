from pathlib import Path


def test_m16_retry_uses_durable_stage_checkpoint_before_raw_reparse():
    source = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    resume = Path("app/cn/stage_resume.py").read_text(encoding="utf-8")
    assert "legacy.ingest_cn_package" in source
    assert "resume_staged_package" in source
    assert "stage_checkpoint_is_usable" in source
    assert "save_stage_checkpoint" in source
    assert "STAGE_CHECKPOINT_RESUME" in source
    assert "CN_M16_STAGE_V1" in resume
    assert "stage_counts" in resume
    assert "source_sha256" in resume


def test_manual_runner_keeps_api_online_and_uses_guarded_one_shot_worker():
    source = Path("scripts/run-cn.ps1").read_text(encoding="utf-8")
    assert "docker compose run --rm --no-deps worker python -m app.cn.guarded_run_once" in source
    assert "docker compose stop api" not in source
    assert "docker compose up -d api" not in source


def test_guarded_one_shot_preserves_package_replay_execution():
    source = Path("app/cn/guarded_run_once.py").read_text(encoding="utf-8")
    ingest = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    assert "build_execution_guard()" in source
    assert "scan_and_ingest_cn" in source
    assert "PACKAGE_REPLAY" in ingest
    assert "checkpoint" not in source.lower()
