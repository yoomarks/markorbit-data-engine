param(
    [double]$MinimumHostFreeGiB = 128,
    [double]$MinimumHostFreePercent = 10,
    [double]$MinimumClickHouseFreeGiB = 128,
    [double]$MinimumClickHouseFreePercent = 10,
    [double]$ReserveGiB = 32,
    [string]$HostStoragePath = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not $HostStoragePath) {
        $envPath = Join-Path $repoRoot ".env"
        if (Test-Path -LiteralPath $envPath -PathType Leaf) {
            $rawLine = Get-Content -LiteralPath $envPath -Encoding UTF8 |
                Where-Object { $_ -match '^\s*RAW_DATA_PATH\s*=' } |
                Select-Object -First 1
            if ($rawLine) {
                $HostStoragePath = (($rawLine -split '=', 2)[1]).Trim().Trim('"').Trim("'")
            }
        }
    }
    if (-not $HostStoragePath) {
        $HostStoragePath = $repoRoot
    }
    if (-not [System.IO.Path]::IsPathRooted($HostStoragePath)) {
        $HostStoragePath = Join-Path $repoRoot $HostStoragePath
    }

    $existingPath = $HostStoragePath
    while (-not (Test-Path -LiteralPath $existingPath) -and $existingPath) {
        $parent = Split-Path -Parent $existingPath
        if (-not $parent -or $parent -eq $existingPath) { break }
        $existingPath = $parent
    }
    if (-not (Test-Path -LiteralPath $existingPath)) {
        throw "Unable to resolve host storage path for headroom check: $HostStoragePath"
    }

    $resolvedHostPath = (Resolve-Path -LiteralPath $existingPath).Path
    $driveRoot = [System.IO.Path]::GetPathRoot($resolvedHostPath)
    if (-not $driveRoot) {
        throw "Unable to determine host drive for storage path: $resolvedHostPath"
    }
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    $hostFreeBytes = [int64]$drive.AvailableFreeSpace
    $hostTotalBytes = [int64]$drive.TotalSize
    $gib = [math]::Pow(1024, 3)
    $hostRequiredBytes = [math]::Max(
        $MinimumHostFreeGiB * $gib,
        $hostTotalBytes * ($MinimumHostFreePercent / 100.0)
    ) + ($ReserveGiB * $gib)
    $hostFreePercent = if ($hostTotalBytes -gt 0) { 100.0 * $hostFreeBytes / $hostTotalBytes } else { 0.0 }
    $hostSafe = $hostTotalBytes -gt 0 -and $hostFreeBytes -ge $hostRequiredBytes

    $clickhouseRunning = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouseRunning) {
        throw "clickhouse must be running before storage headroom can be checked."
    }

    $chArgs = @(
        "run", "--rm", "--no-deps", "-T",
        "--volume", "${repoRoot}\app:/app/app:ro",
        "worker", "python", "-m", "app.storage_headroom",
        "--minimum-free-gib", "$MinimumClickHouseFreeGiB",
        "--minimum-free-percent", "$MinimumClickHouseFreePercent",
        "--reserve-gib", "$ReserveGiB",
        "--compact"
    )
    $clickhouseJsonLines = & docker compose @chArgs
    $clickhouseExitCode = $LASTEXITCODE
    $clickhouseJson = $clickhouseJsonLines -join "`n"
    if (-not $clickhouseJson.Trim()) {
        throw "ClickHouse headroom check produced no JSON report."
    }
    try {
        $clickhouse = $clickhouseJson | ConvertFrom-Json
    }
    catch {
        throw "ClickHouse headroom check produced invalid JSON: $($_.Exception.Message)"
    }

    $report = [ordered]@{
        headroom_version = "DATA_ENGINE_STORAGE_HEADROOM_V1"
        read_only = $true
        status = if ($hostSafe -and $clickhouse.safe_to_mutate -and $clickhouseExitCode -eq 0) { "PASS" } else { "BLOCKED" }
        safe_to_mutate = [bool]($hostSafe -and $clickhouse.safe_to_mutate -and $clickhouseExitCode -eq 0)
        policy = [ordered]@{
            minimum_host_free_gib = $MinimumHostFreeGiB
            minimum_host_free_percent = $MinimumHostFreePercent
            minimum_clickhouse_free_gib = $MinimumClickHouseFreeGiB
            minimum_clickhouse_free_percent = $MinimumClickHouseFreePercent
            reserve_gib = $ReserveGiB
        }
        host = [ordered]@{
            requested_path = $HostStoragePath
            resolved_path = $resolvedHostPath
            drive_root = $driveRoot
            free_space = $hostFreeBytes
            total_space = $hostTotalBytes
            free_percent = $hostFreePercent
            required_free_bytes = [int64]$hostRequiredBytes
            safe = [bool]$hostSafe
        }
        clickhouse = $clickhouse
    }
    $json = $report | ConvertTo-Json -Depth 20

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "storage_headroom_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) { New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "Storage headroom status: $($report.status)"
    Write-Host ("Host free: {0:N2} GiB ({1:N2}%)" -f ($hostFreeBytes / $gib), $hostFreePercent)
    if ($clickhouse.disk) {
        Write-Host ("ClickHouse free: {0:N2} GiB ({1:N2}%)" -f ($clickhouse.disk.free_space / $gib), $clickhouse.disk.free_percent)
    }
    Write-Host "Report: $OutputPath"

    if (-not $report.safe_to_mutate) {
        throw "Storage headroom policy blocked mutation. Free space must satisfy both the host-volume and ClickHouse-volume thresholds plus reserve."
    }
}
finally {
    Pop-Location
}
