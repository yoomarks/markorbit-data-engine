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
    $OutputPath = Join-Path $reportDir "m16_real_data_preflight_$stamp.json"
}
$outputFull = [System.IO.Path]::GetFullPath($OutputPath)
$outputDir = Split-Path -Parent $outputFull
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

Write-Host "Building current worker image without starting the persistent worker..." -ForegroundColor Cyan
docker compose build worker
if ($LASTEXITCODE -ne 0) {
    throw "Worker image build failed."
}

Write-Host "Running non-destructive M1.6 real-data preflight..." -ForegroundColor Cyan
$jsonText = docker compose run --rm --no-deps worker python -m app.cn.preflight_m16_real_data | Out-String
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 real-data preflight process failed."
}

$result = $jsonText | ConvertFrom-Json
$jsonText | Set-Content -LiteralPath $outputFull -Encoding UTF8

Write-Host "Preflight status: $($result.status)" -ForegroundColor $(if ($result.status -eq "FAIL") { "Red" } elseif ($result.status -eq "PASS_WITH_WARNINGS") { "Yellow" } else { "Green" })
Write-Host "Mode: $($result.mode)"
Write-Host "Replay safe: $($result.safe_to_run_replay_command)"
Write-Host "Inference audit safe: $($result.safe_to_run_inference_audit)"
Write-Host "Report: $outputFull"
Write-Host "Persistent worker remains stopped."

if ($result.hard_fail_reasons.Count -gt 0) {
    Write-Host "Hard failures:" -ForegroundColor Red
    $result.hard_fail_reasons | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    throw "M1.6 real-data preflight failed. Do not replay CN packages until the hard failures are resolved."
}

if ($result.warning_reasons.Count -gt 0) {
    Write-Host "Warnings:" -ForegroundColor Yellow
    $result.warning_reasons | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow }
}
