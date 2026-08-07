$ErrorActionPreference = "Stop"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

Write-Host "M1.5 deterministic DEV reset." -ForegroundColor Yellow
Write-Host "PostgreSQL and ClickHouse volumes will be removed. raw_data is not removed." -ForegroundColor Yellow
Write-Host "Worker is intentionally NOT started during validation." -ForegroundColor Yellow

docker compose down -v
Assert-LastExitCode "docker compose down -v"

docker compose up -d --build postgres clickhouse api
Assert-LastExitCode "docker compose up postgres clickhouse api"

Write-Host "Validation environment recreated: http://localhost:8080" -ForegroundColor Green
Write-Host "Next: validate-cn-contract.ps1, then validate-cn-fixture.ps1." -ForegroundColor Green
