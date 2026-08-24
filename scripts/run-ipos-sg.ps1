param(
    [string]$StateDir = "raw_data\ipos_sg"
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

    Write-Host "Running authenticated Singapore IPOS live-source acceptance..."
    docker compose run --rm --no-deps -T `
        --env DATA_GOV_SG_API_KEY `
        --volume $appMount `
        worker python -m app.snapshot_delta.ipos_sg_acceptance --resolve-download-url
    if ($LASTEXITCODE -ne 0) {
        throw "Singapore IPOS live-source acceptance failed; full-corpus acquisition was not started."
    }

    Write-Host "Running authenticated Singapore IPOS full-corpus lifecycle..."
    docker compose run --rm --no-deps -T `
        --env DATA_GOV_SG_API_KEY `
        --volume $appMount `
        --volume $stateMount `
        worker python -m app.snapshot_delta.ipos_sg_full_acceptance `
            --state-dir /app/ipos_sg_state `
            --report-path /app/ipos_sg_state/acceptance/latest.json
    if ($LASTEXITCODE -ne 0) {
        throw "Singapore IPOS full-corpus acceptance failed. Accepted state, if any, remains in $hostState for safe recovery."
    }

    Write-Host "Singapore IPOS authenticated source + full-corpus acceptance: PASS"
    Write-Host "State: $hostState"
    Write-Host "Report: $(Join-Path $hostState 'acceptance\latest.json')"
}
finally {
    Pop-Location
}
