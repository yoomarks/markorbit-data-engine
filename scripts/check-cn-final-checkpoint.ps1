param(
    [string]$OutputPath = "",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) {
            throw "$service must be running before the CN final checkpoint."
        }
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($persistentWorkerId) {
        throw "Persistent worker is running. Stop it before the CN final checkpoint."
    }

    $checkpointArgs = @("-m", "app.cn.final_checkpoint")
    if ($Compact) {
        $checkpointArgs += "--compact"
    }

    # The checkpoint is database read-only. Mount only the checked-out app code
    # so the report uses the current branch without rebuilding the worker image.
    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @checkpointArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "CN final checkpoint produced no JSON report."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "CN final checkpoint produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "cn_m16_final_checkpoint_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "CN final checkpoint status: $($report.status)"
    Write-Host "Ready for next domain: $($report.ready_for_next_domain)"
    Write-Host "Acceptance executed: $($report.acceptance_executed)"
    Write-Host "Report: $OutputPath"

    if ($report.summary) {
        Write-Host "Registered packages: $($report.summary.registered_package_count)"
        Write-Host "Active ClickHouse bytes: $($report.summary.active_clickhouse_bytes)"
        Write-Host "Active stage rows: $($report.summary.active_stage_rows)"
    }

    if ($exitCode -ne 0) {
        $reasonCodes = @($report.reasons | ForEach-Object { $_.code })
        throw "CN final checkpoint did not pass: status=$($report.status); reasons=$($reasonCodes -join ', ')"
    }

    Write-Host "CN final checkpoint passed. Persistent worker remains stopped."
}
finally {
    Pop-Location
}
