$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "Persistent worker is running. Stop it first: docker compose stop worker"
}

Write-Host "Verifying active M1.6 goods identity runtime..."
docker compose run --rm --no-deps worker python -m app.cn.verify_m16_runtime
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 runtime identity verification failed."
}

Write-Host "M1.6 runtime identity verification complete. Persistent worker remains stopped."
