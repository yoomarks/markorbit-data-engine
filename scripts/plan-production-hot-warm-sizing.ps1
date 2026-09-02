[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [ValidateRange(1, 9999)]
    [int]$ExpectedHistoryParts = 91,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [ValidateRange(1, 99)]
    [double]$HostRecommendedFreePercent = 30,
    [ValidateRange(1, 99)]
    [double]$HostHardFreePercent = 20,
    [ValidateRange(1, 99)]
    [double]$DiskRecommendedFreePercent = 30,
    [ValidateRange(1, 99)]
    [double]$DiskHardFreePercent = 20,
    [string]$PilotReceiptPath = '',
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $lines = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $exitCode`: $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $originMain -ne $expected) { throw "Exact main drift detected during $Phase." }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}

function Assert-NoWorkerContainers {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-a','-q','worker') -AllowFailure
    if ($probe['exit_code'] -ne 0) { throw 'Unable to inspect worker container state.' }
    $count = @($probe['lines'] | Where-Object { $_.Trim() }).Count
    Write-Host "worker_container_count=$count"
    if ($count -ne 0) { throw "Worker containers must be absent at sizing boundary; observed $count." }
}

function Get-ProductionClickHouseHealth {
    $idsProbe = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids = @($idsProbe['lines'] | Where-Object { $_.Trim() })
    if ($idsProbe['exit_code'] -ne 0 -or $ids.Count -ne 1) {
        return [ordered]@{ ready=$false; health=$null; container_id=$null }
    }
    $containerId = $ids[0].Trim()
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim().ToLowerInvariant()
    $ready = [bool](
        $healthProbe['exit_code'] -eq 0 -and
        $health -eq 'healthy' -and
        $sqlProbe['exit_code'] -eq 0 -and
        ((@($sqlProbe['lines']) -join '').Trim() -eq '1')
    )
    return [ordered]@{ ready=$ready; health=$health; container_id=$containerId }
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label file missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Invoke-WorkerJson([string[]]$PythonArgs, [string]$Label) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(docker compose run --rm --no-deps -T `
            --volume "${repoRoot}\app:/app/app:ro" `
            worker python @PythonArgs)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $json = ($lines | ForEach-Object { $_.ToString() }) -join "`n"
    if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
    if (-not $json.Trim()) { throw "$Label produced no JSON." }
    try { return $json | ConvertFrom-Json }
    catch { throw "$Label produced invalid JSON: $($_.Exception.Message)" }
}

function Get-LatestAcceptedPilotReceipt {
    if ($PilotReceiptPath) {
        return (Resolve-Path -LiteralPath $PilotReceiptPath).Path
    }
    $candidates = @(Get-ChildItem -LiteralPath $EvidenceRoot -Recurse -Filter 'pilot_receipt.json' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending)
    foreach ($candidate in $candidates) {
        try {
            $receipt = Get-Content -LiteralPath $candidate.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($receipt.receipt_version -eq 'US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1' -and
                [bool]$receipt.safe -and
                [bool]$receipt.projection_input_ready -and
                $receipt.status -eq 'PASS') {
                return $candidate.FullName
            }
        }
        catch {}
    }
    throw "No accepted US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1 found under $EvidenceRoot."
}

function Get-RequiredCapacityBytes {
    param(
        [Parameter(Mandatory = $true)][int64]$PayloadBytes,
        [Parameter(Mandatory = $true)][double]$FreePercent
    )
    if ($PayloadBytes -lt 0) { throw 'PayloadBytes must be non-negative.' }
    if ($FreePercent -le 0 -or $FreePercent -ge 100) { throw 'FreePercent must be between 0 and 100.' }
    if ($PayloadBytes -eq 0) { return [int64]0 }
    $usableFraction = 1.0 - ($FreePercent / 100.0)
    return [int64][math]::Ceiling([double]$PayloadBytes / $usableFraction)
}

function Get-HostUsableBytes {
    param(
        [Parameter(Mandatory = $true)][int64]$TotalBytes,
        [Parameter(Mandatory = $true)][double]$FreePercent
    )
    if ($TotalBytes -lt 0) { throw 'TotalBytes must be non-negative.' }
    if ($FreePercent -le 0 -or $FreePercent -ge 100) { throw 'FreePercent must be between 0 and 100.' }
    $usableFraction = 1.0 - ($FreePercent / 100.0)
    return [int64][math]::Floor([double]$TotalBytes * $usableFraction)
}

