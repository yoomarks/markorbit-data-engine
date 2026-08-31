from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECONCILE = ROOT / "scripts" / "reconcile-clickhouse-post-copy-linux-volume.ps1"


def text() -> str:
    return RECONCILE.read_text(encoding="utf-8")


def test_reconcile_is_exact_main_idle_and_zero_worker() -> None:
    t = text()
    assert "[string]$ExpectedMainSha" in t
    assert "git fetch origin main" in t
    assert "stop-idle-worker.ps1" in t
    assert "Worker containers must be absent" in t
    assert "Exact-main drift before Linux-volume activation" in t


def test_reconcile_reuses_existing_linux_volume_without_recopy_or_wipe() -> None:
    t = text()
    lowered = t.lower()
    assert '[string]$RetainedVolume = "markorbit-data-engine_clickhouse_data"' in t
    assert "reconcile_stage=activate_existing_linux_volume" in t
    assert "volume_wipe_performed=False" in t
    assert "copy_performed=False" in t
    assert "automatic_windows_bind_rollback_performed=False" in t
    for forbidden in (
        "cp -a",
        "rm -rf",
        "docker volume rm",
        "find /target -mindepth 1 -maxdepth 1 -exec rm",
        "start-bindclickhouse",
    ):
        assert forbidden not in lowered


def test_reconcile_compares_stable_source_target_identity() -> None:
    t = text()
    assert "Get-StaticIdentityFromMount" in t
    assert "Get-StaticIdentityFromContainer" in t
    assert "metadata_sha256" in t
    assert "store_uuid_sha256" in t
    assert "Assert-StaticIdentityEqual" in t
    assert "STATIC_METADATA_UUID_IDENTITY_OK" in t


def test_reconcile_does_not_require_physical_active_rows_to_be_equal() -> None:
    t = text()
    assert "ExpectedPreActivationActiveRows = 2948782201" in t
    assert "physical_row_reduction_since_pre_activation" in t
    assert "Active physical rows unexpectedly increased without writers" in t
    assert "active_rows -ne" not in t
    assert "Logical baseline mismatch active_rows" not in t


def test_reconcile_freezes_merges_and_checks_integrity_before_real_insert() -> None:
    t = text()
    assert "SYSTEM STOP MERGES" in t
    assert "SYSTEM START MERGES" in t
    assert "schema snapshot drifted" in t
    assert "suspicious_detached_part_count" in t
    assert "schema_version tmp_insert dirs remain" in t
    assert "ENGINE=MergeTree" in t
    assert "INSERT INTO" in t
    assert "native_mergetree_commit_verified=True" in t
    assert "POST_COPY_LINUX_VOLUME_RECONCILIATION_PASS" in t


def test_reconcile_never_applies_us_schema_or_replays_corpus() -> None:
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
