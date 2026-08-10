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
    "worker", "python", "-m", "app.cn.full_replay"
)

if ($ResumeFailed) {
    $argsList += "--resume-failed"
}
if ($MaxPackages -gt 0) {
    $argsList += @("--max-packages", "$MaxPackages")
}

Write-Host "Starting deterministic CN full-corpus replay..."
if ($ResumeFailed) {
    Write-Host "FAILED/MISSING_FILE barrier repair is explicitly enabled."
}

& docker @argsList
if ($LASTEXITCODE -ne 0) {
    throw "CN full replay exited with code $LASTEXITCODE. See the JSON event above for the exact package error."
}
