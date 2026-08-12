param(
    [switch]$ResumeFailed,
    [int]$MaxPackages = 0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "replay-telemetry.ps1")

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "persistent worker is running. Stop it first: docker compose stop worker"
}

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "assert-storage-headroom.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Storage headroom gate blocked CN full replay."
}

$argsList = @(
    "compose", "run", "--build", "--rm", "--no-deps", "-T",
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
Write-Host "CN replay builds the current one-shot worker image, uses disk-spilling grace-hash joins and a 3600-second ClickHouse HTTP wait; other Data Engine domains keep their default JOIN algorithm and timeout."
if ($ResumeFailed) {
    Write-Host "FAILED/MISSING_FILE barrier repair is explicitly enabled."
}

$telemetry = $null
$telemetryStatus = "NOT_RECORDED"
$telemetryError = ""
try {
    $telemetry = Start-DataEngineReplayTelemetry `
        -Domain "CN" `
        -Jurisdiction "CN" `
        -CommandName "replay-cn-full.ps1"
    $telemetryStatus = "COMMAND_RUNNING"
}
catch {
    Write-Warning "Replay telemetry start failed without blocking CN replay: $($_.Exception.Message)"
}

try {
    $replayStartedUtc = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ")
    & docker @argsList
    $replayExitCode = $LASTEXITCODE
    if ($replayExitCode -ne 0) {
        Write-Host "CN replay failed. Reading ClickHouse query failures from this replay window only..."
        & docker compose run --build --rm --no-deps -T worker python -m app.cn.clickhouse_failure `
            --since-utc $replayStartedUtc
        throw "CN full replay exited with code $replayExitCode. The ClickHouse diagnostic above is scoped to this replay; failure_count=0 means the package failed outside captured ClickHouse query_log and the CN_FULL_REPLAY_PACKAGE error above is authoritative."
    }
    if ($telemetry) {
        $telemetryStatus = "COMMAND_SUCCEEDED"
    }
}
catch {
    if ($telemetry) {
        $telemetryStatus = "COMMAND_FAILED"
        $telemetryError = $_.Exception.Message
    }
    throw
}
finally {
    if ($telemetry) {
        Complete-DataEngineReplayTelemetry `
            -Context $telemetry `
            -Status $telemetryStatus `
            -ErrorMessage $telemetryError
    }
}
