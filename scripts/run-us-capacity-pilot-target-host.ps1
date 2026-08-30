param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts = 91,
    [ValidateRange(1, 1000000)]
    [int]$ExpectedSequence = 1,
    [string]$ExpectedFileName = "apc18840407-20251231-01.zip",
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedPackageSha256 = "9b65bdcb80c2bdd6efa6869432771c30613bed6dc8efd3d4589e2fd8b334b062",
    [ValidateRange(0, 1000000)]
    [int]$ExpectedRemainingBefore = 310,
    [ValidateRange(0, 1000000)]
    [int]$ExpectedRemainingAfter = 309,
    [ValidateRange(30, 1800)]
    [int]$ClickHouseHealthTimeoutSeconds = 600,
    [string]$EvidenceRoot = "reports"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $expectedSha = $ExpectedMainSha.Trim().ToLowerInvariant()
    $expectedPackageSha = $ExpectedPackageSha256.Trim().ToLowerInvariant()

    function Get-RunningClickHouseContainerId {
        $ids = @(& docker compose ps --status running -q clickhouse 2>$null | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect ClickHouse container state."
        }
        if ($ids.Count -ne 1) {
            throw "Exactly one running ClickHouse container is required for the bounded US target-host pilot."
        }
        return $ids[0].Trim()
    }

    function Wait-ClickHouseHealthy([string]$Phase) {
        $deadline = (Get-Date).AddSeconds($ClickHouseHealthTimeoutSeconds)
        $lastHealth = "unknown"
        do {
            $containerId = Get-RunningClickHouseContainerId
            $healthLines = @(& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $containerId 2>$null)
            if ($LASTEXITCODE -eq 0) {
                $values = @($healthLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
                if ($values.Count -eq 1) {
                    $lastHealth = $values[0].Trim().ToLowerInvariant()
                    if ($lastHealth -eq "healthy") {
                        Write-Host "clickhouse_health_phase=$Phase"
                        Write-Host "clickhouse_docker_health=healthy"
                        return
                    }
                }
            }
            Start-Sleep -Seconds 2
        } while ((Get-Date) -lt $deadline)
        throw "ClickHouse did not become healthy during $Phase. last_health=$lastHealth"
    }

    function Assert-NoWorkerContainers {
        $workerIds = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect worker containers."
        }
        Write-Host "worker_container_count_all_states=$($workerIds.Count)"
        if ($workerIds.Count -ne 0) {
            throw "No worker container in any state is allowed at a bounded target-host pilot gate."
        }
    }

    function Assert-ExactMain([string]$Phase) {
        $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
        $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to resolve exact main identity during $Phase."
        }
        Write-Host "exact_main_phase=$Phase"
        Write-Host "HEAD=$head"
        Write-Host "origin/main=$originMain"
        Write-Host "expected=$expectedSha"
        if ($head -ne $expectedSha -or $originMain -ne $expectedSha) {
            throw "Exact main drift detected during $Phase."
        }
        if (git status --porcelain) {
            throw "Working tree is dirty during $Phase."
        }
    }

    function Invoke-HotV2Gate([string]$OutputPath, [string]$Phase) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
            (Join-Path $PSScriptRoot "diagnose-clickhouse-active-hot-permissions-v2.ps1") `
            -OutputPath $OutputPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
            throw "Active-Hot V2 failed during $Phase."
        }
        $hot = Get-Content -LiteralPath $OutputPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($hot.report_version -ne "CLICKHOUSE_ACTIVE_HOT_PERMISSION_DIAGNOSTIC_V1" -or `
            @($hot.blockers).Count -ne 0 -or `
            -not [bool]$hot.schema_version.rwx_for_server_identity -or `
            @($hot.schema_version.tmp_insert_dirs).Count -ne 0 -or `
            -not [bool]$hot.disposable_root_rename_probe.passed -or `
            -not [bool]$hot.cn_comparison.rwx_for_server_identity) {
            throw "Active-Hot V2 is not fully healthy during $Phase."
        }
        Write-Host "active_hot_phase=$Phase"
        Write-Host "active_hot_blockers=0"
        Write-Host "active_hot_schema_rwx=True"
        Write-Host "active_hot_tmp_insert_dirs=0"
        Write-Host "active_hot_root_rename=True"
        Write-Host "active_hot_cn_comparison_rwx=True"
    }

    Write-Host "===== EXACT-MAIN SINGLE-PROCESS SAFETY GATE ====="
    if (git status --porcelain) {
        throw "Working tree must be clean before the bounded US target-host pilot."
    }
    $branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        throw "Bounded US target-host pilot must run from local main."
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to fetch origin/main."
    }
    Assert-ExactMain "entry"
    Write-Host "EXACT_MAIN_BOUNDED_US_PILOT_OK"

    Write-Host "`n===== REQUIRED SERVICES / GLOBAL IDLE ====="
    foreach ($service in @("postgres", "clickhouse")) {
        $running = docker compose ps --status running -q $service
        if ($LASTEXITCODE -ne 0 -or -not $running) {
            throw "$service must be running before the bounded US target-host pilot."
        }
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "stop-idle-worker.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Global Data Engine idle gate failed."
    }
    Assert-NoWorkerContainers
    Wait-ClickHouseHealthy "pre-schema"
    Write-Host "GLOBAL_IDLE_ZERO_WORKER_CLICKHOUSE_HEALTHY_OK"

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $evidenceDir = Join-Path $EvidenceRoot "us_capacity_pilot_target_host_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path
    $hotBeforePath = Join-Path $evidenceDir "active_hot_before_schema.json"
    $hotAfterPath = Join-Path $evidenceDir "active_hot_after_schema.json"
    $transitionPath = Join-Path $evidenceDir "transition.json"

    Write-Host "`n===== ACTIVE HOT PRE-SCHEMA GATE ====="
    Invoke-HotV2Gate $hotBeforePath "pre-schema"
    Write-Host "ACTIVE_HOT_PRE_SCHEMA_OK"

    Write-Host "`n===== APPLY US M1.4 SCHEMA ONLY ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "apply-us-m1-schema.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "US M1.4 schema apply failed. No package replay was started."
    }
    Assert-NoWorkerContainers
    Wait-ClickHouseHealthy "post-schema"
    Write-Host "US_M14_SCHEMA_APPLY_OK"

    Write-Host "`n===== ACTIVE HOT POST-SCHEMA GATE ====="
    Invoke-HotV2Gate $hotAfterPath "post-schema"
    Write-Host "ACTIVE_HOT_POST_SCHEMA_OK"

    Write-Host "`n===== US APPLICATION TRANSITION READY GATE ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "check-us-application-transition.ps1") `
        -ExpectedHistoryParts $ExpectedHistoryParts `
        -OutputPath $transitionPath
    if ($LASTEXITCODE -ne 0) {
        throw "US Application transition is not READY. No package replay was started."
    }
    Assert-NoWorkerContainers
    Write-Host "US_APPLICATION_TRANSITION_READY_OK"

    Write-Host "`n===== FINAL PRE-MUTATION EXACT-MAIN / IDLE GATE ====="
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to refresh origin/main before package mutation."
    }
    Assert-ExactMain "pre-package-mutation"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "stop-idle-worker.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Global Data Engine idle gate drifted before package mutation."
    }
    Assert-NoWorkerContainers
    Wait-ClickHouseHealthy "pre-package-mutation"
    Write-Host "FINAL_PRE_MUTATION_GATE_OK"

    Write-Host "`n===== EXACTLY ONE FROZEN US CAPACITY PILOT PACKAGE ====="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "run-us-capacity-pilot.ps1") `
        -ExpectedHistoryParts $ExpectedHistoryParts `
        -ExpectedSequence $ExpectedSequence `
        -ExpectedFileName $ExpectedFileName `
        -ExpectedPackageSha256 $expectedPackageSha `
        -ExpectedRemainingBefore $ExpectedRemainingBefore `
        -ExpectedRemainingAfter $ExpectedRemainingAfter `
        -Apply `
        -EvidenceRoot $evidenceDir
    if ($LASTEXITCODE -ne 0) {
        throw "Exactly-one-package US capacity pilot failed closed."
    }
    Assert-NoWorkerContainers

    Write-Host "`n===== TARGET-HOST PILOT STOP POINT ====="
    Write-Host "expected_package_sequence=$ExpectedSequence"
    Write-Host "expected_package_file=$ExpectedFileName"
    Write-Host "expected_package_sha256=$expectedPackageSha"
    Write-Host "expected_remaining_before=$ExpectedRemainingBefore"
    Write-Host "expected_remaining_after=$ExpectedRemainingAfter"
    Write-Host "full_corpus_replay_performed=False"
    Write-Host "capacity_projection_performed=False"
    Write-Host "worker_start_performed=False"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host "US_TARGET_HOST_EXACT_ONE_PACKAGE_PILOT_PASS"
}
finally {
    Pop-Location
}
