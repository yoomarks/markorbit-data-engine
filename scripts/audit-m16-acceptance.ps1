$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "Persistent worker is running. Stop it first: docker compose stop worker"
}

Write-Host "Running M1.6 acceptance integrity audit in a one-shot worker..." -ForegroundColor Cyan
docker compose run --rm --no-deps worker python -m app.cn.audit_acceptance
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 acceptance integrity audit failed"
}

Write-Host "Acceptance audit complete. Persistent worker remains stopped." -ForegroundColor Green
