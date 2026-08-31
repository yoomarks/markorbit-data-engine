from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-dedicated-wsl-clickhouse-spike.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_operator_is_explicit_apply_and_exact_main_guarded() -> None:
    text = source()
    assert "[switch]$Apply" in text
    assert "[switch]$CleanupMounts" in text
    assert "Assert-ExactMain 'entry'" in text
    assert "Assert-ExactMain 'exit'" in text
    assert "Working tree must be clean" in text


def test_runtime_identity_and_exact_clickhouse_version_are_frozen() -> None:
    text = source()
    assert "MarkOrbit-ClickHouse-Spike" in text
    assert "D:\\MarkOrbitData\\wsl-runtime\\MarkOrbit-ClickHouse-Spike" in text
    assert "F:\\MarkOrbitData\\spike\\MarkOrbit-ClickHouse-Spike-base.tar" in text
    assert "24.8.14.39" in text
    assert "clickhouse-common-static_${ClickHouseVersion}_amd64.deb" in text
    assert "packages.clickhouse.com/deb/pool/main/c/clickhouse" in text
    assert "PRODUCTION_CLICKHOUSE_VERSION_NOT_" in text


def test_export_import_are_bounded_and_existing_distros_are_not_unregistered() -> None:
    text = source()
    assert "@('--export',$ToolingDistro,$ExportTar)" in text
    assert "@('--import',$RuntimeDistro,$RuntimeRoot,$ExportTar,'--version','2')" in text
    assert "--unregister" not in text
    assert "runtime_distro_unregister_performed=False" in text
    assert "Remove-Item" not in text


def test_four_retained_ext4_disks_are_reused_not_reformatted() -> None:
    text = source()
    for path in (
        "D:\\MarkOrbitData\\spike\\hot_cn_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_us_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_global_spike.vhdx",
        "E:\\MarkOrbitData\\spike\\warm_spike.vhdx",
    ):
        assert path in text
    assert "mkfs.ext4" not in text
    assert "--mount','--vhd" in text
    assert "native-clickhouse" in text
    assert "spike_vhdx_delete_performed=False" in text


def test_native_storage_policies_and_real_mergetree_acceptance_are_required() -> None:
    text = source()
    for disk in ("hot_cn", "hot_us", "hot_global", "warm"):
        assert f"<{disk}><type>local</type>" in text
    for policy in ("spike_hot_cn", "spike_hot_us", "spike_hot_global", "spike_warm"):
        assert policy in text
    assert "ENGINE=MergeTree" in text
    assert "$InsertBatchCount = 24" in text
    assert "max(level)" in text
    assert "uniqExact(disk_name)" in text
    assert "tmp_insert_*" in text
    assert "permission denied|operation not permitted|cannot rename|failed to rename" in text


def test_cross_runtime_connectivity_tests_direct_and_stable_routes() -> None:
    text = source()
    assert "hostname','-I" in text
    assert "docker_direct_native" in text
    assert "app_direct_http" in text
    assert "host.docker.internal" in text
    assert "docker_stable_native" in text
    assert "app_stable_http" in text
    assert "clickhouse_connect.get_client" in text
    assert "--no-deps" in text


def test_decisions_distinguish_storage_go_from_connectivity_blocker() -> None:
    text = source()
    assert "DEDICATED_WSL_CLICKHOUSE_GO" in text
    assert "WSL_CLICKHOUSE_STORAGE_GO_CONNECTIVITY_BLOCKED" in text
    assert "WSL_CLICKHOUSE_SPIKE_BLOCKED" in text
    assert "READY_FOR_DEDICATED_WSL_CLICKHOUSE_APPLY" in text


def test_production_safety_invariants_are_frozen() -> None:
    text = source()
    assert "worker_container_count_before" in text
    assert "worker_container_count_after" in text
    assert "production_clickhouse_before_ready" in text
    assert "production_clickhouse_after_ready" in text
    assert "accepted_volume_before_present" in text
    assert "accepted_volume_after_present" in text
    assert "production_clickhouse_restart_performed=False" in text
    assert "production_clickhouse_mutation_performed=False" in text
    assert "accepted_volume_mutation_performed=False" in text
    assert "corpus_replay_performed=False" in text
    assert "docker','prune" not in text
    assert "docker','volume','rm" not in text


def test_cleanup_stops_native_server_and_only_unmounts_spike_vhdx() -> None:
    text = source()
    assert "Stop-SpikeServer" in text
    assert "wsl.exe' @('--unmount',$VhdxPath)" in text
    assert "server_stopped=" in text
    assert "spike_unmount_performed=" in text
    assert "default_distro_before=" in text
    assert "default_distro_final=" in text
    assert "--set-default" in text
