from pathlib import Path
from types import SimpleNamespace

import app.cn.guarded_run_once as guard_module
from app.cn.guarded_run_once import incoming_policy_issues


def _registered(file_name: str, dimension: str, value: str) -> dict[str, str]:
    return {
        "file_name": file_name,
        "partition_dimension": dimension,
        "partition_value": value,
    }


def test_incoming_policy_accepts_known_zip_partition(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "2023_1.zip").write_bytes(b"monthly")
    issues = incoming_policy_issues(incoming, registered_partitions=[])
    assert issues == []


def test_incoming_policy_rejects_unknown_precedence(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "mystery.zip").write_bytes(b"unknown")
    issues = incoming_policy_issues(incoming, registered_partitions=[])
    assert [issue["type"] for issue in issues] == ["UNKNOWN_PACKAGE_PRECEDENCE"]


def test_incoming_policy_rejects_non_zip_cn_source(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "1999.xml").write_text("<xml />", encoding="utf-8")
    issues = incoming_policy_issues(incoming, registered_partitions=[])
    assert [issue["type"] for issue in issues] == ["UNSUPPORTED_M16_CN_SOURCE_SUFFIX"]


def test_incoming_policy_rejects_two_files_for_same_partition(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "2023_1.zip").write_bytes(b"a")
    (incoming / "2023-01.zip").write_bytes(b"b")
    issues = incoming_policy_issues(incoming, registered_partitions=[])
    assert any(issue["type"] == "AMBIGUOUS_INCOMING_PARTITION" for issue in issues)


def test_registered_same_file_for_partition_is_allowed(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "2023_1.zip").write_bytes(b"monthly")
    issues = incoming_policy_issues(
        incoming,
        registered_partitions=[_registered("2023_1.zip", "UPDATE_MONTH", "2023-01")],
    )
    assert issues == []


def test_new_filename_for_registered_partition_is_rejected(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "2023-01.zip").write_bytes(b"revision")
    issues = incoming_policy_issues(
        incoming,
        registered_partitions=[_registered("2023_1.zip", "UPDATE_MONTH", "2023-01")],
    )
    assert any(issue["type"] == "NEW_FILE_FOR_REGISTERED_PARTITION" for issue in issues)


def test_clean_first_run_requires_preflight_and_replay_plan(tmp_path: Path, monkeypatch) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "1999.zip").write_bytes(b"base")

    monkeypatch.setattr(guard_module, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path))
    monkeypatch.setattr(guard_module, "_registered_partitions", lambda: [])
    monkeypatch.setattr(guard_module, "engine_version", lambda: "M1.6")
    monkeypatch.setattr(
        guard_module,
        "build_preflight",
        lambda: {
            "status": "PASS_WITH_WARNINGS",
            "mode": "CLEAN_RESET_READY_FOR_REPLAY",
            "safe_to_run_replay_command": True,
            "warning_reasons": ["clean_registry_waiting_for_replay"],
        },
    )
    monkeypatch.setattr(guard_module, "collect_incoming_packages", lambda _root: [object()])
    monkeypatch.setattr(
        guard_module,
        "evaluate_replay_plan",
        lambda _packages, preflight: {
            "status": "PASS",
            "package_count": 1,
            "warning_reasons": [],
            "expected_processing_order": [{"file_name": "1999.zip"}],
        },
    )

    guard = guard_module.build_execution_guard()
    assert guard["allowed"] is True
    assert guard["mode"] == "CLEAN_RESET_FIRST_RUN"
    assert guard["preflight"]["status"] == "PASS_WITH_WARNINGS"
    assert guard["replay_plan"]["status"] == "PASS"


def test_registered_continuation_checks_m16_replay_boundary(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "incoming" / "cn").mkdir(parents=True)
    called: list[str] = []
    monkeypatch.setattr(guard_module, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path))
    monkeypatch.setattr(
        guard_module,
        "_registered_partitions",
        lambda: [_registered("1999.zip", "FILING_YEAR", "1999")],
    )
    monkeypatch.setattr(guard_module, "_retry_required_packages", lambda: [])
    monkeypatch.setattr(guard_module, "engine_version", lambda: "M1.6")
    monkeypatch.setattr(
        guard_module,
        "ensure_m16_goods_schema",
        lambda: called.append("schema"),
    )
    monkeypatch.setattr(
        guard_module,
        "ensure_m16_goods_replay_boundary",
        lambda: called.append("boundary"),
    )

    guard = guard_module.build_execution_guard()
    assert guard["allowed"] is True
    assert guard["mode"] == "REGISTERED_REPLAY_CONTINUATION"
    assert called == ["schema", "boundary"]


def test_failed_package_blocks_normal_replay_until_retry(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "incoming" / "cn").mkdir(parents=True)
    monkeypatch.setattr(guard_module, "get_settings", lambda: SimpleNamespace(raw_data_root=tmp_path))
    monkeypatch.setattr(
        guard_module,
        "_registered_partitions",
        lambda: [
            _registered("1999.zip", "FILING_YEAR", "1999"),
            _registered("2000.zip", "FILING_YEAR", "2000"),
        ],
    )
    monkeypatch.setattr(
        guard_module,
        "_retry_required_packages",
        lambda: [
            {
                "package_id": "pkg-1999",
                "file_name": "1999.zip",
                "status": "FAILED",
                "source_rank": 1,
                "error_message": "fixture failure",
            }
        ],
    )
    monkeypatch.setattr(guard_module, "engine_version", lambda: "M1.6")

    guard = guard_module.build_execution_guard()
    assert guard["allowed"] is False
    assert guard["mode"] == "RETRY_REQUIRED"
    assert guard["issues"][0]["type"] == "FAILED_PACKAGE_MUST_BE_RETRIED_BEFORE_ADVANCE"
    assert "retry-cn.ps1" in guard["issues"][0]["instruction"]


def test_run_cn_uses_guarded_one_shot_entrypoint() -> None:
    script = Path("scripts/run-cn.ps1").read_text(encoding="utf-8")
    assert "python -m app.cn.guarded_run_once" in script
    assert "python -m app.cn.run_once" not in script
    assert "persistent worker is running" in script


def test_persistent_worker_cannot_bootstrap_clean_replay() -> None:
    worker = Path("app/worker.py").read_text(encoding="utf-8")
    assert "build_execution_guard()" in worker
    assert 'guard.get("mode") == "CLEAN_RESET_FIRST_RUN"' in worker
    assert "persistent worker will not bootstrap it automatically" in worker
    assert 'trigger_type="SCHEDULED_GUARDED"' in worker
