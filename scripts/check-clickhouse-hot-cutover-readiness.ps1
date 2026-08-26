[CmdletBinding()]
param(
    [string]$HotPath = $env:CLICKHOUSE_HOT_DATA_PATH,
    [string]$ColdPath = $env:CLICKHOUSE_COLD_DATA_PATH,
    [string]$LogPath = $env:CLICKHOUSE_LOG_PATH,
    [int]$ReserveGiB = 128
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

function Invoke-ComposeShell {
    param(
        [Parameter(Mandatory = $true)][string]$Service,
        [Parameter(Mandatory = $true)][string]$Command
    )

    return Invoke-DockerText -Arguments @("compose", "exec", "-T", $Service, "sh", "-lc", $Command)
}

function Get-ScalarInt64 {
    param([Parameter(Mandatory = $true)][object[]]$Lines)

    $text = ($Lines | Where-Object { $_ -ne $null } | ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ -ne "" } | Select-Object -Last 1)
    if ($null -eq $text -or $text -notmatch '^\d+$') {
        throw "Expected integer output, got: $($Lines -join ' | ')"
    }
    return [int64]$text
}

function Resolve-Directory {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Name is required."
    }
    if ($Path -notmatch '^[A-Za-z]:[\\/]') {
        throw "$Name must be an absolute Windows drive path: $Path"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Name directory does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Get-ClickHouseBaseline {
    $command = @'
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database "$CLICKHOUSE_DB" --format TSVRaw --query "SELECT countDistinct(table), count(), coalesce(sum(rows), 0), coalesce(sum(bytes_on_disk), 0) FROM system.parts WHERE active AND database = currentDatabase()"
'@
    $line = (Invoke-ComposeShell -Service "clickhouse" -Command $command |
        Where-Object { $_ -ne $null } | ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ -ne "" } | Select-Object -Last 1)
    $parts = $line -split "`t"
    if ($parts.Count -ne 4) {
        throw "Unexpected ClickHouse baseline output: $line"
    }
    return [ordered]@{
        active_table_count = [int64]$parts[0]
        active_part_count = [int64]$parts[1]
        active_rows = [int64]$parts[2]
        active_bytes_on_disk = [int64]$parts[3]
    }
}

$resolvedHot = Resolve-Directory -Name "CLICKHOUSE_HOT_DATA_PATH" -Path $HotPath
$resolvedCold = Resolve-Directory -Name "CLICKHOUSE_COLD_DATA_PATH" -Path $ColdPath
$resolvedLog = Resolve-Directory -Name "CLICKHOUSE_LOG_PATH" -Path $LogPath

# Reuse the existing destination/Compose preflight. It is read-only.
$preflightText = @(& "$PSScriptRoot\check-hot-cold-storage.ps1" `
    -HotPath $resolvedHot -ColdPath $resolvedCold -LogPath $resolvedLog)
$preflight = (($preflightText -join [Environment]::NewLine) | ConvertFrom-Json)
if (-not $preflight.compose_validated) {
    throw "Hot/Cold Compose preflight did not validate."
}

$clickhouseIdLines = Invoke-DockerText -Arguments @("compose", "ps", "-q", "clickhouse")
$clickhouseId = ($clickhouseIdLines | ForEach-Object { $_.ToString().Trim() } |
    Where-Object { $_ -ne "" } | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($clickhouseId)) {
    throw "The current Compose ClickHouse container is required to resolve the authoritative source volume."
}

$mountsJson = (Invoke-DockerText -Arguments @("inspect", $clickhouseId, "--format", "{{json .Mounts}}")) -join ""
$mounts = @($mountsJson | ConvertFrom-Json)
$sourceMount = @($mounts | Where-Object { $_.Destination -eq "/var/lib/clickhouse" }) | Select-Object -First 1
if ($null -eq $sourceMount) {
    throw "Unable to resolve /var/lib/clickhouse from the current ClickHouse container."
}
if ($sourceMount.Type -ne "volume" -or [string]::IsNullOrWhiteSpace($sourceMount.Name)) {
    throw "Current /var/lib/clickhouse is not a Docker named volume; refusing named-volume cutover."
}
$sourceVolume = [string]$sourceMount.Name
$image = ((Invoke-DockerText -Arguments @("inspect", $clickhouseId, "--format", "{{.Config.Image}}")) -join "").Trim()
if ([string]::IsNullOrWhiteSpace($image)) {
    throw "Unable to resolve the current ClickHouse image."
}

$runningJobs = Get-ScalarInt64 -Lines (Invoke-ComposeShell -Service "postgres" -Command `
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM control.job_run WHERE status = ''RUNNING''"')
$processingCn = Get-ScalarInt64 -Lines (Invoke-ComposeShell -Service "postgres" -Command `
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM control.source_package WHERE jurisdiction = ''CN'' AND status = ''PROCESSING''"')

$runningServices = @(Invoke-DockerText -Arguments @("compose", "ps", "--services", "--status", "running") |
    ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -ne "" })

$hotEntries = @(Get-ChildItem -LiteralPath $resolvedHot -Force -ErrorAction Stop)
$hotEmpty = $hotEntries.Count -eq 0

# The source is mounted read-only into a disposable container. du reads filesystem
# metadata only; it does not query or validate the trademark corpus.
$sizeLines = Invoke-DockerText -Arguments @(
    "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
    "--mount", "type=volume,source=$sourceVolume,target=/source,readonly",
    $image, "-lc", "du -sb /source | cut -f1"
)
$sourceBytes = Get-ScalarInt64 -Lines $sizeLines

$hotRoot = [System.IO.Path]::GetPathRoot($resolvedHot)
$hotDriveName = $hotRoot.Substring(0, 1)
$hotDrive = Get-PSDrive -Name $hotDriveName -PSProvider FileSystem
$reserveBytes = [int64]$ReserveGiB * 1GB
$requiredFreeBytes = $sourceBytes + $reserveBytes
$headroomOk = [int64]$hotDrive.Free -ge $requiredFreeBytes

$baseline = Get-ClickHouseBaseline
$safe = (
    $runningJobs -eq 0 -and
    $processingCn -eq 0 -and
    $hotEmpty -and
    $headroomOk -and
    $preflight.compose_validated
)

[ordered]@{
    read_only = $true
    safe_to_cutover = $safe
    source_volume = $sourceVolume
    clickhouse_image = $image
    source_volume_bytes = $sourceBytes
    hot_path = $resolvedHot
    cold_path = $resolvedCold
    log_path = $resolvedLog
    hot_path_empty = $hotEmpty
    hot_free_bytes = [int64]$hotDrive.Free
    reserve_bytes = $reserveBytes
    required_free_bytes = $requiredFreeBytes
    headroom_ok = $headroomOk
    running_job_count = $runningJobs
    processing_cn_package_count = $processingCn
    running_services = $runningServices
    clickhouse_baseline = $baseline
    compose_validated = [bool]$preflight.compose_validated
    executes_migration = $false
    revalidates_source_packages = $false
} | ConvertTo-Json -Depth 6
