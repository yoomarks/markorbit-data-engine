[CmdletBinding()]
param(
    [string]$HotPath = $env:CLICKHOUSE_HOT_DATA_PATH,
    [string]$ColdPath = $env:CLICKHOUSE_COLD_DATA_PATH,
    [string]$LogPath = $env:CLICKHOUSE_LOG_PATH,
    [int]$ReserveGiB = 128,
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-DockerText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = @(& docker @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Invoke-Readiness {
    $text = @(& "$PSScriptRoot\check-clickhouse-hot-cutover-readiness.ps1" `
        -HotPath $HotPath -ColdPath $ColdPath -LogPath $LogPath -ReserveGiB $ReserveGiB)
    return (($text -join [Environment]::NewLine) | ConvertFrom-Json)
}

function Invoke-HotColdCompose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $all = @("compose", "-f", "docker-compose.yml", "-f", "docker-compose.hot-cold-storage.yml") + $Arguments
    return Invoke-DockerText -Arguments $all
}

function Get-HotBaseline {
    $command = @'
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database "$CLICKHOUSE_DB" --format TSVRaw --query "SELECT countDistinct(table), count(), coalesce(sum(rows), 0), coalesce(sum(bytes_on_disk), 0) FROM system.parts WHERE active AND database = currentDatabase()"
'@
    $lines = Invoke-HotColdCompose -Arguments @("exec", "-T", "clickhouse", "sh", "-lc", $command)
    $line = ($lines | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -ne "" } |
        Select-Object -Last 1)
    $parts = $line -split "`t"
    if ($parts.Count -ne 4) {
        throw "Unexpected ClickHouse baseline output after cutover: $line"
    }
    return [ordered]@{
        active_table_count = [int64]$parts[0]
        active_part_count = [int64]$parts[1]
        active_rows = [int64]$parts[2]
        active_bytes_on_disk = [int64]$parts[3]
    }
}

function Get-StructuralManifest {
    param(
        [Parameter(Mandatory = $true)][string]$MountSpec,
        [Parameter(Mandatory = $true)][string]$Image,
        [Parameter(Mandatory = $true)][string]$Label
    )

    # Metadata-only: no file contents are read. The digest covers relative path,
    # regular-file size/link-count, directory path, and symlink target.
    $manifestCommand = @'
set -eu
stats="$(find /root -type f -printf '%s\n' | awk '{bytes += $1; count += 1} END {printf "%.0f\t%.0f\n", bytes, count}')"
symlink_count="$(find /root -type l -printf '.\n' | wc -l | tr -d ' ')"
directory_count="$(find /root -mindepth 1 -type d -printf '.\n' | wc -l | tr -d ' ')"
digest="$(find /root -mindepth 1 \( -type d -printf 'D\t%P\n' -o -type f -printf 'F\t%P\t%s\t%n\n' -o -type l -printf 'L\t%P\t%l\n' \) | LC_ALL=C sort | sha256sum | awk '{print $1}')"
printf '%s\t%s\t%s\t%s\n' "$stats" "$symlink_count" "$directory_count" "$digest"
'@
    $lines = Invoke-DockerText -Arguments @(
        "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
        "--mount", $MountSpec,
        $Image, "-lc", $manifestCommand
    )
    $line = ($lines | ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ -ne "" } | Select-Object -Last 1)
    $parts = $line -split "`t"
    if ($parts.Count -ne 5 -or $parts[4] -notmatch '^[0-9a-f]{64}$') {
        throw "Unexpected $Label structural manifest output: $line"
    }
    return [ordered]@{
        regular_file_bytes = [int64]$parts[0]
        regular_file_count = [int64]$parts[1]
        symlink_count = [int64]$parts[2]
        directory_count = [int64]$parts[3]
        manifest_sha256 = [string]$parts[4]
    }
}

function Assert-StructuralManifestEqual {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After
    )

    foreach ($field in @("regular_file_bytes", "regular_file_count", "symlink_count", "directory_count")) {
        if ([int64]$Before[$field] -ne [int64]$After[$field]) {
            throw "ClickHouse structural copy mismatch for $field`: source=$($Before[$field]) target=$($After[$field])"
        }
    }
    if ([string]$Before.manifest_sha256 -ne [string]$After.manifest_sha256) {
        throw "ClickHouse structural manifest mismatch after Hot copy."
    }
}

function Assert-LogicalBaselineEqual {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After
    )

    # MergeTree background merges are allowed to change active part count and
    # bytes_on_disk without changing logical data. Only merge-stable invariants
    # are hard cutover gates; part/byte values remain in the receipt as evidence.
    foreach ($field in @("active_table_count", "active_rows")) {
        if ([int64]$Before.$field -ne [int64]$After[$field]) {
            throw "ClickHouse logical baseline mismatch for $field`: before=$($Before.$field) after=$($After[$field])"
        }
    }
}

