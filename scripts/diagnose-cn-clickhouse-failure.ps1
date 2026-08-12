param(
    [int]$Limit = 3,
    [string]$SinceUtc = ""
)

$ErrorActionPreference = "Stop"

if ($Limit -lt 1 -or $Limit -gt 20) {
    throw "Limit must be between 1 and 20."
}

Write-Host "Reading recent ClickHouse query failures (read-only)..."
if ($SinceUtc) {
    Write-Host "Only failures at or after $SinceUtc will be returned."
}
Write-Host "Ensuring the one-shot worker image contains the current repository code..."

$argsList = @(
    "compose", "run", "--build", "--rm", "--no-deps", "-T",
    "worker", "python", "-m", "app.cn.clickhouse_failure",
    "--limit", "$Limit"
)
if ($SinceUtc) {
    $argsList += @("--since-utc", $SinceUtc)
}

& docker @argsList
if ($LASTEXITCODE -ne 0) {
    throw "CN ClickHouse failure diagnostic exited with code $LASTEXITCODE."
}
