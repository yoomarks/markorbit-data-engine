$ErrorActionPreference = "Stop"

Write-Host "Running M1.6 CN goods lifecycle fixture..."
$python = "from app.cn import goods_lifecycle as g; from app.cn.goods_lifecycle_sql import incoming_goods_sql; g.incoming_goods_sql = incoming_goods_sql; from app.cn.validate_goods_lifecycle import main; main()"
docker compose exec -T api python -c $python
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 CN goods lifecycle fixture failed."
}

Write-Host "M1.6 goods lifecycle fixture complete."
