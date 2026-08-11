param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("assignment", "ttab")]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$MetadataPath,

    [Parameter(Mandatory = $true)]
    [string[]]$ExpectedFileName,

    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before authoritative ODP metadata preflight."
}

if (-not (Test-Path -LiteralPath $MetadataPath -PathType Leaf)) {
    throw "ODP metadata JSON not found: $MetadataPath"
}
$metadataRaw = Get-Content -LiteralPath $MetadataPath -Raw -Encoding UTF8
try {
    $metadata = $metadataRaw | ConvertFrom-Json
}
catch {
    throw "ODP metadata file is invalid JSON: $MetadataPath"
}

$bundle = @{
    domain = $Domain
    expected_file_names = @($ExpectedFileName)
    metadata = $metadata
}
$bundleJson = $bundle | ConvertTo-Json -Depth 100 -Compress

Write-Host "Running authoritative USPTO ODP metadata preflight for $Domain..."
$jsonLines = $bundleJson | & docker compose run --build --rm --no-deps -T worker python -m app.uspto_odp_bulk_metadata --stdin
$exitCode = $LASTEXITCODE
$json = $jsonLines -join "`n"
if (-not $json.Trim()) {
    throw "USPTO ODP metadata preflight produced no JSON report."
}

try {
    $report = $json | ConvertFrom-Json
}
catch {
    throw "USPTO ODP metadata preflight produced invalid JSON: $($_.Exception.Message)"
}

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "uspto_odp_${Domain}_metadata_preflight_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "ODP metadata preflight status: $($report.status)"
Write-Host "Resolved files: $($report.resolved_file_count)/$($report.expected_file_count)"
Write-Host "Report: $OutputPath"

if ($exitCode -ne 0 -or -not $report.safe) {
    $issueTypes = @($report.issues | ForEach-Object { $_.type })
    throw "USPTO ODP metadata preflight not ready: $($issueTypes -join ', ')"
}

Write-Host "Authoritative ODP metadata preflight complete. Persistent worker remains stopped."
