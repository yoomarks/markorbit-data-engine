param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$FileName
)

$ErrorActionPreference = "Stop"

$persistentWorker = docker compose ps --status running --services worker
if ($persistentWorker -match "worker") {
    throw "Persistent worker is running. Stop it first: docker compose stop worker"
}

Write-Host "Requeueing $FileName for deterministic full-package replay..."
docker compose run --rm --no-deps worker python -m app.cn.requeue_package $FileName
if ($LASTEXITCODE -ne 0) {
    throw "Failed to requeue $FileName (exit code $LASTEXITCODE)."
}
