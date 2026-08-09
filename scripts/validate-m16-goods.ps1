$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "Persistent worker is running. Stop it first: docker compose stop worker"
}

Write-Host "Running M1.6 CN goods lifecycle fixture..."
$python = "from app.cn import goods_lifecycle as g; from app.cn.goods_lifecycle_sql import incoming_goods_sql; g.incoming_goods_sql = incoming_goods_sql; from app.cn.validate_goods_lifecycle import main; main()"
docker compose run --rm --no-deps worker python -c $python
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 CN goods lifecycle fixture failed."
}

Write-Host "M1.6 goods lifecycle fixture complete. Persistent worker remains stopped."