function Start-OriginalClickHouse {
    Invoke-DockerText -Arguments @(
        "compose", "-f", "docker-compose.yml", "up", "-d", "--wait", "--no-deps", "clickhouse"
    ) | Out-Null
}

function Restart-Services {
    param([string[]]$Services)
    if ($Services.Count -gt 0) {
        Invoke-DockerText -Arguments (@("compose", "start") + $Services) | Out-Null
    }
}

$initial = Invoke-Readiness
if (-not $Execute) {
    [ordered]@{
        execute = $false
        readiness = $initial
        next_action = "Re-run with -Execute only during an approved cutover window."
        source_volume_retained = $true
    } | ConvertTo-Json -Depth 8
    return
}

if (-not $initial.safe_to_cutover) {
    throw "Cutover readiness is not green; refusing execution."
}

$env:CLICKHOUSE_HOT_DATA_PATH = $initial.hot_path
$env:CLICKHOUSE_COLD_DATA_PATH = $initial.cold_path
$env:CLICKHOUSE_LOG_PATH = $initial.log_path

$writerCandidates = @("api", "worker", "mark-image-worker", "qcc-acquisition")
$runningServices = @($initial.running_services)
$writersToStop = @($writerCandidates | Where-Object { $runningServices -contains $_ })
$writersStopped = @()
$clickhouseStopped = $false
$hotActivated = $false