function Get-CurrentNewAllocationBudgetBytes {
    param(
        [Parameter(Mandatory = $true)][int64]$TotalBytes,
        [Parameter(Mandatory = $true)][int64]$FreeBytes,
        [Parameter(Mandatory = $true)][double]$FreePercent
    )
    if ($TotalBytes -lt 0 -or $FreeBytes -lt 0 -or $FreeBytes -gt $TotalBytes) { throw 'Invalid host total/free byte evidence.' }
    if ($FreePercent -le 0 -or $FreePercent -ge 100) { throw 'FreePercent must be between 0 and 100.' }
    $reserve = [int64][math]::Ceiling([double]$TotalBytes * ($FreePercent / 100.0))
    return [int64][math]::Max([int64]0, [int64]($FreeBytes - $reserve))
}

function Sum-TableBytes([object[]]$Tables) {
    $sum = [int64]0
    foreach ($table in @($Tables)) { $sum += [int64]$table.bytes_on_disk }
    return $sum
}

function Invoke-ReadinessReceipt([string]$SizingRelativeRoot) {
    $readinessRelativeRoot = Join-Path $SizingRelativeRoot 'readiness'
    $readinessAbsoluteRoot = Join-Path $repoRoot $readinessRelativeRoot
    New-Item -ItemType Directory -Force -Path $readinessAbsoluteRoot | Out-Null
    $script = Join-Path $PSScriptRoot 'profile-production-multi-disk-migration-readiness.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$script,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-EvidenceRoot',$readinessRelativeRoot
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe @childArgs 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    foreach ($line in @($output | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($exitCode -ne 0) { throw "Production migration readiness exited $exitCode." }
    $dirs = @(Get-ChildItem -LiteralPath $readinessAbsoluteRoot -Directory -Filter 'production_multi_disk_migration_readiness_*' |
        Sort-Object LastWriteTime -Descending)
    if ($dirs.Count -ne 1) { throw "Expected exactly one isolated readiness receipt directory; observed $($dirs.Count)." }
    $receiptPath = Join-Path $dirs[0].FullName 'receipt.json'
    return [ordered]@{ path=$receiptPath; report=(Read-JsonFile $receiptPath 'production migration readiness') }
}

try {
    Write-Host '===== PRODUCTION HOT/WARM SIZING PLAN ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Production Hot/Warm sizing must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Production Hot/Warm sizing requires elevated Administrator PowerShell.'
    }

    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    Assert-NoWorkerContainers
    $productionBefore = Get-ProductionClickHouseHealth
    if (-not $productionBefore['ready']) { throw 'Production ClickHouse must be healthy before sizing.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $sizingRelativeRoot = Join-Path $EvidenceRoot "production_hot_warm_sizing_$timestamp"
    $evidenceDir = Join-Path $repoRoot $sizingRelativeRoot
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    Write-Host 'sizing_stage=readiness_refresh'
    $readinessResult = Invoke-ReadinessReceipt $sizingRelativeRoot
    $readiness = $readinessResult['report']
    $readyForSizing = [bool](
        [string]$readiness.decision -eq 'PRODUCTION_MULTI_DISK_MIGRATION_READINESS_READY_FOR_SIZING_PLAN' -and
        [bool]$readiness.migration_contract.ready_for_sizing_plan -and
        @($readiness.blockers).Count -eq 0
    )
    Write-Host "readiness_decision=$([string]$readiness.decision)"
    Write-Host "readiness_ready_for_sizing=$readyForSizing"
    if (-not $readyForSizing) {
        throw "Production migration readiness is not ready for sizing: $(@($readiness.blockers) -join ',')"
    }

    Write-Host 'sizing_stage=cn_system_metadata'
    $capacityProfile = Invoke-WorkerJson @('-m','app.cn.capacity_profile') 'CN Hot/Warm capacity profile'
    if ([string]$capacityProfile.profile_version -ne 'CN_HOT_WARM_CAPACITY_PROFILE_V1' -or
        -not [bool]$capacityProfile.read_only -or
        [bool]$capacityProfile.full_corpus_scan -or
        [bool]$capacityProfile.mutation_performed) {
        throw 'CN capacity profile did not preserve the read-only metadata contract.'
    }

    $sourceBaseline = $readiness.production.source_baseline
    $factsRows = [int64]$capacityProfile.active_totals.rows_from_parts
    $factsBytes = [int64]$capacityProfile.active_totals.bytes_on_disk
    $sourceRows = [int64]$sourceBaseline.active_rows
    $sourceBytes = [int64]$sourceBaseline.active_bytes_on_disk
    $factsSnapshotWithinSourceBaseline = [bool](
        $factsRows -gt 0 -and $factsBytes -gt 0 -and
        $factsRows -le $sourceRows -and $factsBytes -le $sourceBytes
    )
    Write-Host "facts_snapshot_within_source_baseline=$factsSnapshotWithinSourceBaseline"
    if (-not $factsSnapshotWithinSourceBaseline) {
        throw 'markorbit_facts metadata snapshot is invalid or exceeds the refreshed production source baseline.'
    }

    $allTables = @($capacityProfile.tables)
    $cnTables = @($allTables | Where-Object { ([string]$_.table).StartsWith('cn_') })
    $usTables = @($allTables | Where-Object { ([string]$_.table).StartsWith('us_') })
    $otherTables = @($allTables | Where-Object {
        -not ([string]$_.table).StartsWith('cn_') -and
        -not ([string]$_.table).StartsWith('us_')
    })
    $cnCurrentBytes = Sum-TableBytes $cnTables
    $usCurrentBytes = Sum-TableBytes $usTables
    $globalCurrentBytes = Sum-TableBytes $otherTables
    $cnWarmCandidateTables = @($cnTables | Where-Object { ([string]$_.placement_contract).StartsWith('WARM_') })
    $cnHotContractTables = @($cnTables | Where-Object { ([string]$_.placement_contract).StartsWith('HOT_') })
    $cnWarmCandidateBytes = Sum-TableBytes $cnWarmCandidateTables
    $cnExplicitHotContractBytes = Sum-TableBytes $cnHotContractTables

    Write-Host 'sizing_stage=us_application_projection'
    $remaining = Invoke-WorkerJson @(
        '-m','app.us.remaining_capacity_inventory',
        '--expected-history-parts',"$ExpectedHistoryParts",
        '--compact'
    ) 'Remaining US Application source inventory'
    if (-not [bool]$remaining.safe -or [string]$remaining.status -ne 'PASS') {
        throw 'Remaining US Application inventory is not authoritative/safe.'
    }
    $pilotPath = Get-LatestAcceptedPilotReceipt
    $pilot = Read-JsonFile $pilotPath 'US bounded pilot receipt'
    if ([string]$pilot.receipt_version -ne 'US_BOUNDED_CAPACITY_PILOT_RECEIPT_V1' -or
        -not [bool]$pilot.safe -or -not [bool]$pilot.projection_input_ready -or [string]$pilot.status -ne 'PASS') {
        throw 'US bounded pilot receipt is not accepted projection input.'
    }
    $pilotRaw = [int64]$pilot.pilot.raw_bytes
    $pilotHot = [int64]$pilot.pilot.hot_bytes
    if ($pilotRaw -le 0 -or $pilotHot -le 0) { throw 'US pilot raw/hot bytes must be positive.' }
    $remainingRaw = [int64]$remaining.remaining_raw_bytes
    $projectedRemainingUsApplicationHot = [int64][math]::Ceiling(
        [double]$remainingRaw * ([double]$pilotHot / [double]$pilotRaw)
    )
    $projectedUsApplicationPayload = [int64]($usCurrentBytes + $projectedRemainingUsApplicationHot)

    Write-Host 'sizing_stage=physical_budget_math'
    $driveMap = @{}
    foreach ($drive in @($readiness.host_drives)) { $driveMap[[string]$drive.drive] = $drive }
    foreach ($letter in @('D:','E:','F:')) {
        if (-not $driveMap.ContainsKey($letter)) { throw "Sizing readiness omitted drive $letter." }
    }
    $d = $driveMap['D:']
    $e = $driveMap['E:']
    $f = $driveMap['F:']

    $dHostRecommendedUsable = Get-HostUsableBytes ([int64]$d.total_bytes) $HostRecommendedFreePercent
    $dHostHardUsable = Get-HostUsableBytes ([int64]$d.total_bytes) $HostHardFreePercent
    $dCurrentRecommendedNewBudget = Get-CurrentNewAllocationBudgetBytes ([int64]$d.total_bytes) ([int64]$d.free_bytes) $HostRecommendedFreePercent
    $dCurrentHardNewBudget = Get-CurrentNewAllocationBudgetBytes ([int64]$d.total_bytes) ([int64]$d.free_bytes) $HostHardFreePercent
    $eHostRecommendedUsable = Get-HostUsableBytes ([int64]$e.total_bytes) $HostRecommendedFreePercent
    $eHostHardUsable = Get-HostUsableBytes ([int64]$e.total_bytes) $HostHardFreePercent
    $eCurrentRecommendedNewBudget = Get-CurrentNewAllocationBudgetBytes ([int64]$e.total_bytes) ([int64]$e.free_bytes) $HostRecommendedFreePercent
    $eCurrentHardNewBudget = Get-CurrentNewAllocationBudgetBytes ([int64]$e.total_bytes) ([int64]$e.free_bytes) $HostHardFreePercent

    # Fail closed: conditional Warm candidates are NOT subtracted from CN Hot until
    # equivalence/rollback evidence exists. The initial hot_cn payload therefore
    # conservatively carries every currently active cn_* byte.
    $hotCnPayload = $cnCurrentBytes
    $hotUsApplicationPayload = $projectedUsApplicationPayload
    $hotGlobalExistingPayload = $globalCurrentBytes

    $hotCnRecommended = Get-RequiredCapacityBytes $hotCnPayload $DiskRecommendedFreePercent
    $hotUsRecommended = Get-RequiredCapacityBytes $hotUsApplicationPayload $DiskRecommendedFreePercent
    $hotGlobalExistingRecommended = Get-RequiredCapacityBytes $hotGlobalExistingPayload $DiskRecommendedFreePercent
    $warmCandidateRecommended = Get-RequiredCapacityBytes $cnWarmCandidateBytes $DiskRecommendedFreePercent

    $hotCnHard = Get-RequiredCapacityBytes $hotCnPayload $DiskHardFreePercent
    $hotUsHard = Get-RequiredCapacityBytes $hotUsApplicationPayload $DiskHardFreePercent
    $hotGlobalExistingHard = Get-RequiredCapacityBytes $hotGlobalExistingPayload $DiskHardFreePercent
    $warmCandidateHard = Get-RequiredCapacityBytes $cnWarmCandidateBytes $DiskHardFreePercent

    $recommendedCoreHot = [int64]($hotCnRecommended + $hotUsRecommended)
    $hardCoreHot = [int64]($hotCnHard + $hotUsHard)
    $hotGlobalRecommendedQuota = [int64][math]::Max([int64]0, [int64]($dHostRecommendedUsable - $recommendedCoreHot))
    $hotGlobalHardQuota = [int64][math]::Max([int64]0, [int64]($dHostHardUsable - $hardCoreHot))
    $dRecommendedFinalFits = [bool]($hotGlobalRecommendedQuota -ge $hotGlobalExistingRecommended)
    $dHardFinalFits = [bool]($hotGlobalHardQuota -ge $hotGlobalExistingHard)

    # Final E architecture fit uses total physical capacity after a separately
    # accepted rebalance. Current E free-space fit is evaluated independently
    # below so existing unrelated bytes do not become a false architecture blocker.
    $eRecommendedFinalFits = [bool]($warmCandidateRecommended -le $eHostRecommendedUsable)
    $eHardFinalFits = [bool]($warmCandidateHard -le $eHostHardUsable)
    $eCurrentRecommendedProvisionFits = [bool]($warmCandidateRecommended -le $eCurrentRecommendedNewBudget)
    $eCurrentHardProvisionFits = [bool]($warmCandidateHard -le $eCurrentHardNewBudget)

    # This is only a lower-bound coexistence check: the new ext4 plane must at
    # least materialize the current active source bytes while the accepted Docker
    # source remains retained. VHDX quotas are not counted as preallocated bytes.
    $sourceActiveBytes = $sourceBytes
    $dCoexistenceRecommendedLowerBoundFits = [bool]($sourceActiveBytes -le $dCurrentRecommendedNewBudget)
    $dCoexistenceHardLowerBoundFits = [bool]($sourceActiveBytes -le $dCurrentHardNewBudget)

    $finalCapacityState = if ($dRecommendedFinalFits -and $eRecommendedFinalFits) {
        'RECOMMENDED_30_PERCENT_PLAN_FITS'
    } elseif ($dHardFinalFits -and $eHardFinalFits) {
        'HARD_20_PERCENT_ONLY'
    } else {
        'CAPACITY_ARCHITECTURE_BLOCKED'
    }
    $coexistenceState = if ($dCoexistenceRecommendedLowerBoundFits -and $eCurrentRecommendedProvisionFits) {
        'CURRENT_HOST_CAN_PROVISION_WITH_RECOMMENDED_RESERVE'
    } elseif ($dCoexistenceHardLowerBoundFits -and $eCurrentHardProvisionFits) {
        'CURRENT_HOST_HARD_FLOOR_ONLY'
    } else {
        'REBALANCE_REQUIRED_BEFORE_PROVISION'
    }

    $planBlockers = @()
    if (-not $dHardFinalFits) { $planBlockers += 'D_FINAL_HOT_PLAN_CANNOT_FIT_20_PERCENT_HOST_AND_DISK_FLOORS' }
    if (-not $eHardFinalFits) { $planBlockers += 'E_FINAL_WARM_PLAN_CANNOT_FIT_20_PERCENT_HOST_AND_DISK_FLOORS' }

    $decision = if ($planBlockers.Count -eq 0) {
        'PRODUCTION_HOT_WARM_SIZING_PLAN_READY'
    } else {
        'PRODUCTION_HOT_WARM_SIZING_CAPACITY_BLOCKED'
    }
    $nextGate = if ($planBlockers.Count -gt 0) {
        'PRODUCTION_STORAGE_CAPACITY_REDESIGN'
    } elseif ($coexistenceState -eq 'REBALANCE_REQUIRED_BEFORE_PROVISION') {
        'PRODUCTION_STORAGE_REBALANCE_PLAN'
    } elseif ($coexistenceState -eq 'CURRENT_HOST_HARD_FLOOR_ONLY') {
        'PRODUCTION_STORAGE_RESERVE_EXCEPTION_REVIEW'
    } else {
        'PRODUCTION_VHDX_PROVISIONING_PREFLIGHT'
    }

    $productionAfter = Get-ProductionClickHouseHealth
    Assert-NoWorkerContainers
    if (-not $productionAfter['ready']) { throw 'Production ClickHouse must remain healthy after sizing.' }
    $envHashAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during read-only sizing.' }

    $report = [ordered]@{
        plan_version='PRODUCTION_HOT_WARM_SIZING_PLAN_V1'
        decision=$decision
        read_only=$true
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        next_gate=$nextGate
        readiness=[ordered]@{
            receipt_path=$readinessResult['path']
            decision=[string]$readiness.decision
            ready_for_sizing_plan=$readyForSizing
            blockers=@($readiness.blockers)
        }
        evidence_inputs=[ordered]@{
            cn_profile_version=[string]$capacityProfile.profile_version
            cn_query_scope=[string]$capacityProfile.query_scope
            cn_full_corpus_scan=[bool]$capacityProfile.full_corpus_scan
            facts_snapshot_within_source_baseline=$factsSnapshotWithinSourceBaseline
            us_pilot_receipt_path=$pilotPath
            us_pilot_receipt_identity=[string]$pilot.pilot.receipt_identity
            us_remaining_inventory_version=[string]$remaining.inventory_version
            us_application_projection_scope='APPLICATION_ONLY_DO_NOT_GENERALIZE_TO_ASSIGNMENT_TTAB_OR_GLOBAL'
        }
        current_payload=[ordered]@{
            source_active_rows=$sourceRows
            source_active_bytes_on_disk=$sourceActiveBytes
            facts_active_rows=$factsRows
            facts_active_bytes_on_disk=$factsBytes
            cn_active_bytes=$cnCurrentBytes
            cn_explicit_hot_contract_bytes=$cnExplicitHotContractBytes
            cn_conditional_warm_candidate_bytes=$cnWarmCandidateBytes
            cn_conservative_initial_hot_payload_bytes=$hotCnPayload
            us_active_bytes=$usCurrentBytes
            global_other_active_bytes=$globalCurrentBytes
        }
        us_application=[ordered]@{
            remaining_package_count=[int]$remaining.remaining_count
            remaining_raw_bytes=$remainingRaw
            pilot_raw_bytes=$pilotRaw
            pilot_hot_bytes=$pilotHot
            measured_raw_to_hot_ratio=[double]$pilotHot / [double]$pilotRaw
            projected_remaining_hot_bytes=$projectedRemainingUsApplicationHot
            projected_total_application_hot_payload_bytes=$hotUsApplicationPayload
            assignment_projection_authorized=$false
            ttab_projection_authorized=$false
            global_projection_authorized=$false
        }
        reserve_policy=[ordered]@{
            host_recommended_free_percent=$HostRecommendedFreePercent
            host_hard_free_percent=$HostHardFreePercent
            disk_recommended_free_percent=$DiskRecommendedFreePercent
            disk_hard_free_percent=$DiskHardFreePercent
        }
        drives=[ordered]@{
            D=[ordered]@{
                total_bytes=[int64]$d.total_bytes
                free_bytes=[int64]$d.free_bytes
                filesystem=[string]$d.filesystem
                recommended_host_usable_bytes=$dHostRecommendedUsable
                hard_host_usable_bytes=$dHostHardUsable
                current_new_allocation_budget_recommended_bytes=$dCurrentRecommendedNewBudget
                current_new_allocation_budget_hard_bytes=$dCurrentHardNewBudget
            }
            E=[ordered]@{
                total_bytes=[int64]$e.total_bytes
                free_bytes=[int64]$e.free_bytes
                filesystem=[string]$e.filesystem
                recommended_host_usable_bytes=$eHostRecommendedUsable
                hard_host_usable_bytes=$eHostHardUsable
                current_new_allocation_budget_recommended_bytes=$eCurrentRecommendedNewBudget
                current_new_allocation_budget_hard_bytes=$eCurrentHardNewBudget
            }
            F=[ordered]@{
                total_bytes=[int64]$f.total_bytes
                free_bytes=[int64]$f.free_bytes
                filesystem=[string]$f.filesystem
                role='RAW_COLD_NATIVE_WINDOWS'
                fresh_raw_recopy_required=$false
            }
        }
        target_quotas=[ordered]@{
            recommended=[ordered]@{
                hot_cn_capacity_bytes=$hotCnRecommended
                hot_us_application_capacity_bytes=$hotUsRecommended
                hot_global_bootstrap_capacity_bytes=$hotGlobalRecommendedQuota
                hot_global_existing_minimum_capacity_bytes=$hotGlobalExistingRecommended
                warm_candidate_capacity_bytes=$warmCandidateRecommended
                global_future_scale_sufficiency_claimed=$false
                warm_future_us_global_sufficiency_claimed=$false
            }
            hard_floor=[ordered]@{
                hot_cn_capacity_bytes=$hotCnHard
                hot_us_application_capacity_bytes=$hotUsHard
                hot_global_bootstrap_capacity_bytes=$hotGlobalHardQuota
                hot_global_existing_minimum_capacity_bytes=$hotGlobalExistingHard
                warm_candidate_capacity_bytes=$warmCandidateHard
                global_future_scale_sufficiency_claimed=$false
                warm_future_us_global_sufficiency_claimed=$false
            }
        }
        fit=[ordered]@{
            final_capacity_state=$finalCapacityState
            coexistence_state=$coexistenceState
            d_recommended_final_fits=$dRecommendedFinalFits
            d_hard_final_fits=$dHardFinalFits
            e_recommended_final_fits=$eRecommendedFinalFits
            e_hard_final_fits=$eHardFinalFits
            d_coexistence_recommended_lower_bound_fits=$dCoexistenceRecommendedLowerBoundFits
            d_coexistence_hard_lower_bound_fits=$dCoexistenceHardLowerBoundFits
            e_current_recommended_provision_fits=$eCurrentRecommendedProvisionFits
            e_current_hard_provision_fits=$eCurrentHardProvisionFits
            coexistence_lower_bound_bytes=$sourceActiveBytes
        }
        blockers=@($planBlockers)
        constraints=[ordered]@{
            conditional_cn_warm_demotion_authorized=$false
            vhdx_create_authorized=$false
            vhdx_resize_authorized=$false
            vhdx_mount_authorized=$false
            live_migration_authorized=$false
            source_volume_delete_authorized=$false
            source_volume_reclaim_counted_as_current_free_space=$false
            raw_delete_authorized=$false
            full_cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
            assignment_capacity_inferred_from_application=$false
            ttab_capacity_inferred_from_application=$false
            global_capacity_inferred_from_us_application=$false
            future_warm_capacity_claimed_without_evidence=$false
        }
        production_invariant_preserved=[bool]($productionBefore['ready'] -and $productionAfter['ready'])
        env_unchanged=$envUnchanged
        vhdx_create_performed=$false
        vhdx_resize_performed=$false
        vhdx_mount_performed=$false
        vhdx_move_performed=$false
        wsl_unmount_performed=$false
        wsl_shutdown_performed=$false
        docker_restart_performed=$false
        docker_prune_performed=$false
        production_clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        source_copy_performed=$false
        corpus_replay_performed=$false
    }

    $reportPath = Join-Path $evidenceDir 'production_hot_warm_sizing_plan.json'
    $report | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    $gib = [math]::Pow(1024,3)
    Write-Host '===== PRODUCTION HOT/WARM SIZING RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "final_capacity_state=$finalCapacityState"
    Write-Host "coexistence_state=$coexistenceState"
    Write-Host "facts_snapshot_within_source_baseline=$factsSnapshotWithinSourceBaseline"
    Write-Host ("cn_active_gib={0:N2}" -f ($cnCurrentBytes / $gib))
    Write-Host ("cn_conditional_warm_candidate_gib={0:N2}" -f ($cnWarmCandidateBytes / $gib))
    Write-Host ("us_current_active_gib={0:N2}" -f ($usCurrentBytes / $gib))
    Write-Host "us_remaining_application_packages=$([int]$remaining.remaining_count)"
    Write-Host ("us_projected_remaining_application_hot_gib={0:N2}" -f ($projectedRemainingUsApplicationHot / $gib))
    Write-Host ("recommended_hot_cn_capacity_gib={0:N2}" -f ($hotCnRecommended / $gib))
    Write-Host ("recommended_hot_us_application_capacity_gib={0:N2}" -f ($hotUsRecommended / $gib))
    Write-Host ("recommended_hot_global_bootstrap_capacity_gib={0:N2}" -f ($hotGlobalRecommendedQuota / $gib))
    Write-Host ("recommended_warm_candidate_capacity_gib={0:N2}" -f ($warmCandidateRecommended / $gib))
    Write-Host ("drive_D_current_new_budget_30pct_gib={0:N2}" -f ($dCurrentRecommendedNewBudget / $gib))
    Write-Host ("drive_E_current_new_budget_30pct_gib={0:N2}" -f ($eCurrentRecommendedNewBudget / $gib))
    Write-Host "d_recommended_final_fits=$dRecommendedFinalFits"
    Write-Host "d_coexistence_recommended_lower_bound_fits=$dCoexistenceRecommendedLowerBoundFits"
    Write-Host "e_recommended_final_fits=$eRecommendedFinalFits"
    Write-Host "e_current_recommended_provision_fits=$eCurrentRecommendedProvisionFits"
    Write-Host "vhdx_create_authorized=False"
    Write-Host "live_migration_authorized=False"
    Write-Host "us_package_2_authorized=False"
    Write-Host "us_bulk_authorized=False"
    Write-Host "raw_delete_authorized=False"
    Write-Host "blocker_count=$($planBlockers.Count)"
    foreach ($blocker in $planBlockers) { Write-Host "blocker=$blocker" }
    Write-Host "production_invariant_preserved=$([bool]$report.production_invariant_preserved)"
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_HOT_WARM_SIZING_PLAN_DONE'

    Assert-ExactMain 'exit'
}
finally { Pop-Location }
