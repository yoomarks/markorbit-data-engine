from __future__ import annotations

from pathlib import Path

import pytest

import app.cn_qcc.operator as operator


def _batch(**overrides) -> dict[str, object]:
    values: dict[str, object] = {
        "batch_id": "11111111-1111-1111-1111-111111111111",
        "batch_key": "CN_QCC_20260821T020000Z_11111111",
        "status": "EXPORTED",
        "task_count": 12,
        "export_path": "/tmp/tasks.csv",
        "export_sha256": "a" * 64,
        "result_path": "",
        "planned_at": None,
        "exported_at": None,
    }
    values.update(overrides)
    return values


def test_expected_paths_are_deterministic(tmp_path: Path) -> None:
    key = "CN_QCC_20260821T020000Z_11111111"
    assert operator.outgoing_path(tmp_path / "out", key).name == f"{key}.tasks.csv"
    assert operator.expected_result_path(tmp_path / "in", key).name == f"{key}.result.csv"


def test_disabled_state_does_not_touch_database(monkeypatch, tmp_path: Path) -> None:
    def fail_if_called():
        raise AssertionError("disabled acquisition must not query PostgreSQL")

    monkeypatch.setattr(operator, "_open_batch", fail_if_called)
    state = operator.acquisition_state(enabled=False, incoming_root=tmp_path)
    assert state.readiness == "DISABLED"
    assert state.open_batch_id == ""


def test_exported_batch_waits_for_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(operator, "_open_batch", lambda: _batch())
    state = operator.acquisition_state(enabled=True, incoming_root=tmp_path)
    assert state.readiness == "WAITING_RESULT"
    assert state.batch_status == "EXPORTED"
    assert state.result_expected_path.endswith(".result.csv")


def test_result_file_changes_readiness(monkeypatch, tmp_path: Path) -> None:
    batch = _batch()
    monkeypatch.setattr(operator, "_open_batch", lambda: batch)
    result_path = operator.expected_result_path(tmp_path, str(batch["batch_key"]))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text("task_id,result_status\n", encoding="utf-8")
    state = operator.acquisition_state(enabled=True, incoming_root=tmp_path)
    assert state.readiness == "RESULT_READY"


def test_planned_batch_is_ready_to_export(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(operator, "_open_batch", lambda: _batch(status="PLANNED"))
    state = operator.acquisition_state(enabled=True, incoming_root=tmp_path)
    assert state.readiness == "READY_TO_EXPORT"


def test_cycle_rejects_invalid_policy_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="capacity"):
        operator.run_cycle(
            enabled=False,
            capacity=0,
            refresh_days=180,
            outgoing_root=tmp_path,
            incoming_root=tmp_path,
        )
    with pytest.raises(ValueError, match="refresh_days"):
        operator.run_cycle(
            enabled=False,
            capacity=1,
            refresh_days=0,
            outgoing_root=tmp_path,
            incoming_root=tmp_path,
        )


def test_disabled_cycle_is_explicit(monkeypatch, tmp_path: Path) -> None:
    def fail_if_called():
        raise AssertionError("disabled cycle must not query PostgreSQL")

    monkeypatch.setattr(operator, "_open_batch", fail_if_called)
    result = operator.run_cycle(
        enabled=False,
        capacity=500,
        refresh_days=180,
        outgoing_root=tmp_path,
        incoming_root=tmp_path,
    )
    assert result["action"] == "DISABLED"
    assert result["state"]["readiness"] == "DISABLED"
