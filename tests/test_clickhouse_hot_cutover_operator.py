import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "scripts" / "check-clickhouse-hot-cutover-readiness.ps1"
MIGRATE = ROOT / "scripts" / "migrate-clickhouse-volume-to-hot.ps1"
COMPOSE = ROOT / "docker-compose.yml"


def _compose_service_names(text: str) -> set[str]:
    services: set[str] = set()
    in_services = False
    for line in text.splitlines():
        if line == "services:":
            in_services = True
            continue
        if not in_services:
            continue
        if line and not line.startswith(" "):
            break
        if line.startswith("  ") and not line.startswith("    "):
            stripped = line.strip()
            if stripped.endswith(":"):
                services.add(stripped[:-1])
    return services


def test_readiness_is_control_plane_and_metadata_only():
    text = READINESS.read_text(encoding="utf-8")

    assert "safe_to_cutover" in text
    assert "control.job_run" in text
    assert "status = ''RUNNING''" in text
    assert "control.source_package" in text
    assert "status = ''PROCESSING''" in text
    assert "system.parts" in text
    assert "source=$sourceVolume,target=/source,readonly" in text
    assert "find /source -type f" in text
    assert "source_regular_file_bytes" in text
    assert "cold_path_empty" in text
    assert "revalidates_source_packages = $false" in text

    forbidden = (
        "OPTIMIZE TABLE",
        "SELECT * FROM",
        "FINAL",
        "docker volume rm",
        "down -v",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in text


def test_readiness_resolves_source_mount_without_native_template_string_literal():
    text = READINESS.read_text(encoding="utf-8")

    assert '"inspect", $clickhouseId, "--format", "{{json .Mounts}}"' in text
    assert "$mountsJson | ConvertFrom-Json" in text
    assert "foreach ($mount in $mounts)" in text
    assert '[string]$mount.Destination -ne "/var/lib/clickhouse"' in text
    assert "$sourceMount = $mount" in text
    assert "Multiple /var/lib/clickhouse mounts found" in text
    assert "$sourceMountType = [string]$sourceMount.Type" in text
    assert "$sourceMountName = [string]$sourceMount.Name" in text
    assert "Observed Type='$sourceMountType' Name='$sourceMountName'" in text

    # Windows PowerShell 5.1 can strip embedded quote characters while building
    # a native-process command line. Keep the Docker template quote-free and do
    # the exact Destination match after JSON parsing instead.
    assert "$sourceMountFormat" not in text
    assert "{{if eq .Destination" not in text
    assert 'Where-Object { $_.Destination -eq "/var/lib/clickhouse" }' not in text


def test_readiness_labels_scalar_evidence_failures():
    text = READINESS.read_text(encoding="utf-8")

    assert '[AllowNull()][object]$Lines' in text
    assert 'throw "Expected integer output for $Name, got: $rendered"' in text
    assert '$rendered = if ($items.Count -eq 0) { "<empty>" }' in text

    for variable, field in (
        ("$runningJobLines", "running_job_count"),
        ("$processingCnLines", "processing_cn_package_count"),
        ("$sizeLines", "source_regular_file_bytes"),
    ):
        assert f"{variable} = @(" in text
        assert f'Get-ScalarInt64 -Name "{field}" -Lines {variable}' in text

    assert "Get-ScalarInt64 -Lines (Invoke-ComposeShell" not in text


def test_migration_is_explicit_source_preserving_and_rollback_capable():
    text = MIGRATE.read_text(encoding="utf-8")

    assert "[switch]$Execute" in text
    assert "if (-not $Execute)" in text
    assert "check-clickhouse-hot-cutover-readiness.ps1" in text
    assert '"compose", "stop", "clickhouse"' in text
    assert "source=$sourceVolume,target=/source,readonly" in text
    assert "cp -a /source/. /target/" in text
    assert "Get-StructuralManifest" in text
    assert "structural_manifest_verified = $true" in text
    assert "source_volume_retained = $true" in text
    assert "Start-OriginalClickHouse" in text
    assert "Assert-LogicalBaselineEqual" in text
    assert "system.disks" in text
    assert "source_packages_revalidated = $false" in text

    forbidden = (
        "docker volume rm",
        "down -v",
        "Remove-Item $sourceVolume",
        "OPTIMIZE TABLE",
        "2023_5.zip",
    )
    for marker in forbidden:
        assert marker not in text


def test_migration_closes_writer_shutdown_race_before_clickhouse_stop():
    text = MIGRATE.read_text(encoding="utf-8")

    writer_stop = text.index('if ($writersToStop.Count -gt 0)')
    second_readiness = text.index("$afterWriterStop = Invoke-Readiness")
    clickhouse_stop = text.index('Invoke-DockerText -Arguments @("compose", "stop", "clickhouse")')
    copy_data = text.index("cp -a /source/. /target/")

    assert writer_stop < second_readiness < clickhouse_stop < copy_data


def test_migration_probes_windows_bind_semantics_before_clickhouse_stop():
    text = MIGRATE.read_text(encoding="utf-8")

    probe = text.index("$probeCommandTemplate = @'")
    clickhouse_stop = text.index('Invoke-DockerText -Arguments @("compose", "stop", "clickhouse")')

    assert probe < clickhouse_stop
    for marker in (
        'mkdir "$hot_root/dir"',
        'mv "$hot_root/dir" "$hot_root/dir-renamed"',
        'ln "$hot_root/dir-renamed/renamed" "$hot_root/hardlink"',
        'ln -s "dir-renamed/renamed" "$hot_root/symlink"',
        'chown "$uid:$gid"',
        'chmod 640',
        'stat -c %h',
        'bind_filesystem_capabilities_verified = $true',
    ):
        assert marker in text


def test_migration_freezes_structural_source_manifest_only_after_clickhouse_stop():
    text = MIGRATE.read_text(encoding="utf-8")

    clickhouse_stop = text.index('Invoke-DockerText -Arguments @("compose", "stop", "clickhouse")')
    source_measure = text.index("$sourceManifest = Get-StructuralManifest")
    copy_data = text.index("cp -a /source/. /target/")

    assert clickhouse_stop < source_measure < copy_data
    assert "$sourceBytes = [int64]$initial.source_regular_file_bytes" not in text
    assert "source_bytes_measured_after_clickhouse_stop = $true" in text


def test_migration_structural_manifest_is_metadata_only_and_compared_before_activation():
    text = MIGRATE.read_text(encoding="utf-8")

    manifest_function = text[
        text.index("function Get-StructuralManifest") : text.index("function Assert-StructuralManifestEqual")
    ]
    assert "F\\t%P\\t%s\\t%n" in manifest_function
    assert "L\\t%P\\t%l" in manifest_function
    assert "D\\t%P" in manifest_function
    assert "sha256sum" in manifest_function
    assert "regular_file_bytes" in manifest_function
    assert "cat /root" not in manifest_function

    copy_data = text.index("cp -a /source/. /target/")
    target_measure = text.index("$targetManifest = Get-StructuralManifest")
    compare_manifest = text.index("Assert-StructuralManifestEqual -Before $sourceManifest -After $targetManifest")
    activate_hot = text.index('Invoke-HotColdCompose -Arguments @("up", "-d", "--wait", "--no-deps", "clickhouse")')

    assert copy_data < target_measure < compare_manifest < activate_hot
    assert "source_structure_manifest_sha256" in text
    assert "hot_copy_structure_manifest_sha256" in text


def test_migration_hard_gates_only_merge_stable_metadata():
    text = MIGRATE.read_text(encoding="utf-8")

    function_text = text[
        text.index("function Assert-LogicalBaselineEqual") : text.index("function Start-OriginalClickHouse")
    ]
    assert '@("active_table_count", "active_rows")' in function_text
    assert 'foreach ($field in @("active_table_count", "active_part_count", "active_rows", "active_bytes_on_disk"))' not in text
    assert "$metadataBefore = $afterWriterStop.clickhouse_baseline" in text
    assert "metadata_guard_fields = @(\"active_table_count\", \"active_rows\")" in text
    assert "metadata_observation_fields = @(\"active_part_count\", \"active_bytes_on_disk\")" in text


def test_migration_pause_set_tracks_every_compose_application_service():
    migrate_text = MIGRATE.read_text(encoding="utf-8")
    compose_text = COMPOSE.read_text(encoding="utf-8")

    match = re.search(
        r"\$writerCandidates\s*=\s*@\((.*?)\)",
        migrate_text,
        flags=re.DOTALL,
    )
    assert match is not None
    paused_services = set(re.findall(r'"([^"]+)"', match.group(1)))

    compose_services = _compose_service_names(compose_text)
    assert {"postgres", "clickhouse"}.issubset(compose_services)
    application_services = compose_services - {"postgres", "clickhouse"}

    assert paused_services == application_services
