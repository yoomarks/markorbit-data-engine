from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts" / "finalize-clickhouse-linux-volume-acceptance.ps1"


def text() -> str:
    return FINALIZER.read_text(encoding="utf-8")


def test_finalizer_requires_exact_main_idle_and_linux_volume() -> None:
    t = text()
    assert "[string]$ExpectedMainSha" in t
    assert "git fetch origin main" in t
    assert "stop-idle-worker.ps1" in t
    assert "Worker containers must be absent" in t
    assert "[string]$AcceptedVolume = \"markorbit-data-engine_clickhouse_data\"" in t
    assert "Accepted Linux-volume mount required" in t


def test_finalizer_never_copies_wipes_or_rolls_back_windows_bind() -> None:
    t = text().lower()
    for forbidden in (
        "cp -a",
        "rm -rf",
        "docker volume rm",
        "docker-compose.hot-cold-storage.yml",
        "start-bindclickhouse",
    ):
        assert forbidden not in t
    assert "volume_wipe_performed=false" in t
    assert "copy_performed=false" in t
    assert "windows_bind_rollback_performed=false" in t
    assert "automatic_windows_bind_rollback_performed=false" in t


def test_finalizer_rechecks_source_target_static_identity() -> None:
    t = text()
    assert "Get-StaticIdentityFromMount" in t
    assert "Get-StaticIdentityFromContainer" in t
    assert "Assert-StaticIdentityEqual" in t
    assert "STATIC_METADATA_UUID_IDENTITY_OK" in t
    assert "metadata_sha256" in t
    assert "store_uuid_sha256" in t


def test_finalizer_freezes_merges_and_waits_for_drain() -> None:
    t = text()
    assert "SYSTEM STOP MERGES" in t
    assert "Wait-ForMergesToDrain" in t
    assert "SELECT count() FROM system.merges" in t
    assert "SYSTEM START MERGES" in t


def test_mergetree_probe_decodes_sql_base64_before_clickhouse_client() -> None:
    t = text()
    assert "SQL_B64='__SQL_B64__'" in t
    assert "printf '%s' \"$SQL_B64\" | base64 -d | timeout __TIMEOUT__s clickhouse-client" in t
    assert "ENGINE=MergeTree" in t
    assert "INSERT INTO" in t
    assert "SELECT count()" in t
    assert "DROP DATABASE" in t
    assert "native_mergetree_commit_verified=True" in t
    # The target-host failure shape passed Base64 directly to ClickHouse.
    assert "printf %s $payload | timeout ${TimeoutSeconds}s clickhouse-client" not in t


def test_finalizer_requires_stable_state_before_and_after_probe() -> None:
    t = text()
    assert "schema snapshot drifted" in t
    assert "suspicious_detached_part_count" in t
    assert "schema_version tmp_insert dirs remain" in t
    assert "Stable baseline changed during final acceptance" in t
    assert "table_uuid_sha256" in t
    assert "table_engine_sha256" in t


def test_finalizer_never_applies_schema_or_replays_corpus() -> None:
    t = text().lower()
    for forbidden in (
        "apply-us-m1-schema.ps1",
        "run-us-capacity-pilot.ps1",
        "replay-us-deterministic.ps1",
        "2023_5.zip",
        " -all",
    ):
        assert forbidden not in t
    assert "schema_apply_performed=false" in t
    assert "corpus_replay_performed=false" in t
