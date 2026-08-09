from pathlib import Path


def test_preflight_script_is_non_destructive_and_worker_safe() -> None:
    script = Path("scripts/preflight-m16-real-data.ps1").read_text(encoding="utf-8")
    assert "docker compose ps --status running --services worker" in script
    assert "Persistent worker is running" in script
    assert "docker compose build worker" in script
    assert "python -m app.cn.preflight_m16_real_data" in script
    assert "safe_to_run_replay_command" in script
    assert "safe_to_run_inference_audit" in script
    assert "docker compose start worker" not in script
    assert "docker compose down" not in script
    assert "/api/jobs/cn/run" not in script
    assert "/api/jobs/cn/retry" not in script


def test_readme_requires_preflight_before_real_replay() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "preflight-m16-real-data.ps1" in readme
    assert "safe_to_run_inference_audit = true" in readme
    assert "docs/M1_6_REAL_DATA_PREFLIGHT.md" in readme
