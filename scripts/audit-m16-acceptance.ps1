param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before M1.6 acceptance audit."
}

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before M1.6 acceptance audit."
    }
}

Write-Host "Running M1.6 acceptance integrity audit..."
$jsonLines = & docker compose run --build --rm --no-deps -T worker python -m app.cn.audit_acceptance
$exitCode = $LASTEXITCODE
$json = $jsonLines -join "`n"

if (-not $json.Trim()) {
    throw "M1.6 acceptance audit produced no JSON report."
}

try {
    $report = $json | ConvertFrom-Json
}
catch {
    throw "M1.6 acceptance audit produced invalid JSON: $($_.Exception.Message)"
}

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "cn_m16_full_acceptance_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "CN M1.6 acceptance status: $($report.status)"
Write-Host "Report: $OutputPath"
Write-Host "Registered packages: $($report.package_registry.package_count)"
Write-Host "Non-success packages: $($report.package_registry.non_success_count)"

if ($exitCode -ne 0 -or $report.status -eq "FAIL") {
    throw "M1.6 acceptance failed: $($report.hard_fail_reasons -join ', ')"
}
if ($report.status -eq "NOT_READY") {
    throw "M1.6 acceptance is not ready: $($report.not_ready_reasons -join ', ')"
}

Write-Host "Acceptance audit complete. Persistent worker remains stopped."
