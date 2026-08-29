param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [switch]$StopIdleWorker,
    [string]$EvidenceRoot = "reports"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $expectedSha = $ExpectedMainSha.Trim().ToLowerInvariant()

    Write-Host "===== EXACT-MAIN SAFETY GATE ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before target-host preparation."
    }

    $branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        throw "Target-host preparation must run from the local main branch."
    }

    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to fetch origin/main."
    }

    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve exact Data Engine commit identity."
    }

    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expectedSha"

    if ($head -ne $expectedSha -or $originMain -ne $expectedSha) {
        throw "Exact main drift detected. Update local main separately, then re-run this operator with the newly authorized SHA."
    }
    if (git status --porcelain) {
        throw "Working tree changed during the exact-main safety gate."
    }
    Write-Host "EXACT_MAIN_CLEAN_OK"

    Write-Host "`n===== REQUIRED SERVICES ====="
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) {
            throw "$service must be running before target-host preparation."
        }
    }
    Write-Host "POSTGRES_CLICKHOUSE_RUNNING_OK"

    Write-Host "`n===== GLOBAL IDLE WORKER GATE ====="
    $idleArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $PSScriptRoot "stop-idle-worker.ps1")
    )
    if ($StopIdleWorker) {
        $idleArgs += "-StopIdleWorker"
    }
    & powershell.exe @idleArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Global idle-worker gate failed. No storage diagnostic or US mutation was started."
    }

    $workerAfter = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to verify worker state after the global idle-worker gate."
    }
    if ($workerAfter) {
        throw "Persistent worker remains running. Stop is not authorized or did not complete."
    }
    Write-Host "PERSISTENT_WORKER_STOPPED_OK"

    Write-Host "`n===== ACTIVE HOT PERMISSION DIAGNOSTIC ====="
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $evidenceDir = Join-Path $EvidenceRoot "us_capacity_pilot_preparation_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path
    $permissionReport = Join-Path $evidenceDir "active_hot_permission.json"

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "diagnose-clickhouse-active-hot-permissions.ps1") `
        -OutputPath $permissionReport
    if ($LASTEXITCODE -ne 0) {
        throw "Active Hot permission diagnostic failed. No permission repair or US mutation was attempted."
    }
    if (-not (Test-Path -LiteralPath $permissionReport -PathType Leaf)) {
        throw "Active Hot permission diagnostic returned no report."
    }

    Write-Host "`n===== PREPARATION STOP POINT ====="
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host "Permission report: $permissionReport"
    Write-Host "Worker restart: NOT_PERFORMED"
    Write-Host "Permission repair: NOT_PERFORMED"
    Write-Host "US schema apply: NOT_PERFORMED"
    Write-Host "US replay: NOT_PERFORMED"
    Write-Host "Next action: REVIEW_ACTIVE_HOT_PERMISSION_EVIDENCE"
    Write-Host "US_CAPACITY_PILOT_PERMISSION_REVIEW_REQUIRED"
}
finally {
    Pop-Location
}
