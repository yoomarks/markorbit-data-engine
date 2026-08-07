$ErrorActionPreference = "Stop"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

Write-Host "M1.5 requires clean DEV database volumes." -ForegroundColor Yellow
Write-Host "PostgreSQL and ClickHouse volumes will be removed. raw_data is not removed." -ForegroundColor Yellow

docker compose down -v
Assert-LastExitCode "docker compose down -v"

docker compose up -d --build
Assert-LastExitCode "docker compose up"

Write-Host "M1.5 environment recreated: http://localhost:8080" -ForegroundColor Green
