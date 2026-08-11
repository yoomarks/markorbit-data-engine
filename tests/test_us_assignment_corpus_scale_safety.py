from __future__ import annotations

import inspect
from pathlib import Path

from app.us_assignment import corpus_replay


def test_assignment_apply_reuses_one_full_source_preflight_per_process():
    source = inspect.getsource(corpus_replay.execute_replay)
    assert source.count("preflight_manifest(") == 1
    assert "_build_replay_plan_from_preflight(preflight)" in source
    assert "build_replay_plan(manifest_path, raw_root)" not in source
    assert '"source_preflight_runs": 1' in source


def test_assignment_corpus_one_shot_scripts_build_current_worker_image():
    for path in (
        "scripts/preflight-us-assignment-corpus.ps1",
        "scripts/replay-us-assignment-deterministic.ps1",
        "scripts/audit-us-assignment-corpus.ps1",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "--build" in source, path
