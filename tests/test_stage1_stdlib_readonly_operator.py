from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from app.us import repository as us_repository


ROOT = Path(__file__).resolve().parents[1]


def test_stage1_contract_modules_import_without_site_packages() -> None:
    code = """
from app.us.target_canary import APPLICATION_CANARY_TABLES
from app.us.replay_executor import build_replay_plan
from app.us.target_canary_review import FINAL_READY_DECISION
from app.us.target_canary_stage1_plan import build_stage1_replay_plan
assert len(APPLICATION_CANARY_TABLES) == 12
assert callable(build_replay_plan)
assert callable(build_stage1_replay_plan)
assert FINAL_READY_DECISION == 'BOUNDED_US_APPLICATION_CANARY_REVIEW_READY_FOR_OPERATOR_GO'
"""
    completed = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_stdlib_settings_fallback_exposes_only_raw_root() -> None:
    code = """
import os
os.environ['RAW_DATA_ROOT'] = 'operator-raw-root'
from app.config import Settings, get_settings
settings = get_settings()
assert str(settings.raw_data_root) == 'operator-raw-root'
try:
    settings.postgres_host
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('stdlib fallback unexpectedly exposed application runtime settings')
try:
    Settings()
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('full Settings unexpectedly instantiated without pydantic-settings')
"""
    completed = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_docker_registry_fallback_is_read_only_and_fail_closed(monkeypatch) -> None:
    calls: list[tuple[list[str], str]] = []

    inspect_payload = [
        {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "markorbit-data-engine",
                    "com.docker.compose.service": "postgres",
                },
                "Env": [
                    "POSTGRES_USER=markorbit",
                    "POSTGRES_DB=markorbit_control",
                ],
            },
            "State": {
                "Status": "running",
                "Health": {"Status": "healthy"},
            },
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": "markorbit-data-engine_postgres_data",
                    "Destination": "/var/lib/postgresql/data",
                }
            ],
        }
    ]
    registry_payload = [
        {
            "package_id": "00000000-0000-0000-0000-000000000001",
            "package_sequence": 1,
            "file_name": "apc18840407-20251231-01.zip",
            "sha256": "a" * 64,
            "package_kind": "HISTORICAL_APPLICATIONS",
            "partition_dimension": "COVERAGE_RANGE_PART",
            "partition_value": "1884-04-07/2025-12-31#001",
            "source_rank": 1,
            "status": "SUCCESS",
            "profile": {},
            "schema_version": "US_M1.4",
        }
    ]

    def fake_run(args: list[str], *, label: str) -> str:
        calls.append((list(args), label))
        if args[:2] == ["docker", "ps"]:
            return "abc123\n"
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(inspect_payload)
        if args[:2] == ["docker", "exec"]:
            return json.dumps(registry_payload)
        raise AssertionError(args)

    monkeypatch.setattr(us_repository, "_run_readonly_docker_command", fake_run)
    rows = us_repository._list_us_replay_registry_via_docker_psql()

    assert rows == registry_payload
    exec_args = next(args for args, _label in calls if args[:2] == ["docker", "exec"])
    assert "PGOPTIONS=-c default_transaction_read_only=on" in exec_args
    assert "psql" in exec_args
    sql = exec_args[-1].lower()
    assert sql.lstrip().startswith("select")
    for forbidden in (" insert ", " update ", " delete ", " alter ", " drop ", " truncate "):
        assert forbidden not in f" {sql} "

    joined = " ".join(" ".join(args) for args, _label in calls).lower()
    for forbidden in (
        "docker start",
        "docker restart",
        "docker stop",
        "docker rm",
        "docker compose up",
        "docker compose run",
    ):
        assert forbidden not in joined


def test_stage1_operator_never_installs_python_runtime_packages() -> None:
    text = (ROOT / "scripts" / "freeze-production-us-application-canary-stage1.ps1").read_text(
        encoding="utf-8"
    ).lower()
    assert "pip install" not in text
    assert "python -m pip" not in text


def test_stage1_operator_uses_accepted_pilot_planner_not_generic_registry_dry_run() -> None:
    text = (ROOT / "scripts" / "freeze-production-us-application-canary-stage1.ps1").read_text(
        encoding="utf-8"
    )
    assert "app.us.target_canary_stage1_plan" in text
    assert "app.us.replay_executor --expected-history-parts 91 --max-packages 1" not in text
