param(
    [switch]$Compact,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $clickhouseId = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouseId) {
        throw "ClickHouse must be running before the storage capacity profile can be read."
    }

    $argsList = @("-m", "app.storage_capacity_profile")
    if ($Compact) { $argsList += "--compact" }

    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @argsList
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"
    if ($exitCode -ne 0) {
        throw "Storage capacity profile failed with exit code $exitCode."
    }
    if (-not $json.Trim()) {
        throw "Storage capacity profile produced no JSON report."
    }
    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "Storage capacity profile produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "storage_capacity_profile_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    $gib = [math]::Pow(1024, 3)
    Write-Host "Storage capacity profile: $($report.profile_version)"
    Write-Host ("Active storage: {0:N2} GiB" -f ($report.active_bytes / $gib))
    Write-Host "Active rows: $($report.active_rows)"
    foreach ($family in $report.families) {
        Write-Host ("  {0}: {1:N2} GiB ({2:P1})" -f $family.family, ($family.bytes_on_disk / $gib), $family.byte_share)
    }
    Write-Host "Report: $OutputPath"
    Write-Host "Read-only profile complete. No table mutation, OPTIMIZE, deletion, or migration was performed."
}
finally {
    Pop-Location
}
