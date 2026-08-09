param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest,
    [switch]$Apply,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before US replay source staging."
}

$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.stage_sources",
    "--expected-history-parts", "$ExpectedHistoryParts"
)
if ($DeepSourceTest) {
    $args += "--deep-source-test"
}
if ($Apply) {
    $args += "--apply"
}

$jsonLines = & docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US replay source staging process failed."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $mode = if ($Apply) { "apply" } else { "dryrun" }
    $OutputPath = Join-Path "reports" "us_source_staging_${mode}_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "US source staging mode: $($report.mode)"
Write-Host "US source staging status: $($report.status)"
Write-Host "Copy required: $($report.copy_required_count)"
Write-Host "Already staged: $($report.already_staged_count)"
Write-Host "Conflicts: $($report.conflict_count)"
Write-Host "Report: $OutputPath"

if ($report.status -eq "BLOCKED") {
    throw "US source staging blocked: $($report.blocked_reason)"
}
if (-not $Apply -and $report.status -eq "READY") {
    Write-Host "Dry run only. Re-run with -Apply to perform verified archive-to-incoming copies."
}
