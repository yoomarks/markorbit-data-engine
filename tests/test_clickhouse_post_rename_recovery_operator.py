from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "scripts" / "recover-clickhouse-after-hot-path-rename.ps1"


def test_post_rename_recovery_is_fail_closed_and_path_safe() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert '"E:\\MarkOrbitData\\hot\\clickhouse-cs"' in text
    assert '"E:\\MarkOrbitData\\hot\\clickhouse"' in text
    assert "Old Hot path still exists" in text
    assert "Renamed Hot directory missing" in text
    assert "queryCaseSensitiveInfo" in text
    assert "$Path.Replace([char]47, [char]92)" in text
    assert "$NewHotPath.Replace([char]92, [char]47)" in text
    assert "$ColdPath.Replace([char]92, [char]47)" in text
    assert "$LogPath.Replace([char]92, [char]47)" in text
    assert "docker compose @compose create clickhouse" in lowered
    assert "docker compose @compose create --no-deps clickhouse" not in lowered
    assert "ClickHouse unexpectedly has compose dependencies" in text
    assert "CLICKHOUSE_HAS_NO_DEPENDENCIES_OK" in text
    assert "CREATED_MOUNTS_OK" in text
    assert "check-cn-serving-state.ps1" in text
    assert "profile-cn-hot-warm-capacity.ps1" in text
    assert "ROW_EQUIVALENCE_OK" in text
    assert "POST_RENAME_RECOVERY_OK" in text

    forbidden = (
        "rename-item",
        "remove-item",
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


def test_post_rename_recovery_never_starts_api_or_worker() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    lowered = text.lower()

    assert 'markorbit-data-engine-worker-1' in lowered
    assert 'markorbit-data-engine-api-1' in lowered
    assert "api_worker_stopped_ok" in lowered
    assert "docker start $cid" in lowered
    assert "docker start markorbit-data-engine-worker-1" not in lowered
    assert "docker start markorbit-data-engine-api-1" not in lowered


def test_post_rename_recovery_avoids_regex_for_host_path_conversion() -> None:
    text = OPERATOR.read_text(encoding="utf-8")

    assert "-replace '/'," not in text
    assert "-replace '\\', '/'" not in text
    assert "Replace([char]47, [char]92)" in text
    assert "Replace([char]92, [char]47)" in text
