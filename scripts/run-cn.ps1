$ErrorActionPreference = "Stop"

# Real import is manually deterministic. The worker must not race this command.
$worker = docker compose ps --status running --services worker
if ($worker -match "worker") {
    throw "worker is running. Stop it first: docker compose stop worker"
}

# Manual multi-hour ingestion must not live inside a FastAPI HTTP request.
# Stop the API first so an abandoned/stale request cannot overlap the one-shot
# process. PostgreSQL/ClickHouse remain running and all persisted data is kept.
$apiWasRunning = docker compose ps --status running --services api
if ($apiWasRunning -match "api") {
    Write-Host "Stopping API to guarantee a single CN ingestion process..."
    docker compose stop api | Out-Host
}

try {
    Write-Host "Starting dedicated one-shot CN ingestion..."
    docker compose run --rm --no-deps api python -m app.cn.run_once
    if ($LASTEXITCODE -ne 0) {
        throw "Dedicated CN ingestion exited with code $LASTEXITCODE."
    }
}
finally {
    Write-Host "Starting API..."
    docker compose up -d api | Out-Host
}
