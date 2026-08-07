param(
    [string]$FileName = "1999.zip"
)

$ErrorActionPreference = "Stop"

Write-Host "Auditing M1.6 goods item identity collisions for $FileName..."
docker compose exec -T api python -m app.cn.audit_goods_identity $FileName
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 goods item identity audit failed."
}
