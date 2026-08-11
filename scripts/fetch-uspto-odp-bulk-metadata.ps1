param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("assignment", "ttab")]
    [string]$Domain,

    [string]$MetadataOutputPath = "",

    [string]$FetchReportOutputPath = ""
)

$ErrorActionPreference = "Stop"

function Write-Utf8NoBomAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $directory = Split-Path -Parent $resolvedPath
    if ($directory) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $temporaryPath = "$resolvedPath.tmp.$PID"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temporaryPath, $Content, $utf8NoBom)
        if (Test-Path -LiteralPath $resolvedPath -PathType Leaf) {
            [System.IO.File]::Replace($temporaryPath, $resolvedPath, $null)
        }
        else {
            [System.IO.File]::Move($temporaryPath, $resolvedPath)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

Write-Host "Fetching authoritative USPTO ODP Product Data metadata for $Domain..."
Write-Host "API key material is read only from the worker environment and is never printed."

$jsonLines = & docker compose run --build --rm --no-deps -T worker `
    python -m app.uspto_odp_metadata_fetch --domain $Domain
$exitCode = $LASTEXITCODE
$json = $jsonLines -join "`n"
if (-not $json.Trim()) {
    throw "USPTO ODP metadata fetch produced no JSON report."
}

try {
    $report = $json | ConvertFrom-Json
}
catch {
    throw "USPTO ODP metadata fetch produced invalid JSON: $($_.Exception.Message)"
}

if ($exitCode -ne 0 -or -not $report.safe) {
    $issueType = $report.issue.type
    $issueMessage = $report.issue.message
    throw "USPTO ODP metadata fetch failed: $issueType - $issueMessage"
}
if (-not $report.metadata) {
    throw "USPTO ODP metadata fetch report did not contain metadata."
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
if (-not $MetadataOutputPath) {
    $MetadataOutputPath = Join-Path "reports" "uspto_odp_${Domain}_metadata_$timestamp.json"
}
if (-not $FetchReportOutputPath) {
    $FetchReportOutputPath = Join-Path "reports" "uspto_odp_${Domain}_metadata_fetch_$timestamp.json"
}

$metadataJson = $report.metadata | ConvertTo-Json -Depth 100
Write-Utf8NoBomAtomic -Path $MetadataOutputPath -Content $metadataJson

$report.PSObject.Properties.Remove("metadata")
$reportJson = $report | ConvertTo-Json -Depth 100
Write-Utf8NoBomAtomic -Path $FetchReportOutputPath -Content $reportJson

Write-Host "ODP metadata fetch status: $($report.status)"
Write-Host "Dataset: $($report.odp_dataset_slug)"
Write-Host "Response bytes: $($report.response_byte_count)"
Write-Host "Response SHA-256: $($report.response_sha256)"
Write-Host "Metadata: $([System.IO.Path]::GetFullPath($MetadataOutputPath))"
Write-Host "Fetch report: $([System.IO.Path]::GetFullPath($FetchReportOutputPath))"
Write-Host "No database, raw source package, replay, ingestion, or manifest was modified."
