from __future__ import annotations

import inspect
from pathlib import Path

from app.us import replay_executor


def test_application_apply_runs_full_source_preflight_once_per_process():
    source = inspect.getsource(replay_executor.execute_replay)
    assert source.count("build_preflight(") == 1
    assert "source_preflight=source_preflight" in source
    assert 'result["source_preflight_runs"] = 1' in source
    assert "_discovered_package(step)" in source


def test_application_plan_can_reuse_frozen_source_preflight():
    source = inspect.getsource(replay_executor.build_replay_plan)
    assert "source_preflight:" in source
    assert "source_preflight or build_preflight" in source


def test_application_one_shot_scripts_build_current_worker_image():
    for path in (
        "scripts/preflight-us-source-replay.ps1",
        "scripts/stage-us-replay-sources.ps1",
        "scripts/replay-us-deterministic.ps1",
        "scripts/audit-us-real-data.ps1",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "--build" in source, path


def test_application_completion_does_not_require_optional_full_source_rehash():
    source = Path("scripts/replay-us-deterministic.ps1").read_text(encoding="utf-8")
    assert "VerifySourceFiles before treating the corpus as accepted" not in source
    assert "source re-hash remains optional" in source