try {
    # Stop only writer/API services that were actually running. PostgreSQL and
    # ClickHouse stay up long enough for the second control-plane check.
    if ($writersToStop.Count -gt 0) {
        Invoke-DockerText -Arguments (@("compose", "stop") + $writersToStop) | Out-Null
        $writersStopped = @($writersToStop)
    }

    # Close the race between the initial readiness check and writer shutdown.
    # If a job/package became active, abort before ClickHouse is stopped or copied.
    $afterWriterStop = Invoke-Readiness
    if ([int64]$afterWriterStop.running_job_count -ne 0 -or
        [int64]$afterWriterStop.processing_cn_package_count -ne 0) {
        throw "A task became active before writer shutdown completed; refusing storage cutover."
    }
    if (-not $afterWriterStop.hot_path_empty -or
        -not $afterWriterStop.cold_path_empty -or
        -not $afterWriterStop.headroom_ok) {
        throw "Hot/Cold destination changed or headroom gate failed after writer shutdown."
    }
    if ($afterWriterStop.source_volume -ne $initial.source_volume) {
        throw "Authoritative ClickHouse source volume changed during cutover preparation."
    }

    $sourceVolume = [string]$initial.source_volume
    $image = [string]$initial.clickhouse_image
    # Use the post-writer-stop logical baseline for final comparison. Part count
    # and bytes are retained as observations only because background merges may
    # continue until ClickHouse itself is stopped.
    $metadataBefore = $afterWriterStop.clickhouse_baseline

    # Prove the Windows bind paths support the filesystem semantics required by
    # ClickHouse/cp -a before stopping the authoritative database. Hot is tested
    # for mkdir/rename/hardlink/symlink/ownership/mode; Cold/logs verify ownership,
    # mode and ordinary file IO. All probe artifacts are removed in-container.
    $probeName = ".markorbit-cutover-probe-$([guid]::NewGuid().ToString('N'))"
    $probeCommandTemplate = @'
set -eu
probe="__PROBE__"
hot_root="/hot/$probe"
cold_file="/cold/$probe"
log_file="/logs/$probe"
cleanup() {
  rm -rf "$hot_root"
  rm -f "$cold_file" "$log_file"
}
trap cleanup EXIT
uid="$(id -u clickhouse)"
gid="$(id -g clickhouse)"
mkdir "$hot_root"
mkdir "$hot_root/dir"
printf hot > "$hot_root/dir/source"
mv "$hot_root/dir/source" "$hot_root/dir/renamed"
mv "$hot_root/dir" "$hot_root/dir-renamed"
ln "$hot_root/dir-renamed/renamed" "$hot_root/hardlink"
ln -s "dir-renamed/renamed" "$hot_root/symlink"
chown "$uid:$gid" "$hot_root/dir-renamed/renamed" "$hot_root/hardlink"
chmod 640 "$hot_root/dir-renamed/renamed" "$hot_root/hardlink"
test "$(stat -c %u "$hot_root/dir-renamed/renamed")" = "$uid"
test "$(stat -c %g "$hot_root/dir-renamed/renamed")" = "$gid"
test "$(stat -c %a "$hot_root/dir-renamed/renamed")" = "640"
test "$(stat -c %h "$hot_root/dir-renamed/renamed")" -ge 2
grep -qx hot "$hot_root/hardlink"
grep -qx hot "$hot_root/symlink"
printf cold > "$cold_file"
printf logs > "$log_file"
chown "$uid:$gid" "$cold_file" "$log_file"
chmod 640 "$cold_file" "$log_file"
test "$(stat -c %u "$cold_file")" = "$uid"
test "$(stat -c %u "$log_file")" = "$uid"
test "$(stat -c %a "$cold_file")" = "640"
test "$(stat -c %a "$log_file")" = "640"
grep -qx cold "$cold_file"
grep -qx logs "$log_file"
'@
    $probeCommand = $probeCommandTemplate.Replace("__PROBE__", $probeName)
    Invoke-DockerText -Arguments @(
        "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
        "--mount", "type=bind,source=$($initial.hot_path),target=/hot",
        "--mount", "type=bind,source=$($initial.cold_path),target=/cold",
        "--mount", "type=bind,source=$($initial.log_path),target=/logs",
        $image, "-lc", $probeCommand
    ) | Out-Null

    $logProbePath = Join-Path -Path $initial.log_path -ChildPath $probeName
    if (@(Get-ChildItem -LiteralPath $initial.hot_path -Force).Count -ne 0 -or
        @(Get-ChildItem -LiteralPath $initial.cold_path -Force).Count -ne 0 -or
        (Test-Path -LiteralPath $logProbePath)) {
        throw "Hot/Cold/log bind capability probe did not clean up completely."
    }

    Invoke-DockerText -Arguments @("compose", "stop", "clickhouse") | Out-Null
    $clickhouseStopped = $true
    $stillRunning = @(Invoke-DockerText -Arguments @("compose", "ps", "-q", "--status", "running", "clickhouse") |
        ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -ne "" })
    if ($stillRunning.Count -ne 0) {
        throw "ClickHouse is still running after stop; refusing copy."
    }

    if (@(Get-ChildItem -LiteralPath $initial.hot_path -Force).Count -ne 0) {
        throw "Hot destination is no longer empty immediately before copy."
    }

    # Freeze the authoritative physical structure only after ClickHouse has
    # stopped. This walks filesystem metadata only; it does not hash file contents.
    $sourceManifest = Get-StructuralManifest `
        -MountSpec "type=volume,source=$sourceVolume,target=/root,readonly" `
        -Image $image `
        -Label "source"
    $sourceBytes = [int64]$sourceManifest.regular_file_bytes

    # Copy exact stopped ClickHouse files. The source volume is mounted read-only
    # and is deliberately retained after success for rollback.
    Invoke-DockerText -Arguments @(
        "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
        "--mount", "type=volume,source=$sourceVolume,target=/source,readonly",
        "--mount", "type=bind,source=$($initial.hot_path),target=/target",
        $image, "-lc", "set -eu; cp -a /source/. /target/"
    ) | Out-Null

    $targetManifest = Get-StructuralManifest `
        -MountSpec "type=bind,source=$($initial.hot_path),target=/root,readonly" `
        -Image $image `
        -Label "Hot copy"
    Assert-StructuralManifestEqual -Before $sourceManifest -After $targetManifest
    $targetBytes = [int64]$targetManifest.regular_file_bytes

    Invoke-HotColdCompose -Arguments @("up", "-d", "--wait", "--no-deps", "clickhouse") | Out-Null
    $hotActivated = $true
    $clickhouseStopped = $false

    $hotContainer = ((Invoke-HotColdCompose -Arguments @("ps", "-q", "clickhouse")) |
        ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -ne "" } |
        Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($hotContainer)) {
        throw "Hot/Cold ClickHouse container did not start."
    }
    $mountsJson = (Invoke-DockerText -Arguments @("inspect", $hotContainer, "--format", "{{json .Mounts}}")) -join ""
    $mounts = @($mountsJson | ConvertFrom-Json)
    $hotMount = @($mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse" }) | Select-Object -First 1
    $coldMount = @($mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse-cold" }) | Select-Object -First 1
    if ($null -eq $hotMount -or $hotMount.Type -ne "bind") {
        throw "Activated ClickHouse Hot data root is not a bind mount."
    }
    if ($null -eq $coldMount -or $coldMount.Type -ne "bind") {
        throw "Activated ClickHouse Cold data root is not a bind mount."
    }

    $after = Get-HotBaseline
    Assert-LogicalBaselineEqual -Before $metadataBefore -After $after

    $coldDiskLines = Invoke-HotColdCompose -Arguments @(
        "exec", "-T", "clickhouse", "sh", "-lc",
        'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database "$CLICKHOUSE_DB" --format TSVRaw --query "SELECT count() FROM system.disks WHERE name = ''cold''"'
    )
    $coldDiskCount = [int64](($coldDiskLines | ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1))
    if ($coldDiskCount -ne 1) {
        throw "ClickHouse cold disk is not registered after activation."
    }

    Restart-Services -Services $writersStopped
    $writersStopped = @()

    [ordered]@{
        migration_completed = $true
        hot_cold_activated = $true
        bind_filesystem_capabilities_verified = $true
        source_volume = $sourceVolume
        source_volume_retained = $true
        source_regular_file_bytes = $sourceBytes
        source_regular_file_count = [int64]$sourceManifest.regular_file_count
        source_symlink_count = [int64]$sourceManifest.symlink_count
        source_directory_count = [int64]$sourceManifest.directory_count
        source_structure_manifest_sha256 = [string]$sourceManifest.manifest_sha256
        source_bytes_measured_after_clickhouse_stop = $true
        hot_copy_regular_file_bytes = $targetBytes
        hot_copy_regular_file_count = [int64]$targetManifest.regular_file_count
        hot_copy_symlink_count = [int64]$targetManifest.symlink_count
        hot_copy_directory_count = [int64]$targetManifest.directory_count
        hot_copy_structure_manifest_sha256 = [string]$targetManifest.manifest_sha256
        structural_manifest_verified = $true
        hot_path = $initial.hot_path
        cold_path = $initial.cold_path
        log_path = $initial.log_path
        metadata_initial = $initial.clickhouse_baseline
        metadata_before = $metadataBefore
        metadata_after = $after
        metadata_guard_fields = @("active_table_count", "active_rows")
        metadata_observation_fields = @("active_part_count", "active_bytes_on_disk")
        cold_disk_registered = $true
        source_packages_revalidated = $false
        rollback_available = $true
    } | ConvertTo-Json -Depth 8
}
catch {
    $failure = $_
    # Fail back to the untouched Docker named volume whenever ClickHouse was
    # stopped or the Hot profile was activated. The E: copy is deliberately kept.
    try {
        if ($hotActivated) {
            Invoke-HotColdCompose -Arguments @("stop", "clickhouse") | Out-Null
        }
        if ($clickhouseStopped -or $hotActivated) {
            Start-OriginalClickHouse
        }
    }
    catch {
        Write-Error "Automatic ClickHouse rollback failed: $($_.Exception.Message)"
    }
    try {
        Restart-Services -Services $writersStopped
    }
    catch {
        Write-Error "Writer service restart after failed cutover failed: $($_.Exception.Message)"
    }
    throw $failure
}
