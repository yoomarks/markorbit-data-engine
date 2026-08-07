$ErrorActionPreference = "Stop"

Write-Host "Running M1.6 CN goods lifecycle fixture..."
docker compose exec -T api python -m app.cn.validate_goods_lifecycle
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 CN goods lifecycle fixture failed."
}

Write-Host "M1.6 goods lifecycle fixture complete."
