param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
        throw "US capacity projection input not found: $InputPath"
    }
    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "us_full_corpus_capacity_projection_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }

    $args = @(
        "run", "--rm", "--no-deps", "-T",
        "--volume", "${repoRoot}\app:/app/app:ro",
        "--volume", "${repoRoot}\${InputPath}:/projection/input.json:ro",
        "--volume", "${repoRoot}\${outputDirectory}:/projection/output",
        "worker", "python", "-m", "app.us_capacity_projection",
        "--input", "/projection/input.json",
        "--output", "/projection/output/$([System.IO.Path]::GetFileName($OutputPath))"
    )

    $stdout = & docker compose @args
    $exitCode = $LASTEXITCODE
    if ($stdout) { $stdout | Write-Host }

    if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "US capacity projection produced no report: $OutputPath"
    }
    $report = Get-Content -LiteralPath $OutputPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host "US full-corpus capacity projection: $($report.status)"
    Write-Host "Full-corpus import authorized: $($report.full_corpus_import_authorized)"
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0) {
        throw "US full-corpus capacity gate did not authorize import. Status=$($report.status)"
    }
    if ($report.status -ne "GO" -or -not $report.full_corpus_import_authorized) {
        throw "US full-corpus capacity gate failed closed despite zero process exit."
    }
    Write-Host "US_FULL_CORPUS_CAPACITY_GO"
}
finally {
    Pop-Location
}
