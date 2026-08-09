from pathlib import Path

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


def test_run_cn_uses_guarded_one_shot_entrypoint() -> None:
    script = Path("scripts/run-cn.ps1").read_text(encoding="utf-8")
    assert "python -m app.cn.guarded_run_once" in script
    assert "python -m app.cn.run_once" not in script
    assert "persistent worker is running" in script
