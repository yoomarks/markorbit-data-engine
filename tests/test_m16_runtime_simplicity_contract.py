from pathlib import Path


def test_m16_retry_uses_full_package_replay_without_checkpoint_hooks():
    source = Path("app/cn/ingest_m16.py").read_text(encoding="utf-8")
    assert "PACKAGE_REPLAY" in source
    assert "legacy.ingest_cn_package" in source
    assert "retrying=retrying" in source
    assert "app.cn.checkpoint" not in source
    assert "validated_completed_member_names" not in source
    assert "StageBatchWriter" not in source


def test_manual_runner_keeps_api_online_and_uses_one_shot_worker():
    source = Path("scripts/run-cn.ps1").read_text(encoding="utf-8")
    assert "docker compose run --rm --no-deps worker python -m app.cn.run_once" in source
    assert "docker compose stop api" not in source
    assert "docker compose up -d api" not in source


def test_one_shot_runner_declares_package_replay_mode():
    source = Path("app/cn/run_once.py").read_text(encoding="utf-8")
    assert '"mode": "DEDICATED_WORKER_ONE_SHOT"' in source
    assert '"recovery": "PACKAGE_REPLAY"' in source
