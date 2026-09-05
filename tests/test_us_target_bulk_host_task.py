from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app import admin_task_api
from app.admin_domain_tasks import ADMIN_TASK_KIND
from app.us.target_bulk_batch import derive_batch_manifest, validate_batch_manifest
from app.us.target_bulk_plan import (
    ACCEPTED_PACKAGE2_SHA256,
    ACCEPTED_SCHEMA_MANIFEST_SHA256,
    BULK_PLAN_VERSION,
    _canonical_sha256,
    validate_bulk_plan,
)
from app.us.target_bulk_tasks import TARGET_BULK_TASK_KIND


ROOT = Path(__file__).resolve().parents[1]


def _master_plan() -> dict:
    packages = [
        {
            "sequence": 1,
            "role": "PACKAGE1_TARGET_BRIDGE_REQUIRE_OR_ADOPT",
            "file_name": "p1.zip",
            "sha256": "1" * 64,
            "package_id": "00000000-0000-0000-0000-000000000001",
        },
        {
            "sequence": 3,
            "role": "BOUNDED_SUFFIX",
            "file_name": "p3.zip",
            "sha256": "3" * 64,
            "package_id": "00000000-0000-0000-0000-000000000003",
        },
        {
            "sequence": 4,
            "role": "BOUNDED_SUFFIX",
            "file_name": "p4.zip",
            "sha256": "4" * 64,
            "package_id": "00000000-0000-0000-0000-000000000004",
        },
    ]
    contract = {
        "plan_version": BULK_PLAN_VERSION,
        "read_only": True,
        "production_mutation_authorized": False,
        "execution_main": "a" * 40,
        "raw_root": "F:/MarkOrbitData/raw",
        "expected_history_parts": 91,
        "accepted_source_count": 310,
        "accepted_schema_manifest_sha256": ACCEPTED_SCHEMA_MANIFEST_SHA256,
        "accepted_package2_anchor": {"sequence": 2},
        "accepted_package2_source": {
            "sequence": 2,
            "sha256": ACCEPTED_PACKAGE2_SHA256,
        },
        "inventory_sha256": "b" * 64,
        "bridge_sequence": 1,
        "accepted_existing_target_sequence": 2,
        "start_sequence": 3,
        "end_sequence": 4,
        "suffix_package_count": 2,
        "package_count": 3,
        "packages": packages,
    }
    digest = _canonical_sha256(contract)
    plan = {
        **contract,
        "plan_sha256": digest,
        "required_authority_token": f"GO #545 bounded US Application bulk replay {digest}",
    }
    validate_bulk_plan(plan)
    return plan


def test_batch_manifest_seals_every_child_under_one_master_plan(tmp_path) -> None:
    master = _master_plan()
    manifest = derive_batch_manifest(master, output_dir=tmp_path / "children")
    validate_batch_manifest(manifest, master_plan=master)

    assert manifest["master_plan_sha256"] == master["plan_sha256"]
    assert manifest["child_count"] == 2
    assert [item["sequence"] for item in manifest["children"]] == [3, 4]
    assert all(Path(item["plan_path"]).is_file() for item in manifest["children"])
    assert len({item["plan_sha256"] for item in manifest["children"]}) == 2
    assert all(
        item["required_authority_token"].endswith(item["plan_sha256"])
        for item in manifest["children"]
    )

    tampered = deepcopy(manifest)
    tampered["children"][1]["sequence"] = 5
    with pytest.raises(RuntimeError, match="integrity|coverage"):
        validate_batch_manifest(tampered, master_plan=master)


def test_us_continue_defaults_to_full_frozen_suffix_prepare(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {"accepted": True}

    monkeypatch.setattr(admin_task_api, "queue_target_bulk_prepare", fake_prepare)
    result = admin_task_api._queue_us_application_target_task(
        action="CONTINUE",
        expected_history_parts=0,
        bulk_end_sequence=None,
        bulk_max_packages=None,
    )
    assert result["accepted"] is True
    assert captured == {"end_sequence": 310, "max_packages": None}


def test_us_run_prepares_exactly_one_suffix_package(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {"accepted": True}

    monkeypatch.setattr(admin_task_api, "queue_target_bulk_prepare", fake_prepare)
    admin_task_api._queue_us_application_target_task(
        action="RUN",
        expected_history_parts=91,
        bulk_end_sequence=None,
        bulk_max_packages=None,
    )
    assert captured == {"max_packages": 1}


def test_us_retry_resumes_existing_frozen_target_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_task_api,
        "resume_target_bulk_task",
        lambda: {"accepted": True, "task": {"status": "HOST_RUN_QUEUED"}},
    )
    result = admin_task_api._queue_us_application_target_task(
        action="RETRY",
        expected_history_parts=91,
        bulk_end_sequence=None,
        bulk_max_packages=None,
    )
    assert result["task"]["status"] == "HOST_RUN_QUEUED"


def test_us_target_task_rejects_history_count_or_conflicting_bounds() -> None:
    with pytest.raises(ValueError, match="frozen at"):
        admin_task_api._queue_us_application_target_task(
            action="CONTINUE",
            expected_history_parts=90,
            bulk_end_sequence=None,
            bulk_max_packages=None,
        )
    with pytest.raises(ValueError, match="only one"):
        admin_task_api._queue_us_application_target_task(
            action="CONTINUE",
            expected_history_parts=91,
            bulk_end_sequence=20,
            bulk_max_packages=10,
        )


def test_target_bulk_task_kind_is_not_claimable_by_generic_container_worker() -> None:
    assert TARGET_BULK_TASK_KIND == "US_APPLICATION_TARGET_BULK_CONTROL"
    assert TARGET_BULK_TASK_KIND != ADMIN_TASK_KIND
    generic_source = (ROOT / "app" / "admin_domain_tasks.py").read_text(encoding="utf-8")
    target_source = (ROOT / "app" / "us" / "target_bulk_tasks.py").read_text(encoding="utf-8")
    assert "payload->>'task_kind' = %s" in generic_source
    assert "US_APPLICATION_TARGET_BULK_CONTROL" in target_source


def test_host_worker_keeps_guarded_powershell_and_global_mutation_lock() -> None:
    source = (ROOT / "app" / "us" / "target_bulk_host_worker.py").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "run-us-application-target-bulk-host-worker.ps1").read_text(
        encoding="utf-8"
    )
    assert "run-production-us-application-bulk-replay.ps1" in source
    assert "plan-production-us-application-bulk-replay.ps1" in source
    assert "with engine_mutation_guard() as acquired" in source
    assert "target_bulk_stop_requested(run_id)" in source
    assert "completed_sequences" in source
    assert "execute_bulk_plan(" not in source
    assert "WINDOWS_HOST_TARGET" in launcher
    assert "elevated Administrator PowerShell" in launcher


def test_admin_api_exposes_prepare_approve_resume_status_surface() -> None:
    import app.main as main

    methods_by_path = {
        route.path: set(route.methods or set())
        for route in main.app.routes
        if hasattr(route, "methods")
    }
    assert "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/ACTIVE" in methods_by_path
    assert "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/{run_id}/APPROVE" in methods_by_path
    assert "/api/admin/v2/domain-tasks/US_APPLICATION/BULK/{run_id}/RESUME" in methods_by_path
