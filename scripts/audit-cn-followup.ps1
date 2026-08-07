$ErrorActionPreference = "Stop"

Write-Host "Running focused CN integrity follow-up..." -ForegroundColor Cyan

docker compose exec -T api python -m app.cn.audit_followup
if ($LASTEXITCODE -ne 0) {
    throw "CN integrity follow-up failed"
}

Write-Host "Follow-up audit complete." -ForegroundColor Green
