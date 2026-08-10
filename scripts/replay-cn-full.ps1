param(
    [switch]$ResumeFailed,
    [int]$MaxPackages = 0
)

$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "persistent worker is running. Stop it first: docker compose stop worker"
}

$argsList = @(
    "compose", "run", "--rm", "--no-deps", "-T",
    "--env", "CLICKHOUSE_JOIN_ALGORITHM=grace_hash",
    "--env", "CLICKHOUSE_GRACE_HASH_JOIN_INITIAL_BUCKETS=32",
    "--env", "CLICKHOUSE_SEND_RECEIVE_TIMEOUT=3600",
    "worker", "python", "-m", "app.cn.full_replay"
)

if ($ResumeFailed) {
    $argsList += "--resume-failed"
}
if ($MaxPackages -gt 0) {
    $argsList += @("--max-packages", "$MaxPackages")
}

Write-Host "Starting deterministic CN full-corpus replay..."
Write-Host "CN replay uses disk-spilling grace-hash joins and a 3600-second ClickHouse HTTP wait; other Data Engine domains keep their default JOIN algorithm and timeout."
if ($ResumeFailed) {
    Write-Host "FAILED/MISSING_FILE barrier repair is explicitly enabled."
}

& docker @argsList
if ($LASTEXITCODE -ne 0) {
    throw "CN full replay exited with code $LASTEXITCODE. See the JSON event above for the exact package error."
}
