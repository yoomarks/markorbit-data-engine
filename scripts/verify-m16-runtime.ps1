$ErrorActionPreference = "Stop"

Write-Host "Verifying active M1.6 goods identity runtime..."
docker compose exec -T api python -m app.cn.verify_m16_runtime
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 runtime identity verification failed."
}
