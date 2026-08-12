$ErrorActionPreference = "Stop"

# Keep the web/API service online. Manual CN ingestion runs in its own one-shot
# worker container, so long imports do not occupy or restart FastAPI.
$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "persistent worker is running. Stop it first: docker compose stop worker"
}

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "assert-storage-headroom.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Storage headroom gate blocked guarded CN ingestion."
}

Write-Host "Starting guarded one-shot CN worker (API stays online)..."
Write-Host "First clean run requires M1.6 preflight + deterministic replay plan; continuation runs require the durable-goods replay boundary."
docker compose run --rm --no-deps worker python -m app.cn.guarded_run_once
if ($LASTEXITCODE -ne 0) {
    throw "Guarded CN worker exited with code $LASTEXITCODE."
}
