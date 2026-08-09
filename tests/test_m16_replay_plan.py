from pathlib import Path

from app.cn.package_meta import infer_package_descriptor
from app.cn.replay_plan import PlannedPackage, collect_incoming_packages, evaluate_replay_plan


def _preflight(**overrides):
    result = {
        "status": "PASS_WITH_WARNINGS",
        "mode": "CLEAN_RESET_READY_FOR_REPLAY",
        "safe_to_run_replay_command": True,
        "preflight_version": "PREFLIGHT_V1",
    }
    result.update(overrides)
    return result


def _planned(name: str, *, sha: str, registration_order: int) -> PlannedPackage:
    descriptor = infer_package_descriptor(name)
    return PlannedPackage(
        path=Path(name),
        descriptor=descriptor,
        file_size=100,
        sha256=sha,
        registration_order=registration_order,
        hypothetical_source_rank=descriptor.source_rank(registration_order),
    )


def test_clean_replay_plan_matches_registration_and_semantic_processing_order() -> None:
    packages = [
        _planned("1999.zip", sha="a" * 64, registration_order=1),
        _planned("2000.zip", sha="b" * 64, registration_order=2),
        _planned("2023_1.zip", sha="c" * 64, registration_order=3),
    ]
    result = evaluate_replay_plan(packages, preflight=_preflight())
    assert result["status"] == "PASS"
    assert [row["file_name"] for row in result["scanner_registration_order"]] == [
        "1999.zip",
        "2000.zip",
        "2023_1.zip",
    ]
    assert [row["file_name"] for row in result["expected_processing_order"]] == [
        "1999.zip",
        "2000.zip",
        "2023_1.zip",
    ]


def test_monthly_processing_order_is_date_semantic_not_lexical() -> None:
    packages = [
        _planned("1999.zip", sha="a" * 64, registration_order=1),
        _planned("2023_10.zip", sha="b" * 64, registration_order=2),
        _planned("2023_2.zip", sha="c" * 64, registration_order=3),
    ]
    result = evaluate_replay_plan(packages, preflight=_preflight())
    assert [row["file_name"] for row in result["scanner_registration_order"]] == [
        "1999.zip",
        "2023_10.zip",
        "2023_2.zip",
    ]
    assert [row["file_name"] for row in result["expected_processing_order"]] == [
        "1999.zip",
        "2023_2.zip",
        "2023_10.zip",
    ]


def test_unknown_package_precedence_is_rejected() -> None:
    result = evaluate_replay_plan(
        [_planned("mystery.zip", sha="a" * 64, registration_order=1)],
        preflight=_preflight(),
    )
    assert result["status"] == "FAIL"
    assert "unknown_package_precedence" in result["hard_fail_reasons"]


def test_different_files_for_same_month_partition_are_rejected() -> None:
    result = evaluate_replay_plan(
        [
            _planned("2023_1.zip", sha="a" * 64, registration_order=1),
            _planned("2023-01.zip", sha="b" * 64, registration_order=2),
        ],
        preflight=_preflight(),
    )
    assert result["status"] == "FAIL"
    assert "ambiguous_partition_revision" in result["hard_fail_reasons"]
    assert "UPDATE_MONTH:2023-01" in result["ambiguous_partitions"]


def test_duplicate_content_under_different_names_is_rejected() -> None:
    result = evaluate_replay_plan(
        [
            _planned("1999.zip", sha="a" * 64, registration_order=1),
            _planned("2000.zip", sha="a" * 64, registration_order=2),
        ],
        preflight=_preflight(),
    )
    assert result["status"] == "FAIL"
    assert "duplicate_incoming_package_content" in result["hard_fail_reasons"]


def test_plan_requires_clean_reset_preflight_mode() -> None:
    result = evaluate_replay_plan(
        [_planned("1999.zip", sha="a" * 64, registration_order=1)],
        preflight=_preflight(mode="PARTIAL_OR_PENDING_REPLAY"),
    )
    assert result["status"] == "FAIL"
    assert "replay_plan_requires_clean_reset_mode" in result["hard_fail_reasons"]


def test_plan_warns_when_clean_replay_has_no_monthly_patch() -> None:
    result = evaluate_replay_plan(
        [_planned("1999.zip", sha="a" * 64, registration_order=1)],
        preflight=_preflight(),
    )
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert "no_monthly_patch_in_clean_replay_plan" in result["warning_reasons"]


def test_collect_incoming_packages_hashes_and_uses_scanner_lexical_order(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "cn"
    incoming.mkdir(parents=True)
    (incoming / "2023_2.zip").write_bytes(b"feb")
    (incoming / "1999.zip").write_bytes(b"base")
    (incoming / "2023_10.zip").write_bytes(b"oct")

    packages = collect_incoming_packages(tmp_path)
    assert [package.path.name for package in packages] == [
        "1999.zip",
        "2023_10.zip",
        "2023_2.zip",
    ]
    assert [package.registration_order for package in packages] == [1, 2, 3]
    assert len({package.sha256 for package in packages}) == 3
