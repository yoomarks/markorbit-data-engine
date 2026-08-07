$ErrorActionPreference = "Stop"

Write-Host "M1.5.3 contract preflight..." -ForegroundColor Cyan

docker compose ps
if ($LASTEXITCODE -ne 0) { throw "docker compose ps failed" }

docker compose exec -T api python -m app.cn.validate_contract
if ($LASTEXITCODE -ne 0) { throw "M1.5.3 contract preflight failed" }

Write-Host "M1.5.3 contract preflight passed. Next run validate-cn-fixture.ps1; do NOT run a real ZIP yet." -ForegroundColor Green
