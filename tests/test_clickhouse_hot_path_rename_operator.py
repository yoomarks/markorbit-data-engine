from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "rename-clickhouse-hot-path.ps1"


def test_hot_path_rename_operator_is_path_only_and_fail_closed() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert '"E:\\MarkOrbitData\\hot\\clickhouse-cs"' in text
    assert '"E:\\MarkOrbitData\\hot\\clickhouse"' in text
    assert "GetFileInformationByName" in text
    assert "FILE_CASE_SENSITIVE_INFORMATION" in text
    assert "check-cn-serving-state.ps1" in text
    assert "profile-cn-hot-warm-capacity.ps1" in text
    assert "Rename-Item" in text
    assert "docker stop" in lowered
    assert "docker compose @compose create --no-deps clickhouse" in lowered
    assert "HOT_PATH_RENAME_OK" in text

    # The operator may replace the stopped container shell, but it must never
    # delete/copy/mutate the ClickHouse data directory or start unrelated services.
    forbidden = (
        "remove-item -literalpath $oldhotpath",
        "remove-item -literalpath $newhotpath",
        "copy-item",
        "robocopy",
        "docker compose up",
        "docker system prune",
        "optimize final",
        "alter table",
        "truncate table",
        "drop table",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_hot_path_rename_operator_requires_same_parent_and_row_equivalence() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    assert "Old/New Hot paths must share the same parent" in text
    assert "Total active rows changed across path-only rename" in text
    assert "Active table count changed across path-only rename" in text
    assert "Active rows changed for table" in text
    assert "Case-sensitive flag not preserved after rename" in text
