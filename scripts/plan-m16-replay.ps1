param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "Persistent worker is running. Stop it first: docker compose stop worker"
}

$runningServices = @(docker compose ps --status running --services)
foreach ($required in @("postgres", "clickhouse")) {
    if ($runningServices -notcontains $required) {
        throw "Required service is not running: $required"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputPath) {
    $reportDir = Join-Path $repoRoot "reports"
    New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path $reportDir "m16_replay_plan_$stamp.json"
}
$outputFull = [System.IO.Path]::GetFullPath($OutputPath)
$outputDir = Split-Path -Parent $outputFull
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

Write-Host "Building current worker image without starting the persistent worker..." -ForegroundColor Cyan
docker compose build worker
if ($LASTEXITCODE -ne 0) {
    throw "Worker image build failed."
}

Write-Host "Building deterministic M1.6 clean replay plan..." -ForegroundColor Cyan
$jsonText = docker compose run --rm --no-deps worker python -m app.cn.replay_plan | Out-String
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 replay-plan process failed."
}

$result = $jsonText | ConvertFrom-Json
$jsonText | Set-Content -LiteralPath $outputFull -Encoding UTF8

Write-Host "Replay plan status: $($result.status)" -ForegroundColor $(if ($result.status -eq "FAIL") { "Red" } elseif ($result.status -eq "PASS_WITH_WARNINGS") { "Yellow" } else { "Green" })
Write-Host "Packages: $($result.package_count)"
Write-Host "Base partitions: $($result.base_partition_count)"
Write-Host "Monthly patches: $($result.monthly_patch_count)"
Write-Host "Plan: $outputFull"
Write-Host "Persistent worker remains stopped. No package was registered or ingested."

if ($result.hard_fail_reasons.Count -gt 0) {
    Write-Host "Hard failures:" -ForegroundColor Red
    $result.hard_fail_reasons | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    throw "M1.6 replay plan is unsafe. Do not run CN replay until the hard failures are resolved."
}

if ($result.warning_reasons.Count -gt 0) {
    Write-Host "Warnings:" -ForegroundColor Yellow
    $result.warning_reasons | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow }
}

Write-Host "Expected processing order:" -ForegroundColor Green
$result.expected_processing_order | ForEach-Object {
    Write-Host " $($_.registration_order). $($_.file_name) [$($_.package_kind)] $($_.partition_value)"
}
