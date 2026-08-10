param(
    [int]$Limit = 3
)

$ErrorActionPreference = "Stop"

if ($Limit -lt 1 -or $Limit -gt 20) {
    throw "Limit must be between 1 and 20."
}

Write-Host "Reading recent ClickHouse query failures (read-only)..."
Write-Host "Ensuring the one-shot worker image contains the current repository code..."
& docker compose run --build --rm --no-deps -T worker python -c "from app.cn.clickhouse_failure import recent_clickhouse_failures; import json; print(json.dumps(recent_clickhouse_failures($Limit), ensure_ascii=False, indent=2))"
if ($LASTEXITCODE -ne 0) {
    throw "CN ClickHouse failure diagnostic exited with code $LASTEXITCODE."
}
