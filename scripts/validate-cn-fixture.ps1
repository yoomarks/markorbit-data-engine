$ErrorActionPreference = "Stop"

Write-Host "M1.5.3.1 non-empty runtime fixture..." -ForegroundColor Cyan

docker compose exec -T api python -m app.cn.validate_fixture
if ($LASTEXITCODE -ne 0) { throw "M1.5.3.1 runtime fixture failed" }

Write-Host "M1.5.3.1 runtime fixture passed. Real ZIP import may proceed." -ForegroundColor Green
