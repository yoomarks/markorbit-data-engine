$ErrorActionPreference = "Stop"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
if (-not (Test-Path $envFile)) {
    throw "Missing .env file: $envFile"
}

$rawLine = Get-Content $envFile | Where-Object { $_ -match '^RAW_DATA_PATH=' } | Select-Object -First 1
if (-not $rawLine) {
    throw "RAW_DATA_PATH is not defined in .env"
}
$rawPath = ($rawLine -split '=', 2)[1].Trim().Trim('"').Trim("'")
if (-not [System.IO.Path]::IsPathRooted($rawPath)) {
    $rawPath = Join-Path $repoRoot $rawPath
}

$incoming = Join-Path $rawPath "incoming\cn"
$archive = Join-Path $rawPath "archive\cn"
New-Item -ItemType Directory -Force -Path $incoming | Out-Null
New-Item -ItemType Directory -Force -Path $archive | Out-Null

Write-Host "M1.6 deterministic DEV reset + goods replay preparation." -ForegroundColor Yellow
Write-Host "PostgreSQL and ClickHouse volumes will be removed. Raw ZIP files are preserved." -ForegroundColor Yellow
Write-Host "Worker is intentionally NOT started." -ForegroundColor Yellow

docker compose down -v
Assert-LastExitCode "docker compose down -v"

# Successful packages are normally moved to archive/cn. Copy them back to
# incoming/cn so the clean M1.6 database can rebuild durable item history from
# authoritative raw sources. Ingestion will deduplicate the identical archive
# copy again after success.
if (Test-Path $archive) {
    Get-ChildItem -Path $archive -File -Filter *.zip | Sort-Object Name | ForEach-Object {
        $destination = Join-Path $incoming $_.Name
        if (-not (Test-Path $destination)) {
            Copy-Item -LiteralPath $_.FullName -Destination $destination
            Write-Host "Replay queued: $($_.Name)"
        }
    }
}

docker compose up -d --build postgres clickhouse api
Assert-LastExitCode "docker compose up postgres clickhouse api"

Write-Host "M1.6 validation environment ready: http://localhost:8080" -ForegroundColor Green
Write-Host "Next: validate-m16.ps1, validate-cn-contract.ps1, validate-cn-fixture.ps1, validate-m16-goods.ps1." -ForegroundColor Green
Write-Host "Only after all gates pass should real CN packages be replayed." -ForegroundColor Green
