from __future__ import annotations

from pathlib import Path

import pytest

from app.us import target_canary_stage1_plan as stage1_plan
from app.us.target_canary_review import (
    ACCEPTED_PILOT_EVIDENCE_REF,
    ACCEPTED_PILOT_REGISTRY_ID,
    PILOT_FILE_NAME,
    PILOT_SHA256,
    STAGE1_REGISTRY_BASIS,
    _validate_accepted_pilot_evidence,
)


def _preflight(*, pilot_sha: str = PILOT_SHA256) -> dict[str, object]:
    return {
        "safe_to_replay": True,
        "status": "PASS",
        "replay_plan": [
            {
                "sequence": 1,
                "package_kind": "HISTORICAL_APPLICATIONS",
                "partition_value": "1884-04-07/2025-12-31#001",
                "file_name": PILOT_FILE_NAME,
                "path": f"/accepted/archive/{PILOT_FILE_NAME}",
                "location": "archive",
                "sha256": pilot_sha,
            },
            {
                "sequence": 2,
                "package_kind": "HISTORICAL_APPLICATIONS",
                "partition_value": "1884-04-07/2025-12-31#002",
                "file_name": "apc18840407-20251231-02.zip",
                "path": "/accepted/incoming/apc18840407-20251231-02.zip",
                "location": "incoming",
                "sha256": "b" * 64,
            },
        ],
    }


def test_stage1_planner_uses_only_accepted_pilot_success_prefix(monkeypatch) -> None:
    preflight = _preflight()
    captured: dict[str, object] = {}

    monkeypatch.setattr(stage1_plan, "build_preflight", lambda *args, **kwargs: preflight)

    def fake_build_replay_plan(*args, **kwargs):
        captured.update(kwargs)
        return {
            "status": "READY",
            "safe_to_execute": True,
            "registry_package_count": 1,
        }

    monkeypatch.setattr(stage1_plan, "build_replay_plan", fake_build_replay_plan)

    result = stage1_plan.build_stage1_replay_plan(
        Path("/operator/raw"),
        expected_history_parts=91,
    )

    registry_rows = captured["registry_rows"]
    assert isinstance(registry_rows, list)
    assert len(registry_rows) == 1
    pilot = registry_rows[0]
    assert pilot["package_id"] == ACCEPTED_PILOT_REGISTRY_ID
    assert pilot["status"] == "SUCCESS"
    assert pilot["file_name"] == PILOT_FILE_NAME
    assert pilot["sha256"] == PILOT_SHA256
    assert pilot["source_rank"] > 0
    assert pilot["profile"]["source_sha256"] == PILOT_SHA256
    assert captured["source_preflight"] is preflight

    assert result["registry_basis"] == STAGE1_REGISTRY_BASIS
    assert result["live_registry_read"] is False
    assert result["accepted_pilot_evidence"]["reference"] == ACCEPTED_PILOT_EVIDENCE_REF


def test_stage1_planner_fails_closed_when_current_pilot_sha_drifts(monkeypatch) -> None:
    monkeypatch.setattr(
        stage1_plan,
        "build_preflight",
        lambda *args, **kwargs: _preflight(pilot_sha="0" * 64),
    )

    def should_not_plan(*args, **kwargs):
        raise AssertionError("build_replay_plan must not run after accepted pilot drift")

    monkeypatch.setattr(stage1_plan, "build_replay_plan", should_not_plan)

    with pytest.raises(RuntimeError, match="pilot SHA-256 identity drifted"):
        stage1_plan.build_stage1_replay_plan(
            Path("/operator/raw"),
            expected_history_parts=91,
        )


def test_stage1_review_rejects_live_or_unattributed_registry_basis() -> None:
    with pytest.raises(RuntimeError, match="accepted-pilot evidence"):
        _validate_accepted_pilot_evidence(
            {
                "registry_basis": "LIVE_REGISTRY",
                "live_registry_read": True,
                "registry_package_count": 1,
            }
        )
