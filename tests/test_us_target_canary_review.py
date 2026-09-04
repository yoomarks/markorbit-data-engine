from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.us.target_canary import APPLICATION_CANARY_TABLES, TARGET_STORAGE_POLICY
from app.us.target_canary_review import (
    ACCEPTED_PILOT_EVIDENCE_REF,
    ACCEPTED_PILOT_REGISTRY_ID,
    EXPECTED_HISTORY_PARTS,
    FINAL_READY_DECISION,
    PILOT_FILE_NAME,
    PILOT_SHA256,
    STAGE1_REGISTRY_BASIS,
    STAGE1_SOURCE_DECISION,
    build_stage1_source_review,
)


def _source_schema_lines() -> list[str]:
    return [
        (
            '{"name":"'
            + table.split(".", 1)[1]
            + '","create_table_query":"CREATE TABLE markorbit_facts.'
            + table.split(".", 1)[1]
            + " (id String, source_package_id UUID, last_source_package_id UUID) "
            + "ENGINE = MergeTree ORDER BY id" + '"}'
        )
        for table in APPLICATION_CANARY_TABLES
    ]


def _plan(tmp_path: Path) -> tuple[dict[str, object], Path]:
    package = tmp_path / "apc18840407-20251231-02.zip"
    package.write_bytes(b"bounded-second-package")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    pilot_path = tmp_path / PILOT_FILE_NAME
    source = {
        "file_name": package.name,
        "path": str(package),
        "location": "incoming",
        "file_size": package.stat().st_size,
        "sha256": digest,
        "package_kind": "HISTORICAL_APPLICATIONS",
        "partition_dimension": "COVERAGE_RANGE_PART",
        "partition_value": "1884-04-07/2025-12-31#002",
        "source_period_start": "1884-04-07",
        "source_period_end": "2025-12-31",
        "source_sequence": 20251231002,
        "inspection": {"readable": True},
    }
    first = {
        "sequence": 1,
        "package_kind": "HISTORICAL_APPLICATIONS",
        "partition_value": "1884-04-07/2025-12-31#001",
        "file_name": PILOT_FILE_NAME,
        "path": str(pilot_path),
        "location": "archive",
        "sha256": PILOT_SHA256,
        "registry_package_id": ACCEPTED_PILOT_REGISTRY_ID,
        "registry_status": "SUCCESS",
        "source_rank": 1,
        "action": "SKIP_SUCCESS",
    }
    second = {
        "sequence": 2,
        "package_kind": source["package_kind"],
        "partition_value": source["partition_value"],
        "file_name": source["file_name"],
        "path": source["path"],
        "location": "incoming",
        "sha256": digest,
        "registry_package_id": None,
        "registry_status": "UNREGISTERED",
        "source_rank": None,
        "action": "REGISTER_AND_INGEST",
    }
    filler = [
        {
            "sequence": index,
            "package_kind": "HISTORICAL_APPLICATIONS",
            "partition_value": f"dummy#{index:03d}",
            "file_name": f"dummy-{index}.zip",
            "path": str(tmp_path / f"dummy-{index}.zip"),
            "location": "incoming",
            "sha256": f"{index:064x}"[-64:],
            "registry_package_id": None,
            "registry_status": "UNREGISTERED",
            "source_rank": None,
            "action": "REGISTER_AND_INGEST",
        }
        for index in range(3, 311)
    ]
    plan: dict[str, object] = {
        "mode": "DRY_RUN",
        "status": "READY",
        "safe_to_execute": True,
        "expected_history_parts": EXPECTED_HISTORY_PARTS,
        "success_prefix_count": 1,
        "remaining_count": 309,
        "registry_package_count": 1,
        "registry_basis": STAGE1_REGISTRY_BASIS,
        "live_registry_read": False,
        "accepted_pilot_evidence": {
            "reference": ACCEPTED_PILOT_EVIDENCE_REF,
            "sequence": 1,
            "file_name": PILOT_FILE_NAME,
            "sha256": PILOT_SHA256,
            "current_path": str(pilot_path),
        },
        "next_step": second,
        "steps": [first, second, *filler],
        "preflight": {
            "source_inventory": {
                "sources": [source],
            }
        },
    }
    return plan, package


def test_stage1_review_freezes_exact_second_package_and_hot_us_schema(tmp_path: Path) -> None:
    plan, package = _plan(tmp_path)
    review = build_stage1_source_review(plan, _source_schema_lines())

    assert review["decision"] == STAGE1_SOURCE_DECISION
    assert review["final_ready_decision_if_host_gates_pass"] == FINAL_READY_DECISION
    assert review["mode"] == "READ_ONLY_REVIEW"
    assert review["continuity"]["registry_basis"] == STAGE1_REGISTRY_BASIS
    assert review["continuity"]["live_registry_read"] is False
    assert review["package"]["sequence"] == 2
    assert review["package"]["file_name"] == package.name
    assert review["package"]["size_bytes"] == package.stat().st_size
    assert review["package"]["sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()
    assert review["package"]["package_kind"] == "HISTORICAL_APPLICATIONS"
    assert review["package"]["partition_value"] == "1884-04-07/2025-12-31#002"
    assert review["target"]["storage_policy"] == TARGET_STORAGE_POLICY
    assert review["target"]["first_canary_requires_all_required_tables_absent"] is True
    assert review["schema_manifest"]["tables"] == list(APPLICATION_CANARY_TABLES)
    assert review["safety"]["target_mutation_performed"] is False
    assert review["safety"]["stage2_go_consumed"] is False
    for statement in review["schema_manifest"]["statements"][1:]:
        assert f"storage_policy = '{TARGET_STORAGE_POLICY}'" in statement


def test_stage1_review_rejects_registered_or_shifted_next_package(tmp_path: Path) -> None:
    plan, _package = _plan(tmp_path)
    plan["next_step"]["registry_status"] = "REGISTERED"
    plan["next_step"]["action"] = "INGEST"
    with pytest.raises(RuntimeError, match="already registered"):
        build_stage1_source_review(plan, _source_schema_lines())

    plan, _package = _plan(tmp_path)
    plan["next_step"]["sequence"] = 3
    with pytest.raises(RuntimeError, match="sequence is not 2"):
        build_stage1_source_review(plan, _source_schema_lines())


def test_stage1_review_rejects_pilot_or_source_identity_drift(tmp_path: Path) -> None:
    plan, package = _plan(tmp_path)
    plan["steps"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="pilot SHA-256"):
        build_stage1_source_review(plan, _source_schema_lines())

    plan, package = _plan(tmp_path)
    package.write_bytes(b"changed-after-plan")
    with pytest.raises(RuntimeError, match="size mismatch|SHA-256 mismatch"):
        build_stage1_source_review(plan, _source_schema_lines())


def test_stage1_review_requires_exact_source_schema_set(tmp_path: Path) -> None:
    plan, _package = _plan(tmp_path)
    lines = _source_schema_lines()
    with pytest.raises(RuntimeError, match="schema set mismatch"):
        build_stage1_source_review(plan, lines[:-1])
