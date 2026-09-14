from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.us_assignment import corpus_replay
from app.us_assignment import production_authority as authority


MAIN = "a" * 40
APP_PLAN = "b" * 64
MANIFEST_SHA = "c" * 64
SOURCE_SHA = "d" * 64


def _entry(name: str = "snapshot.zip") -> dict[str, object]:
    return {
        "manifest_path": f"incoming/us_assignment/{name}",
        "file_name": name,
        "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
        "effective_date": "2026-04-09",
        "sha256": SOURCE_SHA,
        "size_bytes": 123,
        "xml_members": [name.replace(".zip", ".xml")],
    }


def _transition(package_count: int = 0) -> dict[str, object]:
    return {
        "status": "ASSIGNMENT_PHASE_UNLOCKED",
        "application_gate": {
            "status": "US_APPLICATION_ALREADY_ACCEPTED",
            "us_pipeline": {"accepted_target_sequence_count": 343},
        },
        "assignment_readiness": {
            "acceptance": {
                "status": "NOT_READY",
                "schema": {"ready": package_count > 0},
                "package_count": package_count,
                "successful_package_count": max(package_count - 1, 0),
                "tables": {
                    "us_assignment_record_history": {
                        "row_count": 100 if package_count else 0,
                        "source_package_count": max(package_count - 1, 0),
                    }
                },
            }
        },
    }


def _state(*, resume: bool = False) -> dict[str, object]:
    packages = (
        [
            {"package_id": "p1", "status": "SUCCESS"},
            {"package_id": "p2", "status": "FAILED"},
        ]
        if resume
        else []
    )
    package_count = len(packages)
    transition = _transition(package_count)
    replay_status = "RETRY_REQUIRED" if resume else "READY"
    action = _entry("daily.zip" if resume else "snapshot.zip")
    action.update(
        {
            "action": "RETRY_FULL_PACKAGE" if resume else "REGISTER_AND_INGEST",
            "registry_status": "FAILED" if resume else "UNREGISTERED",
            "package_id": "p2" if resume else None,
        }
    )
    return {
        "preflight": {
            "expected_snapshot_packages": 1,
            "expected_daily_packages": 155,
            "daily_through": "2026-09-11",
        },
        "replay": {
            "status": replay_status,
            "remaining_count": 155 if resume else 156,
            "next_action": action,
        },
        "transition": transition,
        "epoch": {
            "run_id": "application-run",
            "plan_sha256": APP_PLAN,
            "checkpoint_sequence": 343,
        },
        "baseline": authority._assignment_baseline(transition),
        "entries": [_entry()],
        "incoming": 0 if resume else 1,
        "archive": 1 if resume else 0,
        "packages": packages,
        "remaining": 155 if resume else 156,
    }


def _patch_prepare(monkeypatch: pytest.MonkeyPatch, state: dict[str, object]) -> None:
    def fake_git(args: list[str], root: Path, timeout: int = 45) -> str:
        del root, timeout
        if args[:2] == ["status", "--porcelain"]:
            return ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return MAIN
        raise AssertionError(args)

    monkeypatch.setattr(authority, "_git", fake_git)
    monkeypatch.setattr(authority, "_remote_main", lambda root: MAIN)
    monkeypatch.setattr(authority, "_authority_rows", lambda plan_sha256=None: [])
    monkeypatch.setattr(
        authority,
        "_manifest_evidence",
        lambda manifest_path, raw_root: {
            "relative_path": "manifests/us_assignment/corpus.json",
            "size_bytes": 456,
            "sha256": MANIFEST_SHA,
        },
    )
    monkeypatch.setattr(authority, "_start_state", lambda *args, **kwargs: state)


def test_canonical_plan_sha_ignores_digest_and_token() -> None:
    plan = {"version": "v", "nested": {"x": 1}}
    digest = authority.canonical_plan_sha(plan)
    plan["plan_sha256"] = "not-part-of-canonical-body"
    plan["required_authority_token"] = "also-not-part"
    assert authority.canonical_plan_sha(plan) == digest


