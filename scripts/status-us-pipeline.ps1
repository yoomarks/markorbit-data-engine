param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$VerifySourceFiles,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before taking a deterministic US pipeline snapshot."
}

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before US pipeline readiness inspection."
    }
}

$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.pipeline_readiness",
    "--expected-history-parts", "$ExpectedHistoryParts"
)
if ($DeepSourceTest) {
    $args += "--deep-source-test"
}
if ($VerifySourceFiles) {
    $args += "--verify-source-files"
}

$jsonLines = & docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US pipeline readiness process failed before a report was returned."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "us_pipeline_readiness_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "US pipeline state: $($report.state)"
Write-Host "Ready/accepted: $($report.ready)"
Write-Host "Report: $OutputPath"
if ($report.reason_codes -and $report.reason_codes.Count -gt 0) {
    Write-Host "Reasons: $($report.reason_codes -join ', ')"
}
if ($report.next_action) {
    Write-Host "Next action: $($report.next_action.code)"
    Write-Host $report.next_action.description
    if ($report.next_action.command) {
        Write-Host "Command: $($report.next_action.command)"
    }
    if ($report.next_action.destructive) {
        Write-Host "Warning: next action is destructive."
    }
}
