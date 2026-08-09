param(
    [Parameter(Mandatory = $true)]
    [string]$ReferenceFileName,
    [switch]$NoActivate,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

foreach ($service in @("postgres")) {
    $running = docker compose ps --status running -q $service
    if ($LASTEXITCODE -ne 0 -or -not $running) {
        throw "$service must be running before USPTO status reference import."
    }
}

if ([System.IO.Path]::GetFileName($ReferenceFileName) -ne $ReferenceFileName) {
    throw "ReferenceFileName must be a file name under RAW_DATA_PATH/reference/us, not a path."
}
if (-not $ReferenceFileName.ToLowerInvariant().EndsWith(".json")) {
    throw "ReferenceFileName must end in .json"
}

$containerPath = "/data/raw/reference/us/$ReferenceFileName"
$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.import_status_reference",
    "--reference-file", $containerPath
)
if ($NoActivate) {
    $args += "--no-activate"
}

$jsonLines = & docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "USPTO status reference import failed."
}
$json = $jsonLines -join "`n"
$report = $json | ConvertFrom-Json

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "us_status_reference_import_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "USPTO status reference import status: $($report.status)"
Write-Host "Reference version: $($report.reference_version)"
Write-Host "Record count: $($report.record_count)"
Write-Host "Active: $($report.active)"
Write-Host "Report: $OutputPath"
