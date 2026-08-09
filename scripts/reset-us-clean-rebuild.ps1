param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$Apply,
    [string]$ConfirmReset = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$RequiredConfirmation = "RESET-US-M1.3"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before US clean rebuild reset."
}

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before US clean rebuild reset."
    }
}

if ($Apply -and $ConfirmReset -cne $RequiredConfirmation) {
    throw "Destructive reset requires -ConfirmReset $RequiredConfirmation"
}

$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.reset_rebuild",
    "--expected-history-parts", "$ExpectedHistoryParts"
)
if ($DeepSourceTest) {
    $args += "--deep-source-test"
}
if ($Apply) {
    $args += @("--apply", "--confirm", $RequiredConfirmation)
}

$jsonLines = & docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US clean rebuild reset process failed before a report was returned."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $mode = if ($Apply) { "apply" } else { "dryrun" }
    $OutputPath = Join-Path "reports" "us_clean_rebuild_reset_${mode}_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "US clean rebuild reset mode: $($report.mode)"
Write-Host "Status: $($report.status)"
if ($report.total_fact_rows -ne $null) {
    Write-Host "Current US fact rows: $($report.total_fact_rows)"
} elseif ($report.plan.total_fact_rows -ne $null) {
    Write-Host "Current US fact rows: $($report.plan.total_fact_rows)"
}
if ($report.registered_package_count -ne $null) {
    Write-Host "Registered US packages: $($report.registered_package_count)"
} elseif ($report.plan.registered_package_count -ne $null) {
    Write-Host "Registered US packages: $($report.plan.registered_package_count)"
}
Write-Host "Report: $OutputPath"
if ($report.manifest_path) {
    Write-Host "Pre-reset evidence manifest: $($report.manifest_path)"
    Write-Host "Manifest SHA-256: $($report.manifest_sha256)"
}

if (-not $Apply -and $report.status -eq "READY") {
    Write-Host "Dry run only. To reset, re-run with -Apply -ConfirmReset $RequiredConfirmation."
}
if ($report.status -eq "RESET_COMPLETE") {
    Write-Host "US facts are cleared and existing US source packages are REGISTERED for deterministic replay."
    Write-Host "Next: run replay-us-deterministic.ps1 in dry-run mode before applying replay."
}
if ($report.status -in @("BLOCKED", "BUSY")) {
    $reason = if ($report.blockers) { $report.blockers -join ", " } elseif ($report.plan.blockers) { $report.plan.blockers -join ", " } else { $report.status }
    throw "US clean rebuild reset stopped: $reason"
}
