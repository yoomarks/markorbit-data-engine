from __future__ import annotations

from pathlib import Path

import pytest

from app.us import target_bulk_batch_audit as audit_mod
from app.us import target_bulk_host_worker_v2 as worker_v2
from app.us.target_bulk_batch import derive_batch_manifest
from app.us.target_bulk_plan import (
    ACCEPTED_PACKAGE2_SHA256,
    ACCEPTED_SCHEMA_MANIFEST_SHA256,
    BULK_PLAN_VERSION,
    _canonical_sha256,
    validate_bulk_plan,
)


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


def test_completed_checkpoint_must_be_contiguous_approved_prefix() -> None:
    allowed = [3, 4, 5]
    assert worker_v2._validated_completed_prefix([], allowed_sequences=allowed) == []
    assert worker_v2._validated_completed_prefix([3, 4], allowed_sequences=allowed) == [3, 4]

    with pytest.raises(RuntimeError, match="contiguous approved prefix"):
        worker_v2._validated_completed_prefix([4], allowed_sequences=allowed)
    with pytest.raises(RuntimeError, match="contiguous approved prefix"):
        worker_v2._validated_completed_prefix([3, 5], allowed_sequences=allowed)


def _prepare_audit_files(tmp_path: Path) -> tuple[dict, dict, Path]:
    master = _master_plan()
    manifest = derive_batch_manifest(master, output_dir=tmp_path / "children")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    for child in manifest["children"]:
        (state_dir / f"bulk_{child['plan_sha256']}.journal.json").touch()
    for item in master["packages"]:
        token = item["sha256"][:16]
        (state_dir / f"package_{item['sequence']:03d}_{token}.canary.json").touch()
    return master, manifest, state_dir


def test_batch_final_audit_requires_every_child_and_master_package(monkeypatch, tmp_path) -> None:
    master, manifest, state_dir = _prepare_audit_files(tmp_path)

    monkeypatch.setattr(
        audit_mod,
        "load_bulk_journal",
        lambda path, plan: {
            "state": "COMPLETE",
            "packages": {
                "1": {"status": "COMPLETE", "stage_cleanup_complete": True},
                str(plan["end_sequence"]): {
                    "status": "COMPLETE",
                    "stage_cleanup_complete": True,
                },
            },
        },
    )
    monkeypatch.setattr(audit_mod, "_frozen_from_plan", lambda item: object())
    monkeypatch.setattr(
        audit_mod,
        "_verify_complete_canary",
        lambda client, journal_path, package: {"markorbit_facts.application_case": 1},
    )
    monkeypatch.setattr(audit_mod, "_stage_table_count", lambda client, package: 0)
    monkeypatch.setattr(
        audit_mod,
        "_verify_frozen_package2_anchor",
        lambda client, master_plan: {"markorbit_facts.application_case": 2},
    )
    monkeypatch.setattr(
        audit_mod,
        "_verify_storage",
        lambda client: {
            "final_non_hot_active_parts": 0,
            "stage_non_hot_active_parts": 0,
            "warm_cn_active_parts": 0,
        },
    )
    monkeypatch.setattr(
        audit_mod,
        "_read_target_manifest",
        lambda client: {"sha256": ACCEPTED_SCHEMA_MANIFEST_SHA256},
    )
    monkeypatch.setattr(
        audit_mod,
        "_verify_hot_us_headroom",
        lambda client: {"floor_satisfied": True, "minimum_free_ratio": 0.30},
    )

    result = audit_mod.audit_target_bulk_batch(
        master_plan=master,
        batch_manifest=manifest,
        state_dir=state_dir,
        client=object(),
    )

    assert result["audit_version"] == audit_mod.BATCH_FINAL_AUDIT_VERSION
    assert result["verified_sequences"] == [1, 2, 3, 4]
    assert result["verified_suffix_sequences"] == [3, 4]
    assert result["staging_cleanup_complete"] is True
    assert result["automatic_next_package"] is False

    missing = state_dir / f"bulk_{manifest['children'][1]['plan_sha256']}.journal.json"
    missing.unlink()
    with pytest.raises(RuntimeError, match="child journal is missing: 4"):
        audit_mod.audit_target_bulk_batch(
            master_plan=master,
            batch_manifest=manifest,
            state_dir=state_dir,
            client=object(),
        )


def test_final_audit_failure_blocks_host_task(monkeypatch) -> None:
    updates: list[dict] = []
    task = {
        "run_id": "00000000-0000-0000-0000-000000000340",
        "claimed_from_status": worker_v2.STATUS_RUN_QUEUED,
    }

    monkeypatch.setattr(worker_v2.v1, "_repo_root", lambda: ROOT)
    monkeypatch.setattr(
        worker_v2,
        "_run_execution",
        lambda task, repo_root: (_ for _ in ()).throw(
            RuntimeError("batch final audit failed: missing suffix journal")
        ),
    )
    monkeypatch.setattr(
        worker_v2,
        "update_target_bulk_task",
        lambda run_id, **kwargs: updates.append({"run_id": run_id, **kwargs}) or kwargs,
    )

    result = worker_v2.execute_claimed_target_bulk_task(task)

    assert result["status"] == worker_v2.STATUS_BLOCKED
    assert updates[-1]["status"] == worker_v2.STATUS_BLOCKED
    assert updates[-1]["finish"] is True


def test_production_launcher_uses_v2_and_success_is_after_final_audit() -> None:
    launcher = (ROOT / "scripts" / "run-us-application-target-bulk-host-worker.ps1").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "app" / "us" / "target_bulk_host_worker_v2.py").read_text(
        encoding="utf-8"
    )

    assert "app.us.target_bulk_host_worker_v2" in launcher
    assert "US_APPLICATION_TARGET_BULK_HOST_WORKER_V2" in launcher
    assert "success_requires_master_batch_final_audit=True" in launcher
    assert "audit_target_bulk_batch(" in source
    assert source.index('"phase": "FINAL_AUDIT"') < source.index("status=STATUS_SUCCESS")
    assert "batch final audit failed" in source
    assert "completed-sequence checkpoint is not the contiguous approved prefix" in source
