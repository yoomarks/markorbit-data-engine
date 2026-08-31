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
    if (git status --porcelain) { throw "Working tree must be clean before target-host preparation." }
    $branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") { throw "Target-host preparation must run from local main." }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Unable to fetch origin/main." }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expectedSha"
    if ($head -ne $expectedSha -or $originMain -ne $expectedSha) { throw "Exact main drift detected." }
    if (git status --porcelain) { throw "Working tree changed during exact-main gate." }
    Write-Host "EXACT_MAIN_CLEAN_OK"

    Write-Host "`n===== REQUIRED SERVICES / GLOBAL IDLE ====="
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) { throw "$service must be running before target-host preparation." }
    }
    $idleArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'stop-idle-worker.ps1'))
    if ($StopIdleWorker) { $idleArgs += '-StopIdleWorker' }
    & powershell.exe @idleArgs
    if ($LASTEXITCODE -ne 0) { throw "Global idle-worker gate failed." }
    $workerAfter = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($workerAfter.Count -ne 0) { throw "Worker container in any state is not allowed." }
    Write-Host "GLOBAL_IDLE_ZERO_WORKER_OK"

    Write-Host "`n===== LINUX DATA-VOLUME STORAGE CONTRACT ====="
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "us_capacity_pilot_preparation_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path
    $storageReport = Join-Path $evidenceDir 'active_data_storage_contract.json'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot 'assert-clickhouse-active-hot-storage-contract.ps1') `
        -OutputPath $storageReport
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $storageReport -PathType Leaf)) {
        throw "Linux ClickHouse data-volume contract failed. No US mutation was attempted."
    }
    $storage = Get-Content -LiteralPath $storageReport -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($storage.report_version -ne 'CLICKHOUSE_ACTIVE_DATA_STORAGE_CONTRACT_V2' -or
        @($storage.blockers).Count -ne 0 -or
        -not [bool]$storage.safe_for_clickhouse_merge_tree_writes -or
        [bool]$storage.windows_host_bind_accepted) {
        throw "Active ClickHouse data storage is not accepted for US rollout."
    }

    Write-Host "`n===== PREPARATION STOP POINT ====="
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host "Storage report: $storageReport"
    Write-Host "Worker restart: NOT_PERFORMED"
    Write-Host "US schema apply: NOT_PERFORMED"
    Write-Host "US replay: NOT_PERFORMED"
    Write-Host "Next action: LINUX_VOLUME_STORAGE_ACCEPTED_REVIEW_US_TRANSITION"
    Write-Host "US_CAPACITY_PILOT_STORAGE_CONTRACT_READY"
}
finally {
    Pop-Location
}