def test_build_authority_plan_freezes_initial_empty_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_prepare(monkeypatch, _state())
    plan = authority.build_authority_plan(
        repo_root=tmp_path,
        raw_root=tmp_path,
        manifest_path=tmp_path / "corpus.json",
        expected_history_parts=91,
        authority_generation_id="12345678-1234-4234-8234-123456789abc",
    )
    assert plan["start_mode"] == "INITIAL_EMPTY_START"
    assert plan["replay_status"] == "READY"
    assert plan["remaining_count"] == 156
    assert plan["resume_failed"] is False
    assert plan["expected_registry_count"] == 0
    assert plan["application_epoch"]["checkpoint_sequence"] == 343
    assert plan["plan_sha256"] == authority.canonical_plan_sha(plan)
    assert plan["required_authority_token"] == (
        f"GO #545 US Assignment production replay {plan['plan_sha256']}"
    )


def test_build_authority_plan_freezes_resume_bound_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_prepare(monkeypatch, _state(resume=True))
    plan = authority.build_authority_plan(
        repo_root=tmp_path,
        raw_root=tmp_path,
        manifest_path=tmp_path / "corpus.json",
        expected_history_parts=91,
        authority_generation_id="12345678-1234-4234-8234-123456789abc",
    )
    assert plan["start_mode"] == "RESUME_BOUND_STATE"
    assert plan["replay_status"] == "RETRY_REQUIRED"
    assert plan["resume_failed"] is True
    assert plan["expected_registry_count"] == 2
    assert plan["expected_successful_registry_count"] == 1
    assert plan["remaining_count"] == 155
    assert plan["next_action"]["action"] == "RETRY_FULL_PACKAGE"
    assert plan["next_action"]["package_id"] == "p2"


def test_validate_authority_plan_rejects_consumed_before_source_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = {
        "version": authority.AUTHORITY_VERSION,
        "execution_main": MAIN,
    }
    digest = authority.canonical_plan_sha(plan)
    plan["plan_sha256"] = digest
    plan["required_authority_token"] = (
        f"GO #545 US Assignment production replay {digest}"
    )
    monkeypatch.setattr(
        authority,
        "_authority_rows",
        lambda plan_sha256=None: [{"run_id": "used"}] if plan_sha256 else [],
    )
    with pytest.raises(RuntimeError, match="already consumed"):
        authority.validate_authority_plan(
            plan,
            raw_root=tmp_path,
            expected_main=MAIN,
        )


def test_consume_requires_exact_token_before_database_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = {
        "required_authority_token": "GO exact",
        "plan_sha256": "e" * 64,
    }
    monkeypatch.setattr(
        authority,
        "validate_authority_plan",
        lambda *args, **kwargs: {
            "plan_sha256": "e" * 64,
            "source_count": 156,
            "remaining_count": 156,
            "resume_failed": False,
            "application_checkpoint": 343,
        },
    )

    def fail_connection():
        raise AssertionError("database must not be opened for a wrong GO token")

    with pytest.raises(PermissionError, match="exact US Assignment"):
        authority.consume_authority_plan(
            plan,
            raw_root=tmp_path,
            expected_main=MAIN,
            authority_token="GO wrong",
            connection_factory=fail_connection,
        )


def test_replay_cli_rejects_apply_without_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        corpus_replay,
        "get_settings",
        lambda: SimpleNamespace(raw_data_root=tmp_path),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["corpus-replay", "--manifest", str(tmp_path / "corpus.json"), "--apply", "--all"],
    )
    with pytest.raises(SystemExit) as exc:
        corpus_replay.main()
    assert exc.value.code == 2


def test_validate_runtime_start_revalidates_frozen_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = {"execution_main": MAIN, "plan_sha256": "f" * 64}
    receipt = {"run_id": "run-1"}
    seen: dict[str, object] = {}

    def fake_active(*args, **kwargs):
        seen["active"] = (args, kwargs)
        return {"run_id": "run-1", "resume_failed": False}

    def fake_validate(*args, **kwargs):
        seen["validate"] = (args, kwargs)
        return {"remaining_count": 156}

    monkeypatch.setattr(authority, "validate_active_receipt", fake_active)
    monkeypatch.setattr(authority, "validate_authority_plan", fake_validate)
    result = authority.validate_runtime_start(plan, receipt, raw_root=tmp_path)

    assert result["run_id"] == "run-1"
    assert result["remaining_count"] == 156
    _, kwargs = seen["validate"]
    assert kwargs["expected_main"] == MAIN
    assert kwargs["reject_consumed"] is False
    assert kwargs["allow_active_plan_sha"] == "f" * 64
