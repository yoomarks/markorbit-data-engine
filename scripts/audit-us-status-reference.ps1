param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

foreach ($service in @("postgres", "clickhouse")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before USPTO status reference inventory."
    }
}

$jsonLines = & docker compose run --rm --no-deps worker python -m app.us.status_reference_inventory
if ($LASTEXITCODE -ne 0) {
    throw "USPTO status reference inventory failed."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "us_status_reference_inventory_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "Reference version: $($report.reference.reference_version)"
Write-Host "Reference records: $($report.reference_record_count)"
Write-Host "Observed status codes: $($report.observed_status_code_count)"
Write-Host "Mapped codes: $($report.mapped_code_count)"
Write-Host "Unmapped codes: $($report.unmapped_code_count)"
Write-Host "Report: $OutputPath"
