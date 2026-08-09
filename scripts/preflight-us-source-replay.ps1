param(
    [int]$ExpectedHistoryParts = 0,
    [switch]$DeepSourceTest,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before US source replay preflight."
}

$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.source_preflight"
)
if ($ExpectedHistoryParts -gt 0) {
    $args += @("--expected-history-parts", "$ExpectedHistoryParts")
}
if ($DeepSourceTest) {
    $args += "--deep-source-test"
}

$jsonLines = & docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US source replay preflight process failed."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "us_source_preflight_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "US source preflight status: $($report.status)"
Write-Host "Safe to replay: $($report.safe_to_replay)"
Write-Host "Report: $OutputPath"
Write-Host "Historical sources: $($report.source_inventory.history_source_count)"
Write-Host "Daily sources: $($report.source_inventory.daily_source_count)"
Write-Host "Historical baseline end: $($report.historical_baseline_end)"
Write-Host "Archive sources needing staging: $($report.archive_staging_required_count)"

if ($report.status -eq "FAIL") {
    throw "US source replay preflight failed: $($report.hard_issue_types -join ', ')"
}
if ($report.status -eq "NOT_READY") {
    throw "US source replay preflight is not ready: $($report.not_ready_reasons -join ', ')"
}
