param(
    [string]$StateDir = "raw_data\ipos_sg"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $hostState = Join-Path $repoRoot $StateDir
    New-Item -ItemType Directory -Force -Path $hostState | Out-Null
    $hostState = (Resolve-Path $hostState).Path

    $appMount = "${repoRoot}\app:/app/app:ro"
    $stateMount = "${hostState}:/app/ipos_sg_state:ro"

    docker compose run --rm --no-deps -T `
        --volume $appMount `
        --volume $stateMount `
        worker python -m app.snapshot_delta.ipos_sg_state `
            --state-dir /app/ipos_sg_state
    if ($LASTEXITCODE -ne 0) {
        throw "Singapore IPOS lifecycle state audit is BLOCKED. No data was modified."
    }

    Write-Host "Singapore IPOS lifecycle state audit completed read-only."
}
finally {
    Pop-Location
}
