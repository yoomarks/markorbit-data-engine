param(
    [string]$StateDir = "raw_data\ipos_sg",
    [switch]$RecoverStaleLock
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not $env:DATA_GOV_SG_API_KEY) {
        throw "DATA_GOV_SG_API_KEY must be set in the current PowerShell session."
    }

    $runningWorker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($runningWorker) {
        throw "A worker container is already running. Finish or stop it before the Singapore IPOS full-corpus run."
    }

    $hostState = Join-Path $repoRoot $StateDir
    New-Item -ItemType Directory -Force -Path $hostState | Out-Null
    $hostState = (Resolve-Path $hostState).Path

    $appMount = "${repoRoot}\app:/app/app:ro"
    $stateMount = "${hostState}:/app/ipos_sg_state"
    $operatorArgs = @(
        "-m",
        "app.snapshot_delta.ipos_sg_operator",
        "--state-dir",
        "/app/ipos_sg_state"
    )
    if ($RecoverStaleLock) {
        $operatorArgs += "--recover-stale-lock"
    }

    Write-Host "Running leased authenticated Singapore IPOS operator cycle..."
    Write-Host "The same one-shot worker remains alive for preflight, source authentication, full corpus, and postflight."
    docker compose run --rm --no-deps -T `
        --env DATA_GOV_SG_API_KEY `
        --volume $appMount `
        --volume $stateMount `
        worker python @operatorArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Singapore IPOS operator cycle failed. Accepted state, failure evidence, and any recoverable lifecycle artifacts remain in $hostState."
    }

    Write-Host "Singapore IPOS authenticated operator acceptance: PASS"
    Write-Host "State: $hostState"
    Write-Host "Corpus report: $(Join-Path $hostState 'acceptance\latest.json')"
    Write-Host "Operator report: $(Join-Path $hostState 'acceptance\operator_latest.json')"
}
finally {
    Pop-Location
}
