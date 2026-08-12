param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$Apply,
    [switch]$All,
    [ValidateRange(1, 1000000)]
    [int]$MaxPackages = 1,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before deterministic US replay."
}

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before deterministic US replay."
    }
}

if ($Apply) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "assert-domain-apply-gate.ps1") `
        -TargetDomain "US_APPLICATION" `
        -ExpectedApplicationHistoryParts $ExpectedHistoryParts
    if ($LASTEXITCODE -ne 0) {
        throw "US Application apply gate failed; replay was not started."
    }
}

$args = @(
    "run", "--build", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.replay_executor",
    "--expected-history-parts", "$ExpectedHistoryParts"
)
if ($DeepSourceTest) {
    $args += "--deep-source-test"
}
if ($All) {
    $args += "--all"
} else {
    $args += @("--max-packages", "$MaxPackages")
}
if ($Apply) {
    $args += "--apply"
}

$jsonLines = & docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "Deterministic US replay process failed before a report was returned."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $mode = if ($Apply) { "apply" } else { "dryrun" }
    $OutputPath = Join-Path "reports" "us_deterministic_replay_${mode}_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "US deterministic replay mode: $($report.mode)"
Write-Host "Status: $($report.status)"
Write-Host "Report: $OutputPath"
if ($report.processed_count -ne $null) {
    Write-Host "Processed this run: $($report.processed_count)"
}
if ($report.source_preflight_runs -ne $null) {
    Write-Host "Full source preflights this run: $($report.source_preflight_runs)"
}
if ($report.final_plan -and $report.final_plan.remaining_count -ne $null) {
    Write-Host "Remaining: $($report.final_plan.remaining_count)"
}
if (-not $Apply -and $report.status -eq "READY") {
    Write-Host "Dry run only. Re-run with -Apply to process the next package, or -Apply -All for the full remaining plan."
}
if ($report.status -eq "COMPLETE") {
    Write-Host "Replay is complete. Run audit-us-real-data.ps1 for the normal lightweight acceptance audit; VerifySourceFiles/source re-hash remains optional."
}

if ($report.status -in @("BLOCKED", "FAILED", "BUSY")) {
    $reason = if ($report.error) { $report.error } elseif ($report.blockers) { $report.blockers -join ", " } else { $report.status }
    throw "Deterministic US replay stopped: $reason"
}
