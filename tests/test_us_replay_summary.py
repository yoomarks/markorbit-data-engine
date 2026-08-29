from __future__ import annotations

import json
from pathlib import Path

from app.us.replay_summary import SUMMARY_VERSION, build_summary, write_summary


def _step(sequence: int = 1) -> dict:
    return {
        "sequence": sequence,
        "file_name": "apc-example.zip",
        "sha256": "a" * 64,
        "action": "REGISTER_AND_INGEST",
    }


def test_dry_run_summary_captures_only_operator_gate_fields(tmp_path: Path):
    report = {
        "mode": "DRY_RUN",
        "status": "READY",
        "executor_version": "US_DETERMINISTIC_REPLAY_V1",
        "safe_to_execute": True,
        "remaining_count": 310,
        "next_step": _step(),
        "steps": [_step(i) for i in range(1, 311)],
        "preflight": {"sources": [{"large": "payload"}]},
    }
    summary = build_summary(report)
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["dry_run_ready"] is True
    assert summary["apply_one_package_ok"] is False
    assert summary["remaining_count"] == 310
    assert summary["next_step"]["file_name"] == "apc-example.zip"
    assert "steps" not in summary
    assert "preflight" not in summary


def test_apply_one_package_summary_requires_exactly_one_processed_package():
    report = {
        "mode": "APPLY",
        "status": "PAUSED",
        "executor_version": "US_DETERMINISTIC_REPLAY_V1",
        "processed_count": 1,
        "source_preflight_runs": 1,
        "processed": [
            {
                "sequence": 1,
                "package_id": "pkg-1",
                "file_name": "apc-example.zip",
                "sha256": "b" * 64,
                "retrying": False,
                "metrics": {"huge": [1, 2, 3]},
            }
        ],
        "final_plan": {"remaining_count": 309, "next_step": _step(2)},
    }
    summary = build_summary(report)
    assert summary["apply_one_package_ok"] is True
    assert summary["processed_count"] == 1
    assert summary["source_preflight_runs"] == 1
    assert summary["first_processed"]["package_id"] == "pkg-1"
    assert summary["remaining_count"] == 309


def test_write_summary_validates_full_json_with_python(tmp_path: Path):
    input_path = tmp_path / "replay.json"
    output_path = tmp_path / "replay.json.summary.json"
    input_path.write_text(
        json.dumps(
            {
                "mode": "DRY_RUN",
                "status": "READY",
                "safe_to_execute": True,
                "next_step": _step(),
            }
        ),
        encoding="utf-8",
    )
    summary = write_summary(input_path, output_path)
    assert output_path.is_file()
    assert summary["dry_run_ready"] is True
    assert json.loads(output_path.read_text(encoding="utf-8"))["summary_version"] == SUMMARY_VERSION


def test_windows_operator_never_converts_full_replay_json():
    replay = Path("scripts/replay-us-deterministic.ps1").read_text(encoding="utf-8")
    pilot = Path("scripts/run-us-capacity-pilot.ps1").read_text(encoding="utf-8")
    assert "app.us.replay_summary" in replay
    assert "$summaryJson | ConvertFrom-Json" in replay
    assert "$json | ConvertFrom-Json" not in replay
    assert '$dryRunSummaryPath = "$dryRunPath.summary.json"' in pilot
    assert "Get-Content -LiteralPath $dryRunSummaryPath" in pilot
    assert "Get-Content -LiteralPath $dryRunPath -Raw -Encoding UTF8 | ConvertFrom-Json" not in pilot
