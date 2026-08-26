[CmdletBinding()]
param(
    [string]$HotPath = $env:CLICKHOUSE_HOT_DATA_PATH,
    [string]$ColdPath = $env:CLICKHOUSE_COLD_DATA_PATH,
    [string]$LogPath = $env:CLICKHOUSE_LOG_PATH,
    [switch]$SkipCompose
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-DirectoryPath {
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

function Get-DriveSnapshot {
    param([Parameter(Mandatory = $true)][string]$Path)

    $root = [System.IO.Path]::GetPathRoot($Path)
    $name = $root.Substring(0, 1)
    $drive = Get-PSDrive -Name $name -PSProvider FileSystem
    return [ordered]@{
        drive = $name.ToUpperInvariant()
        root = $root
        used_bytes = [int64]$drive.Used
        free_bytes = [int64]$drive.Free
    }
}

$resolvedHot = Assert-DirectoryPath -Name "CLICKHOUSE_HOT_DATA_PATH" -Path $HotPath
$resolvedCold = Assert-DirectoryPath -Name "CLICKHOUSE_COLD_DATA_PATH" -Path $ColdPath
$resolvedLog = Assert-DirectoryPath -Name "CLICKHOUSE_LOG_PATH" -Path $LogPath

$hotDrive = Get-DriveSnapshot -Path $resolvedHot
$coldDrive = Get-DriveSnapshot -Path $resolvedCold
$logDrive = Get-DriveSnapshot -Path $resolvedLog

if ($hotDrive.drive -eq $coldDrive.drive) {
    throw "Hot and Cold storage must be on different drives."
}
if ($logDrive.drive -ne $hotDrive.drive) {
    throw "ClickHouse logs must remain on the Hot drive for this profile."
}

$composeValidated = $false
if (-not $SkipCompose) {
    $previousHot = $env:CLICKHOUSE_HOT_DATA_PATH
    $previousCold = $env:CLICKHOUSE_COLD_DATA_PATH
    $previousLog = $env:CLICKHOUSE_LOG_PATH
    try {
        $env:CLICKHOUSE_HOT_DATA_PATH = $resolvedHot
        $env:CLICKHOUSE_COLD_DATA_PATH = $resolvedCold
        $env:CLICKHOUSE_LOG_PATH = $resolvedLog
        & docker compose -f docker-compose.yml -f docker-compose.hot-cold-storage.yml config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose config failed with exit code $LASTEXITCODE."
        }
        $composeValidated = $true
    }
    finally {
        $env:CLICKHOUSE_HOT_DATA_PATH = $previousHot
        $env:CLICKHOUSE_COLD_DATA_PATH = $previousCold
        $env:CLICKHOUSE_LOG_PATH = $previousLog
    }
}

[ordered]@{
    read_only = $true
    hot_path = $resolvedHot
    cold_path = $resolvedCold
    log_path = $resolvedLog
    hot_drive = $hotDrive
    cold_drive = $coldDrive
    log_drive = $logDrive
    compose_validated = $composeValidated
    activation_authorized = $false
} | ConvertTo-Json -Depth 4
