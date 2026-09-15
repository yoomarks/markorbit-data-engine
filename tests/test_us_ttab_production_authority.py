from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.us_ttab import corpus_replay
from app.us_ttab import production_authority as authority


MAIN = "a" * 40
MANIFEST_SHA = "c" * 64
SOURCE_SHA = "d" * 64


def _entry(name: str = "snapshot.zip") -> dict[str, object]:
    return {
        "manifest_path": f"incoming/us_ttab/{name}",
        "file_name": name,
        "source_kind": "TTAB_BULK_HISTORICAL_XML",
        "snapshot_at": "2026-09-03T21:00:00.000Z",
        "sha256": SOURCE_SHA,
        "size_bytes": 123,
        "xml_members": [name.replace(".zip", ".xml")],
    }


def _transition(package_count: int = 0) -> dict[str, object]:
    successful = max(package_count - 1, 0)
    ttab_packages = [
        {"package_id": f"p{index + 1}", "status": "SUCCESS" if index < successful else "FAILED"}
        for index in range(package_count)
    ]
    return {
        "status": "TTAB_PHASE_UNLOCKED",
        "assignment_gate_status": "ASSIGNMENT_ACCEPTED",
        "assignment_gate": {
            "status": "ASSIGNMENT_ACCEPTED",
            "assignment_ready": True,
            "assignment_readiness": {
                "acceptance": {
                    "status": "PASS_WITH_WARNINGS",
                    "package_count": 156,
                    "successful_package_count": 156,
                    "source_verification": {
                        "checked_count": 156,
                        "missing_count": 0,
                        "mismatch_count": 0,
                    },
                }
            },
        },
        "ttab_state": "SOURCE_NOT_REGISTERED" if package_count == 0 else "NOT_READY",
        "ttab_ready": False,
        "ttab_readiness": {
            "state": "SOURCE_NOT_REGISTERED" if package_count == 0 else "NOT_READY",
            "acceptance": {
                "status": "NOT_READY",
                "schema": {"ready": package_count > 0},
                "package_count": package_count,
                "packages": ttab_packages,
                "tables": {
                    "us_ttab_proceeding_history": {
                        "row_count": 100 if package_count else 0,
                        "source_package_count": successful,
                    }
                },
            },
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
    transition = _transition(len(packages))
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
            "expected_historical_packages": 5,
            "expected_daily_packages": 254,
            "historical_snapshot_at": "2026-09-03T21:00:00.000Z",
            "daily_through": "2026-09-11",
        },
        "replay": {
            "status": replay_status,
            "remaining_count": 258 if resume else 259,
            "next_action": action,
        },
        "transition": transition,
        "assignment_baseline": authority._assignment_gate_baseline(transition),
        "baseline": authority._ttab_baseline(transition),
        "entries": [_entry()],
        "incoming": 0 if resume else 1,
        "archive": 1 if resume else 0,
        "packages": packages,
        "remaining": 258 if resume else 259,
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
        lambda manifest_path, control_root: {
            "storage_root": "CONTROL_ROOT",
            "relative_path": "us_ttab/corpus_manifest.json",
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
        control_root=tmp_path,
        manifest_path=tmp_path / "corpus.json",
        expected_history_parts=91,
        authority_generation_id="12345678-1234-4234-8234-123456789abc",
    )
    assert plan["start_mode"] == "INITIAL_EMPTY_START"
    assert plan["replay_status"] == "READY"
    assert plan["remaining_count"] == 259
    assert plan["resume_failed"] is False
    assert plan["expected_registry_count"] == 0
    assert plan["assignment_gate_baseline"]["package_count"] == 156
    assert plan["plan_sha256"] == authority.canonical_plan_sha(plan)
    assert plan["required_authority_token"] == (
        f"GO #662 US TTAB production replay {plan['plan_sha256']}"
    )


def test_build_authority_plan_freezes_resume_bound_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_prepare(monkeypatch, _state(resume=True))
    plan = authority.build_authority_plan(
        repo_root=tmp_path,
        raw_root=tmp_path,
        control_root=tmp_path,
        manifest_path=tmp_path / "corpus.json",
        expected_history_parts=91,
        authority_generation_id="12345678-1234-4234-8234-123456789abc",
    )
    assert plan["start_mode"] == "RESUME_BOUND_STATE"
    assert plan["replay_status"] == "RETRY_REQUIRED"
    assert plan["resume_failed"] is True
    assert plan["expected_registry_count"] == 2
    assert plan["expected_successful_registry_count"] == 1
    assert plan["remaining_count"] == 258
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
        f"GO #662 US TTAB production replay {digest}"
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
            control_root=tmp_path,
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
            "source_count": 259,
            "remaining_count": 259,
            "resume_failed": False,
            "assignment_package_count": 156,
        },
    )

    def fail_connection():
        raise AssertionError("database must not be opened for a wrong GO token")

    with pytest.raises(PermissionError, match="exact US TTAB"):
        authority.consume_authority_plan(
            plan,
            raw_root=tmp_path,
            control_root=tmp_path,
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
        return {"remaining_count": 259}

    monkeypatch.setattr(authority, "validate_active_receipt", fake_active)
    monkeypatch.setattr(authority, "validate_authority_plan", fake_validate)
    result = authority.validate_runtime_start(
        plan, receipt, raw_root=tmp_path, control_root=tmp_path
    )

    assert result["run_id"] == "run-1"
    assert result["remaining_count"] == 259
    _, kwargs = seen["validate"]
    assert kwargs["expected_main"] == MAIN
    assert kwargs["reject_consumed"] is False
    assert kwargs["allow_active_plan_sha"] == "f" * 64


def test_manifest_evidence_binds_control_root(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    manifest = control_root / "us_ttab" / "corpus_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"version":"test"}\n', encoding="utf-8")
    evidence = authority._manifest_evidence(manifest, control_root)
    assert evidence["storage_root"] == "CONTROL_ROOT"
    assert evidence["relative_path"] == "us_ttab/corpus_manifest.json"
    assert evidence["size_bytes"] == manifest.stat().st_size
    assert evidence["sha256"] == authority.sha256_file(manifest)


def test_manifest_evidence_rejects_escape(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    control_root.mkdir()
    manifest = tmp_path / "outside.json"
    manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="CONTROL_ROOT"):
        authority._manifest_evidence(manifest, control_root)


def test_replay_cli_requires_authority_control_root(
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
        [
            "corpus-replay", "--manifest", str(tmp_path / "corpus.json"),
            "--apply", "--all",
            "--authority-plan", str(tmp_path / "plan.json"),
            "--authority-receipt", str(tmp_path / "receipt.json"),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        corpus_replay.main()
    assert exc.value.code == 2


def test_validate_authority_plan_accepts_zero_baseline_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state()
    monkeypatch.setattr(authority, "_remote_main", lambda root: MAIN)
    monkeypatch.setattr(authority, "_authority_rows", lambda plan_sha256=None: [])
    monkeypatch.setattr(authority, "_start_state", lambda *args, **kwargs: state)

    def fake_git(args: list[str], root: Path, timeout: int = 45) -> str:
        del root, timeout
        return "" if args[:2] == ["status", "--porcelain"] else MAIN

    monkeypatch.setattr(authority, "_git", fake_git)
    control_root = tmp_path / "control"
    manifest = control_root / "us_ttab" / "corpus_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    plan = authority.build_authority_plan(
        repo_root=tmp_path, raw_root=tmp_path, control_root=control_root,
        manifest_path=manifest, expected_history_parts=91,
        authority_generation_id="12345678-1234-4234-8234-123456789abc",
    )
    assert plan["expected_registry_count"] == 0
    assert plan["expected_successful_registry_count"] == 0
    assert plan["expected_archive_source_count"] == 0
    result = authority.validate_authority_plan(
        plan,
        raw_root=tmp_path,
        control_root=control_root,
        expected_main=MAIN,
    )
    assert result["source_count"] == 1
    assert result["remaining_count"] == 259


def test_build_authority_plan_rejects_stale_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state()
    _patch_prepare(monkeypatch, state)
    monkeypatch.setattr(authority, "_remote_main", lambda root: "b" * 40)
    with pytest.raises(RuntimeError, match="not live origin/main"):
        authority.build_authority_plan(
            repo_root=tmp_path,
            raw_root=tmp_path,
            control_root=tmp_path,
            manifest_path=tmp_path / "corpus.json",
            expected_history_parts=91,
        )


def test_validate_authority_plan_rejects_manifest_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state()
    control_root = tmp_path / "control"
    manifest = control_root / "us_ttab" / "corpus.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    _patch_prepare(monkeypatch, state)
    monkeypatch.setattr(
        authority,
        "_manifest_evidence",
        lambda manifest_path, control_root: {
            "storage_root": "CONTROL_ROOT",
            "relative_path": "us_ttab/corpus.json",
            "size_bytes": manifest.stat().st_size,
            "sha256": authority.sha256_file(manifest),
        },
    )
    plan = authority.build_authority_plan(
        repo_root=tmp_path,
        raw_root=tmp_path,
        control_root=control_root,
        manifest_path=manifest,
        expected_history_parts=91,
        authority_generation_id="12345678-1234-4234-8234-123456789abc",
    )
    manifest.write_text('{"drift":true}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest size drifted|manifest SHA drifted"):
        authority.validate_authority_plan(
            plan,
            raw_root=tmp_path,
            control_root=control_root,
            expected_main=MAIN,
        )


def test_validate_authority_plan_rejects_source_and_registry_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state()
    control_root = tmp_path / "control"
    manifest = control_root / "us_ttab" / "corpus.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    _patch_prepare(monkeypatch, state)
    monkeypatch.setattr(
        authority,
        "_manifest_evidence",
        lambda manifest_path, control_root: {
            "storage_root": "CONTROL_ROOT",
            "relative_path": "us_ttab/corpus.json",
            "size_bytes": manifest.stat().st_size,
            "sha256": authority.sha256_file(manifest),
        },
    )
    plan = authority.build_authority_plan(
        repo_root=tmp_path,
        raw_root=tmp_path,
        control_root=control_root,
        manifest_path=manifest,
        expected_history_parts=91,
        authority_generation_id="12345678-1234-4234-8234-123456789abc",
    )
    drifted = dict(state)
    drifted["entries"] = [dict(_entry(), sha256="e" * 64)]
    monkeypatch.setattr(authority, "_start_state", lambda *args, **kwargs: drifted)
    with pytest.raises(RuntimeError, match="source corpus identity drifted"):
        authority.validate_authority_plan(
            plan,
            raw_root=tmp_path,
            control_root=control_root,
            expected_main=MAIN,
        )

    registry_drift = dict(state)
    registry_drift["packages"] = [{"package_id": "new", "status": "REGISTERED"}]
    monkeypatch.setattr(authority, "_start_state", lambda *args, **kwargs: registry_drift)
    with pytest.raises(RuntimeError, match="registry count drifted"):
        authority.validate_authority_plan(
            plan,
            raw_root=tmp_path,
            control_root=control_root,
            expected_main=MAIN,
        )


def test_replay_cli_rejects_wrong_bound_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control_root = tmp_path / "control"
    control_root.mkdir()
    bound = control_root / "bound.json"
    wrong = control_root / "wrong.json"
    bound.write_text("{}", encoding="utf-8")
    wrong.write_text("{}", encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    plan_path.write_text(
        '{"manifest":{"relative_path":"bound.json"},"execution_main":"' + MAIN + '"}',
        encoding="utf-8",
    )
    receipt_path.write_text('{"run_id":"run-1"}', encoding="utf-8")
    monkeypatch.setattr(
        corpus_replay,
        "get_settings",
        lambda: SimpleNamespace(raw_data_root=tmp_path),
    )
    monkeypatch.setattr(
        authority,
        "validate_runtime_start",
        lambda *args, **kwargs: {"resume_failed": False},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "corpus-replay",
            "--manifest",
            str(wrong),
            "--apply",
            "--all",
            "--authority-plan",
            str(plan_path),
            "--authority-receipt",
            str(receipt_path),
            "--authority-control-root",
            str(control_root),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        corpus_replay.main()
    assert exc.value.code == 2


def test_consume_rejects_duplicate_plan_at_commit_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = {
        "required_authority_token": "GO exact",
        "plan_sha256": "e" * 64,
        "authority_generation_id": "12345678-1234-4234-8234-123456789abc",
        "execution_main": MAIN,
        "manifest": {"sha256": MANIFEST_SHA},
        "source_plan_sha256": SOURCE_SHA,
        "expected_source_count": 259,
        "expected_registry_count": 0,
        "remaining_count": 259,
        "resume_failed": False,
        "production_mutation_scope": "TTAB_SCHEMA_INIT_REGISTER_INGEST_AND_SOURCE_ARCHIVE",
        "assignment_gate_baseline": {"package_count": 156},
    }
    monkeypatch.setattr(
        authority,
        "validate_authority_plan",
        lambda *args, **kwargs: {
            "plan_sha256": "e" * 64,
            "source_count": 259,
            "remaining_count": 259,
            "resume_failed": False,
            "assignment_package_count": 156,
        },
    )

    class Cursor:
        def __init__(self) -> None:
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            if "payload->>'plan_sha256'" in sql:
                self.row = {"run_id": "existing"}
            else:
                self.row = None

        def fetchone(self):
            return self.row

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            raise AssertionError("duplicate consume must not commit")

    with pytest.raises(RuntimeError, match="consumed concurrently"):
        authority.consume_authority_plan(
            plan,
            raw_root=tmp_path,
            control_root=tmp_path,
            expected_main=MAIN,
            authority_token="GO exact",
            connection_factory=Conn,
        )


def test_validate_active_receipt_requires_running_bound_row() -> None:
    plan = {"version": authority.AUTHORITY_VERSION, "execution_main": MAIN}
    digest = authority.canonical_plan_sha(plan)
    plan["plan_sha256"] = digest
    receipt = {
        "version": authority.AUTHORITY_RECEIPT_VERSION,
        "plan_sha256": digest,
        "run_id": "run-1",
    }

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return {
                "status": "RUNNING",
                "payload": {"plan_sha256": digest, "resume_failed": False},
            }

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

    result = authority.validate_active_receipt(plan, receipt, connection_factory=Conn)
    assert result == {
        "run_id": "run-1",
        "plan_sha256": digest,
        "status": "RUNNING",
        "resume_failed": False,
    }


def test_finalize_authority_marks_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {"plan_sha256": "e" * 64}
    receipt = {"run_id": "run-1"}
    monkeypatch.setattr(
        authority,
        "validate_active_receipt",
        lambda *args, **kwargs: {
            "run_id": "run-1",
            "plan_sha256": "e" * 64,
            "status": "RUNNING",
            "resume_failed": False,
        },
    )
    committed = {"value": False}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            assert "UPDATE control.job_run" in sql

        def fetchone(self):
            return {"run_id": "run-1"}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            committed["value"] = True

    result = authority.finalize_authority(
        plan,
        receipt,
        success=True,
        report_path="report.json",
        connection_factory=Conn,
    )
    assert result["status"] == "SUCCESS"
    assert committed["value"] is True
