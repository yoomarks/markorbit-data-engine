param(
    [string]$FileName = "1999.zip"
)

$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "Persistent worker is running. Stop it first: docker compose stop worker"
}

Write-Host "Auditing M1.6 goods item identity collisions for $FileName..."
docker compose run --rm --no-deps worker python -m app.cn.audit_goods_identity $FileName
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 goods item identity audit failed."
}

Write-Host "Goods identity audit complete. Persistent worker remains stopped."
