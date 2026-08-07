$ErrorActionPreference = "Stop"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Review RAW_DATA_PATH and passwords, then run this script again." -ForegroundColor Yellow
    exit 1
}

docker version
Assert-LastExitCode "docker version"

docker compose version
Assert-LastExitCode "docker compose version"

docker compose up -d --build
Assert-LastExitCode "docker compose up"

Write-Host "MarkOrbit Data Engine started: http://localhost:8080" -ForegroundColor Green
