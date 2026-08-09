$ErrorActionPreference = "Stop"

# Keep the web/API service online. Manual CN ingestion runs in its own one-shot
# worker container, so long imports do not occupy or restart FastAPI.
$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "persistent worker is running. Stop it first: docker compose stop worker"
}

Write-Host "Starting dedicated one-shot CN worker (API stays online)..."
docker compose run --rm --no-deps worker python -m app.cn.run_once
if ($LASTEXITCODE -ne 0) {
    throw "Dedicated CN worker exited with code $LASTEXITCODE."
}
