param(
    [switch]$Deep
)

$ErrorActionPreference = "Stop"

Write-Host "Freezing CN ingestion for deterministic audit..." -ForegroundColor Yellow
docker compose stop worker | Out-Host
if ($LASTEXITCODE -ne 0) { throw "Failed to stop worker" }

Write-Host "Running CN data integrity audit..." -ForegroundColor Cyan
if ($Deep) {
    docker compose exec -T api python -m app.cn.audit_data --deep
} else {
    docker compose exec -T api python -m app.cn.audit_data
}
if ($LASTEXITCODE -ne 0) { throw "CN data integrity audit failed" }

Write-Host "Audit complete. Worker remains stopped until the audit is reviewed." -ForegroundColor Green
