from pathlib import Path


def test_replay_plan_script_is_read_only_and_worker_safe() -> None:
    script = Path("scripts/plan-m16-replay.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running --services worker" in script
    assert "Persistent worker is running" in script
    assert "python -m app.cn.replay_plan" in script
    assert "Persistent worker remains stopped. No package was registered or ingested." in script
    assert "docker compose start worker" not in script
    assert "/api/jobs/cn/run" not in script
    assert "/api/jobs/cn/retry" not in script


def test_replay_plan_documentation_freezes_clean_reset_boundary() -> None:
    docs = Path("docs/M1_6_DETERMINISTIC_REPLAY_PLAN.md").read_text(encoding="utf-8")
    assert "CLEAN_RESET_READY_FOR_REPLAY" in docs
    assert "scanner_registration_order" in docs
    assert "expected_processing_order" in docs
    assert "same-partition revision requires explicit official revision evidence" in docs
