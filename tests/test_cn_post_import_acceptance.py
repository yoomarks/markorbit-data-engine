from app.cn.post_import_acceptance import (
    build_post_import_acceptance,
    evaluate_post_import_acceptance,
)


def package(status: str = "SUCCESS") -> dict:
    return {
        "package_id": "pkg-2023-4",
        "file_name": "2023_4.zip",
        "package_kind": "MONTHLY_PATCH",
        "partition_value": "2023-04",
        "source_rank": 202304,
        "status": status,
        "processed_at": "2026-08-24T00:00:00+00:00",
        "error_message": None,
    }


def readiness(status: str, *, hard_issues=None) -> dict:
    return {
        "status": status,
        "hard_issues": list(hard_issues or []),
        "retry_issues": [],
        "packages": {},
        "storage_v2": {},
    }


def test_successful_2023_4_routes_to_normal_continuation_when_more_packages_remain():
    report = evaluate_post_import_acceptance(
        expected_file_name="2023_4.zip",
        package_rows=[package()],
        readiness=readiness("READY"),
        final_checkpoint=None,
    )

    assert report["status"] == "READY_TO_CONTINUE"
    assert report["expected_package_success"] is True
    assert report["readiness_status"] == "READY"
    assert report["final_checkpoint_executed"] is False
    assert report["next_action"] == {
        "mode": "NORMAL",
        "command": (
            "powershell.exe -ExecutionPolicy Bypass "
            "-File .\\scripts\\replay-cn-full.ps1"
        ),
    }


def test_successful_2023_4_routes_to_explicit_failed_resume_when_retry_barrier_exists():
    report = evaluate_post_import_acceptance(
        expected_file_name="2023_4.zip",
        package_rows=[package()],
        readiness=readiness("RETRY_REQUIRED"),
        final_checkpoint=None,
    )

    assert report["status"] == "RETRY_REQUIRED"
    assert report["next_action"]["mode"] == "RESUME_FAILED"
    assert report["next_action"]["command"].endswith("-ResumeFailed")


def test_missing_or_non_success_expected_package_blocks_even_if_replay_looks_complete():
    missing = evaluate_post_import_acceptance(
        expected_file_name="2023_4.zip",
        package_rows=[],
        readiness=readiness("COMPLETE"),
        final_checkpoint={"status": "PASS", "reasons": []},
    )
    failed = evaluate_post_import_acceptance(
        expected_file_name="2023_4.zip",
        package_rows=[package("FAILED")],
        readiness=readiness("COMPLETE"),
        final_checkpoint={"status": "PASS", "reasons": []},
    )

    assert missing["status"] == "BLOCKED"
    assert missing["reasons"][0]["code"] == "EXPECTED_PACKAGE_NOT_REGISTERED"
    assert failed["status"] == "BLOCKED"
    assert failed["reasons"][0]["code"] == "EXPECTED_PACKAGE_NOT_SUCCESS"


def test_readiness_hard_issue_blocks_post_import_continuation():
    report = evaluate_post_import_acceptance(
        expected_file_name="2023_4.zip",
        package_rows=[package()],
        readiness=readiness(
            "BLOCKED",
            hard_issues=[{"code": "PERSISTENT_WORKER_RUNNING"}],
        ),
        final_checkpoint=None,
    )

    assert report["status"] == "BLOCKED"
    assert report["reasons"] == [{"code": "PERSISTENT_WORKER_RUNNING"}]
    assert report["next_action"]["mode"] == "STOP_AND_REVIEW"


def test_complete_replay_reuses_readiness_and_runs_final_checkpoint_once():
    calls = {"readiness": 0, "checkpoint": 0}
    ready_report = readiness("COMPLETE")

    def package_rows_builder(file_name: str):
        assert file_name == "2023_4.zip"
        return [package()]

    def readiness_builder(*, persistent_worker_running: bool):
        calls["readiness"] += 1
        assert persistent_worker_running is False
        return ready_report

    def final_checkpoint_builder(*, persistent_worker_running: bool, readiness_builder):
        calls["checkpoint"] += 1
        assert persistent_worker_running is False
        assert readiness_builder(persistent_worker_running=False) is ready_report
        return {"status": "PASS", "reasons": [], "ready_for_next_domain": True}

    report = build_post_import_acceptance(
        expected_file_name="2023_4.zip",
        package_rows_builder=package_rows_builder,
        readiness_builder=readiness_builder,
        final_checkpoint_builder=final_checkpoint_builder,
    )

    assert calls == {"readiness": 1, "checkpoint": 1}
    assert report["status"] == "PASS"
    assert report["final_checkpoint_executed"] is True
    assert report["next_action"] == {"mode": "CN_REPLAY_ACCEPTED", "command": None}


def test_final_checkpoint_is_not_run_before_expected_package_success():
    def fail_if_called(**kwargs):
        raise AssertionError("final checkpoint must not execute")

    report = build_post_import_acceptance(
        expected_file_name="2023_4.zip",
        package_rows_builder=lambda _: [package("FAILED")],
        readiness_builder=lambda **_: readiness("COMPLETE"),
        final_checkpoint_builder=fail_if_called,
    )

    assert report["status"] == "BLOCKED"
    assert report["final_checkpoint_executed"] is False
