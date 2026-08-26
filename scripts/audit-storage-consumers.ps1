param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "storage_consumers_$timestamp.json"
    }

    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }

    python -m app.storage_consumer_inventory `
        --root $repoRoot `
        --output $OutputPath `
        --compact

    if ($LASTEXITCODE -ne 0) {
        throw "Storage consumer contract audit failed with exit code $LASTEXITCODE"
    }

    Write-Host "Storage consumer contract: PASS"
    Write-Host "Report: $OutputPath"
}
finally {
    Pop-Location
}
