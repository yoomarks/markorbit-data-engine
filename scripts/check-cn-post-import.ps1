param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedFileName,
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
            throw "$service must be running before the CN post-import acceptance check."
        }
    }

    $persistentWorkerId = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }

    $auditArgs = @(
        "-m",
        "app.cn.post_import_acceptance",
        "--expected-file-name",
        $ExpectedFileName
    )
    if ($persistentWorkerId) {
        $auditArgs += "--persistent-worker-running"
    }
    if ($Compact) {
        $auditArgs += "--compact"
    }

    # Read-only acceptance. Use the checked-out application code without rebuilding
    # or starting/restarting the persistent worker.
    $jsonLines = & docker compose run --rm --no-deps -T `
        --volume "${repoRoot}\app:/app/app:ro" `
        worker python @auditArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "CN post-import acceptance produced no JSON report."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "CN post-import acceptance produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $safeFileName = $ExpectedFileName -replace '[^A-Za-z0-9._-]', '_'
        $OutputPath = Join-Path "reports" "cn_m16_post_import_${safeFileName}_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "CN post-import status: $($report.status)"
    Write-Host "Expected package: $($report.expected_file_name)"
    Write-Host "Expected package SUCCESS: $($report.expected_package_success)"
    Write-Host "Replay readiness: $($report.readiness_status)"
    Write-Host "Final checkpoint executed: $($report.final_checkpoint_executed)"
    Write-Host "Next mode: $($report.next_action.mode)"
    if ($report.next_action.command) {
        Write-Host "Next command: $($report.next_action.command)"
    }
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0) {
        $reasonCodes = @($report.reasons | ForEach-Object { $_.code })
        throw "CN post-import acceptance blocked: status=$($report.status); reasons=$($reasonCodes -join ', ')"
    }

    Write-Host "CN post-import acceptance completed read-only. No persistent worker was started or restarted."
}
finally {
    Pop-Location
}
