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

function Invoke-HotColdComposeText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    return Invoke-DockerText -Arguments @(
        "compose", "-f", "docker-compose.yml", "-f", "docker-compose.hot-cold-storage.yml"
    ) + $Arguments
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

function Assert-BaselineEqual {
    param(
        [Parameter(Mandatory = $true)]$Before,
        [Parameter(Mandatory = $true)]$After
    )

    foreach ($field in @("active_table_count", "active_part_count", "active_rows", "active_bytes_on_disk")) {
        if ([int64]$Before.$field -ne [int64]$After[$field]) {
            throw "ClickHouse metadata baseline mismatch for $field`: before=$($Before.$field) after=$($After[$field])"
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
    if (-not $afterWriterStop.hot_path_empty -or -not $afterWriterStop.headroom_ok) {
        throw "Hot destination changed or headroom gate failed after writer shutdown."
    }
    if ($afterWriterStop.source_volume -ne $initial.source_volume) {
        throw "Authoritative ClickHouse source volume changed during cutover preparation."
    }

    $sourceVolume = [string]$initial.source_volume
    $sourceBytes = [int64]$initial.source_volume_bytes
    $image = [string]$initial.clickhouse_image

    # Prove Docker can write the three Windows bind destinations before stopping
    # the authoritative ClickHouse. The probe files are tiny and removed in the
    # same disposable container; they never touch the source named volume.
    $probeName = ".markorbit-cutover-probe-$([guid]::NewGuid().ToString('N'))"
    $probeCommand = "set -eu; trap 'rm -f /hot/$probeName /cold/$probeName /logs/$probeName' EXIT; printf hot > /hot/$probeName; printf cold > /cold/$probeName; printf logs > /logs/$probeName; test \"`$(cat /hot/$probeName)\" = hot; test \"`$(cat /cold/$probeName)\" = cold; test \"`$(cat /logs/$probeName)\" = logs"
    Invoke-DockerText -Arguments @(
        "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
        "--mount", "type=bind,source=$($initial.hot_path),target=/hot",
        "--mount", "type=bind,source=$($initial.cold_path),target=/cold",
        "--mount", "type=bind,source=$($initial.log_path),target=/logs",
        $image, "-lc", $probeCommand
    ) | Out-Null

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

    # Copy exact stopped ClickHouse files. The source volume is mounted read-only
    # and is deliberately retained after success for rollback.
    Invoke-DockerText -Arguments @(
        "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
        "--mount", "type=volume,source=$sourceVolume,target=/source,readonly",
        "--mount", "type=bind,source=$($initial.hot_path),target=/target",
        $image, "-lc", "set -eu; cp -a /source/. /target/"
    ) | Out-Null

    $targetSizeLines = Invoke-DockerText -Arguments @(
        "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
        "--mount", "type=bind,source=$($initial.hot_path),target=/target,readonly",
        $image, "-lc", "du -sb /target | cut -f1"
    )
    $targetSizeText = ($targetSizeLines | ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1)
    if ($null -eq $targetSizeText) {
        throw "Unable to measure copied Hot destination."
    }
    $targetBytes = [int64]$targetSizeText
    if ($targetBytes -ne $sourceBytes) {
        throw "Copied data size mismatch: source=$sourceBytes target=$targetBytes"
    }

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
    Assert-BaselineEqual -Before $initial.clickhouse_baseline -After $after

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
        source_volume = $sourceVolume
        source_volume_retained = $true
        source_volume_bytes = $sourceBytes
        hot_copy_bytes = $targetBytes
        hot_path = $initial.hot_path
        cold_path = $initial.cold_path
        log_path = $initial.log_path
        metadata_before = $initial.clickhouse_baseline
        metadata_after = $after
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
