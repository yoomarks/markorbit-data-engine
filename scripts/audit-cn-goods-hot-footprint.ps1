param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $clickhouseId = docker compose ps --status running -q clickhouse
    if ($LASTEXITCODE -ne 0 -or -not $clickhouseId) {
        throw "ClickHouse must be running before the metadata-only goods Hot footprint audit."
    }

    $auditArgs = @(
        "run", "--rm", "--no-deps", "-T",
        "--volume", "${repoRoot}\app:/app/app:ro",
        "worker", "python", "-m", "app.storage_goods_hot_footprint",
        "--root", "/app",
        "--compact"
    )
    $jsonLines = & docker compose @auditArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"
    if (-not $json.Trim()) {
        throw "CN goods Hot footprint audit produced no JSON report."
    }
    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "CN goods Hot footprint audit produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "cn_goods_hot_footprint_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "CN goods Hot footprint status: $($report.status)"
    Write-Host "Columns: $($report.totals.column_count)"
    Write-Host "Compressed column bytes: $($report.totals.data_compressed_bytes)"
    Write-Host "Compatibility decision: $($report.compatibility_decision)"
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0 -or $report.status -ne "PASS") {
        throw "CN goods Hot footprint audit requires review; no storage migration is authorized."
    }
}
finally {
    Pop-Location
}
