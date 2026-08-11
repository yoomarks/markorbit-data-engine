param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("assignment", "ttab")]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$MetadataPath,

    [Parameter(Mandatory = $true)]
    [string]$SourceSpecPath,

    [switch]$Apply,

    [string]$ManifestOutputPath = "",

    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running -q worker
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Docker Compose worker state."
}
if ($worker) {
    throw "Persistent worker is running. Stop it before ODP corpus manifest generation."
}

foreach ($path in @($MetadataPath, $SourceSpecPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required JSON input not found: $path"
    }
}

try {
    $metadata = (Get-Content -LiteralPath $MetadataPath -Raw -Encoding UTF8) | ConvertFrom-Json
    $sourceSpec = (Get-Content -LiteralPath $SourceSpecPath -Raw -Encoding UTF8) | ConvertFrom-Json
}
catch {
    throw "ODP metadata/source specification is invalid JSON: $($_.Exception.Message)"
}

$bundle = @{
    domain = $Domain
    metadata = $metadata
    sources = @($sourceSpec.sources)
}
$bundleJson = $bundle | ConvertTo-Json -Depth 100 -Compress

Write-Host "Building $Domain corpus manifest from authoritative ODP metadata..."
$jsonLines = $bundleJson | & docker compose run --build --rm --no-deps -T worker python -m app.uspto_odp_manifest_builder --stdin
$exitCode = $LASTEXITCODE
$json = $jsonLines -join "`n"
if (-not $json.Trim()) {
    throw "ODP corpus manifest builder produced no JSON report."
}

try {
    $report = $json | ConvertFrom-Json
}
catch {
    throw "ODP corpus manifest builder produced invalid JSON: $($_.Exception.Message)"
}

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path "reports" "uspto_odp_${Domain}_manifest_build_$timestamp.json"
}
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$json | Set-Content -Encoding UTF8 $OutputPath

Write-Host "Manifest build status: $($report.status)"
Write-Host "Report: $OutputPath"

if ($exitCode -ne 0 -or -not $report.safe) {
    $issueTypes = @($report.issues | ForEach-Object { $_.type })
    throw "ODP corpus manifest is not ready: $($issueTypes -join ', ')"
}

if (-not $Apply) {
    Write-Host "Dry run only. Pass -Apply with -ManifestOutputPath to write the manifest."
    Write-Output ($report.manifest | ConvertTo-Json -Depth 100)
    exit 0
}

if (-not $ManifestOutputPath) {
    throw "-ManifestOutputPath is required when -Apply is used."
}
$manifestDirectory = Split-Path -Parent $ManifestOutputPath
if ($manifestDirectory) {
    New-Item -ItemType Directory -Force -Path $manifestDirectory | Out-Null
}
$manifestJson = $report.manifest | ConvertTo-Json -Depth 100
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path (Get-Location) $ManifestOutputPath), $manifestJson, $utf8NoBom)

Write-Host "Manifest written: $ManifestOutputPath"
Write-Host "Persistent worker remains stopped. No replay or ingestion was started."
