param(
    [Parameter(Mandatory = $true)]
    [string]$CnReport,

    [Parameter(Mandatory = $true)]
    [string]$ApplicationReport,

    [Parameter(Mandatory = $true)]
    [string]$AssignmentReport,

    [Parameter(Mandatory = $true)]
    [string]$TtabReport,

    [int]$ExpectedApplicationHistoryParts = 91,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedApplicationDailyThrough,

    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before final four-domain acceptance."
}

$postgres = docker compose ps --status running -q postgres
if ($LASTEXITCODE -ne 0 -or -not $postgres) {
    throw "postgres must be running before final four-domain acceptance."
}

$reportPaths = @{
    cn = $CnReport
    application = $ApplicationReport
    assignment = $AssignmentReport
    ttab = $TtabReport
}
$reports = @{}
$reportFiles = @{}
foreach ($domain in $reportPaths.Keys) {
    $path = $reportPaths[$domain]
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Acceptance report not found for $domain`: $path"
    }
    $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    try {
        $reports[$domain] = $raw | ConvertFrom-Json
    }
    catch {
        throw "Acceptance report is invalid JSON for $domain`: $path"
    }
    $reportFiles[$domain] = @{
        path = $path
        sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$bundle = @{
    policy = @{
        expected_application_history_parts = $ExpectedApplicationHistoryParts
        expected_application_daily_through = $ExpectedApplicationDailyThrough
    }
    reports = $reports
    report_files = $reportFiles
}
$bundleJson = $bundle | ConvertTo-Json -Depth 100 -Compress

Write-Host "Running final four-domain acceptance gate..."
$jsonLines = $bundleJson | & docker compose run --build --rm --no-deps -T worker python -m app.four_domain_acceptance --stdin
$exitCode = $LASTEXITCODE
$json = $jsonLines -join "`n"
if (-not $json.Trim()) {
    throw "Final four-domain acceptance produced no JSON report."
}

try {
    $report = $json | ConvertFrom-Json
}
catch {
    throw "Final four-domain acceptance produced invalid JSON: $($_.Exception.Message)"
}

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "four_domain_acceptance_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "Four-domain acceptance status: $($report.status)"
Write-Host "Report: $OutputPath"
if ($exitCode -ne 0 -or $report.status -eq "FAIL") {
    throw "Four-domain acceptance failed: $($report.hard_fail_reasons -join ', ')"
}

Write-Host "Final four-domain acceptance complete. Persistent worker remains stopped."
