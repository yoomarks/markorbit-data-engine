from __future__ import annotations

from datetime import datetime

import pytest

from app.component_versions import REPLAY_TELEMETRY_VERSION
from app import replay_telemetry


def test_calculate_runtime_delta_tracks_storage_and_package_changes() -> None:
    before = {
        "clickhouse": {
            "active_bytes": 1000,
            "active_rows": 100,
            "active_stage_bytes": 50,
        },
        "packages": {
            "status_counts": {
                "REGISTERED": 4,
                "SUCCESS": 10,
            }
        },
    }
    after = {
        "clickhouse": {
            "active_bytes": 1450,
            "active_rows": 125,
            "active_stage_bytes": 0,
        },
        "packages": {
            "status_counts": {
                "REGISTERED": 2,
                "SUCCESS": 12,
                "FAILED": 0,
            }
        },
    }

    delta = replay_telemetry.calculate_runtime_delta(before, after)

    assert delta == {
        "clickhouse_active_bytes": 450,
        "clickhouse_active_rows": 25,
        "clickhouse_stage_bytes": -50,
        "package_status_counts": {
            "FAILED": 0,
            "REGISTERED": -2,
            "SUCCESS": 2,
        },
    }


def test_build_snapshot_normalizes_jurisdiction_and_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        replay_telemetry,
        "_package_state",
        lambda jurisdiction: {
            "status_counts": {"SUCCESS": 3},
            "registered_package_count": 3,
            "latest_success": {"file_name": "p.zip"},
        },
    )
    monkeypatch.setattr(
        replay_telemetry,
        "_clickhouse_state",
        lambda: {
            "active_bytes": 123,
            "active_rows": 45,
            "active_stage_bytes": 0,
            "active_stage_rows": 0,
            "disks": [],
        },
    )
    monkeypatch.setattr(
        replay_telemetry,
        "component_versions",
        lambda: {"matrix_version": "TEST"},
    )

    report = replay_telemetry.build_snapshot("us_assignment")

    assert report["telemetry_version"] == REPLAY_TELEMETRY_VERSION
    assert report["read_only"] is True
    assert report["jurisdiction"] == "US_ASSIGNMENT"
    assert report["packages"]["status_counts"] == {"SUCCESS": 3}
    assert report["clickhouse"]["active_bytes"] == 123
    assert report["component_versions"] == {"matrix_version": "TEST"}
    datetime.fromisoformat(report["captured_at"])


def test_build_snapshot_rejects_unknown_jurisdiction() -> None:
    with pytest.raises(ValueError, match="jurisdiction must be one of"):
        replay_telemetry.build_snapshot("OAPI")


def test_row_to_dict_supports_psycopg_dict_rows_and_sequences() -> None:
    columns = ("package_id", "file_name")
    assert replay_telemetry._row_to_dict(
        {"package_id": "1", "file_name": "a.zip"}, columns
    ) == {"package_id": "1", "file_name": "a.zip"}
    assert replay_telemetry._row_to_dict(("2", "b.zip"), columns) == {
        "package_id": "2",
        "file_name": "b.zip",
    }
    assert replay_telemetry._row_to_dict(None, columns) is None
