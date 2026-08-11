param(
    [switch]$VerifySourceFiles,
    [int]$ExpectedHistoryParts = 0,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before US real-data acceptance audit."
}

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before US real-data acceptance audit."
    }
}

$args = @("run", "--build", "--rm", "--no-deps", "worker", "python", "-m", "app.us.audit_real_data_v2")
if ($VerifySourceFiles) {
    $args += "--verify-source-files"
}
if ($ExpectedHistoryParts -gt 0) {
    $args += @("--expected-history-parts", "$ExpectedHistoryParts")
}

$jsonLines = & docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US real-data acceptance audit process failed."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "us_real_data_acceptance_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "US real-data acceptance status: $($report.status)"
Write-Host "Report: $OutputPath"
Write-Host "Historical successful packages: $($report.packages.history_success_count)"
Write-Host "Daily successful packages: $($report.packages.daily_success_count)"
Write-Host "Historical coverage end: $($report.coverage.historical_end)"
Write-Host "Daily coverage end: $($report.coverage.daily_end)"
Write-Host "Historical expected parts: $($report.historical_part_completeness.expected_history_parts)"
Write-Host "Historical missing expected parts: $($report.historical_part_completeness.missing_expected_parts -join ',')"
Write-Host "Snapshot tombstones: $($report.snapshot_reconciliation.total_tombstones)"

if ($report.status -eq "FAIL") {
    throw "US real-data acceptance failed: $($report.hard_fail_reasons -join ', ')"
}
if ($report.status -eq "NOT_READY") {
    throw "US real-data acceptance is not ready: $($report.not_ready_reasons -join ', ')"
}
