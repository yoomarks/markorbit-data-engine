from pathlib import Path


SCRIPT = Path("scripts/run-global-multi-disk-ext4-spike.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_spike_requires_explicit_apply_and_emits_architecture_decision() -> None:
    t = text()
    assert "[switch]$Apply" in t
    assert "requires an elevated Administrator PowerShell session" in t
    for marker in (
        "READY_FOR_BOUNDED_EXT4_SPIKE_APPLY",
        "DOCKER_DESKTOP_EXTERNAL_EXT4_GO",
        "DEDICATED_WSL_CLICKHOUSE_REQUIRED",
        "SPIKE_BLOCKED",
        "GLOBAL_MULTI_DISK_EXT4_SPIKE_V1",
        "GLOBAL_MULTI_DISK_EXT4_SPIKE_DONE",
    ):
        assert marker in t


def test_spike_is_four_small_dynamic_nonproduction_vhdx_files() -> None:
    t = text()
    assert "$SpikeMaximumMiB = 1024" in t
    for path in (
        "D:\\MarkOrbitData\\spike\\hot_cn_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_us_spike.vhdx",
        "D:\\MarkOrbitData\\spike\\hot_global_spike.vhdx",
        "E:\\MarkOrbitData\\spike\\warm_spike.vhdx",
    ):
        assert path in t
    assert "type=expandable" in t
    assert "SPIKE_VHDX_ALREADY_EXISTS" in t
    assert "SPIKE_MOUNT_ALREADY_EXISTS" in t
    assert "SPIKE_CLICKHOUSE_CONTAINER_ALREADY_EXISTS" in t


def test_spike_formats_only_new_vhdx_and_requires_ext4_mount_proof() -> None:
    t = text()
    assert "'--mount','--vhd',$vhdxPath,'--bare'" in t
    assert "'mkfs.ext4','-F','-L'" in t
    assert "'--mount','--vhd',$vhdxPath,'--name'" in t
    assert "findmnt -n -o FSTYPE,SOURCE,TARGET" in t
    assert "-match '^ext4\\s'" in t
    assert "Expected exactly one new WSL block disk" in t
    assert "spike_vhdx_delete_performed=False" in t


def test_spike_proves_docker_linux_filesystem_before_clickhouse() -> None:
    t = text()
    assert "runtimeStage = 'docker_bind_visibility'" in t
    assert '"/mnt/wsl/$($spec[\'mount\'])/clickhouse"' in t
    assert '"type=bind,source=$source,target=/probe"' in t
    assert "stat -f -c %T /probe" in t
    assert "FS_TYPE=" in t
    assert "ext2/ext3|ext4" in t
    assert "linux_ext_filesystem" in t


def test_spike_uses_isolated_clickhouse_with_four_disks_and_policies() -> None:
    t = text()
    assert "$SpikeContainerName = 'markorbit-ext4-spike-clickhouse'" in t
    assert "$ClickHouseImage = 'clickhouse/clickhouse-server:24.8'" in t
    for disk in ("hot_cn", "hot_us", "hot_global", "warm"):
        assert f"<{disk}><type>local</type>" in t
    for policy in ("spike_hot_cn", "spike_hot_us", "spike_hot_global", "spike_warm"):
        assert f"<{policy}><volumes>" in t
    assert "system.disks" in t
    assert "system.storage_policies" in t
    assert "temporary_clickhouse_removed" in t


def test_spike_runs_real_mergetree_background_merge_and_disk_verification() -> None:
    t = text()
    assert "ENGINE=MergeTree" in t
    assert "$InsertBatchCount = 24" in t
    assert "--multiquery" in t
    assert "max(level)" in t
    assert "disk_name" in t
    assert "background_merge_observed" in t
    assert "SELECT count(), sum(id)" in t
    assert "tmp_insert_*" in t
    assert "DROP TABLE IF EXISTS default.$table" in t
    assert "permission denied|operation not permitted|cannot rename|failed to rename" in t


def test_spike_preserves_production_data_plane_and_forbids_broad_mutation() -> None:
    t = text()
    for marker in (
        "production_clickhouse_before",
        "production_clickhouse_after",
        "accepted_volume_before_present",
        "accepted_volume_after_present",
        "production_clickhouse_restart_performed = $false",
        "production_clickhouse_mutation_performed = $false",
        "accepted_volume_mutation_performed = $false",
        "corpus_replay_performed = $false",
    ):
        assert marker in t

    forbidden = (
        "2023_5.zip",
        "replay-us-deterministic.ps1",
        "docker system prune",
        "docker volume rm",
        "docker compose down",
        "wsl.exe' @('--shutdown",
        "Resize-VHD",
        "Optimize-VHD",
        "Mount-VHD",
        "F:\\MarkOrbitData\\recovery",
        "ALTER TABLE",
        "OPTIMIZE TABLE",
        "-Apply -All",
    )
    for marker in forbidden:
        assert marker not in t


def test_cleanup_can_unmount_spike_files_but_never_deletes_them() -> None:
    t = text()
    assert "[switch]$CleanupMounts" in t
    assert "spike_stage=cleanup_mounts" in t
    assert "wsl.exe' @('--unmount',$VhdxPath)" in t
    assert "$spikeVhdxDeletePerformed = $false" in t
    for destructive in ("Remove-Item -LiteralPath $spec['path']", "del $vhdxPath", "erase $vhdxPath"):
        assert destructive not in t
