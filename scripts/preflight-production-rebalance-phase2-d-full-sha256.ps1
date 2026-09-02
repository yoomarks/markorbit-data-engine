[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$RawTargetRoot = 'F:\MarkOrbitData\raw',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& $Command @Arguments 2>&1)
        $nativeExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    $renderedLines = @($outputLines | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $nativeExitCode -ne 0) {
        throw "$Command failed with exit code ${nativeExitCode}: $($renderedLines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$nativeExitCode; lines=@($renderedLines) }
}

function Assert-ExactMain([string]$Boundary) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $headSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMainSha = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Boundary"
    Write-Host "HEAD=$headSha"
    Write-Host "origin/main=$originMainSha"
    Write-Host "expected=$expected"
    if ($headSha -ne $expected -or $originMainSha -ne $expected) { throw "Exact main drift detected during $Boundary." }
    if (git status --porcelain) { throw "Working tree must be clean during $Boundary." }
}

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $candidate = $Path.Trim()
    if ($candidate.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) { $candidate = $candidate.Substring(4) }
    if ($candidate.StartsWith('\??\', [System.StringComparison]::OrdinalIgnoreCase)) { $candidate = $candidate.Substring(4) }
    if (-not ([System.IO.Path]::IsPathRooted($candidate) -and $candidate -match '^[A-Za-z]:[\\/]')) { return '' }
    return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
}

function Test-PathContains([string]$ParentPath, [string]$ChildPath) {
    $parent = Normalize-HostPath $ParentPath
    $child = Normalize-HostPath $ChildPath
    if (-not $parent -or -not $child) { return $false }
    if ($child.Equals($parent, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $child.StartsWith($parent + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-PathsOverlap([string]$LeftPath, [string]$RightPath) {
    return [bool]((Test-PathContains $LeftPath $RightPath) -or (Test-PathContains $RightPath $LeftPath))
}

function Get-OptionalPropertyValue([object]$Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-OptionalArrayProperty([object]$Object, [string]$Name) {
    $value = Get-OptionalPropertyValue $Object $Name
    if ($null -eq $value) { return @() }
    return @($value)
}

function Get-DotEnvValues {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines)
    $values = @{}
    foreach ($line in @($Lines)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith('#')) { continue }
        $equals = $trimmed.IndexOf('=')
        if ($equals -le 0) { continue }
        $key = $trimmed.Substring(0, $equals).Trim()
        $value = $trimmed.Substring($equals + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            if ($value.Length -ge 2) { $value = $value.Substring(1, $value.Length - 2) }
        }
        $values[$key] = $value
    }
    return $values
}

function Get-DriveSnapshot([string]$Letter) {
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { throw "Required drive missing: $root" }
    $drive = New-Object System.IO.DriveInfo($root)
    return [ordered]@{ drive="${Letter}:"; total_bytes=[int64]$drive.TotalSize; free_bytes=[int64]$drive.AvailableFreeSpace; filesystem=[string]$drive.DriveFormat }
}

function Get-ProductionClickHouseHealth {
    $idProbe = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids = @($idProbe.lines | Where-Object { $_.Trim() })
    if ($idProbe.exit_code -ne 0 -or $ids.Count -ne 1) { return [ordered]@{ ready=$false; health=$null; container_id=$null } }
    $containerId = $ids[0].Trim()
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $health = (@($healthProbe.lines) -join '').Trim().ToLowerInvariant()
    $ready = [bool]($healthProbe.exit_code -eq 0 -and $health -eq 'healthy' -and $sqlProbe.exit_code -eq 0 -and ((@($sqlProbe.lines) -join '').Trim() -eq '1'))
    return [ordered]@{ ready=$ready; health=$health; container_id=$containerId }
}

function Assert-AcceptedProductionMount([string]$ContainerId) {
    $probe = Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$ContainerId) -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Unable to inspect production ClickHouse mounts.' }
    try { $mounts = ((@($probe.lines) -join "`n") | ConvertFrom-Json) }
    catch { throw "Production ClickHouse mount JSON invalid: $($_.Exception.Message)" }
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    $ready = [bool]($matches.Count -eq 1 -and [string]$matches[0].Type -eq 'volume' -and [string]$matches[0].Name -eq $AcceptedVolume)
    Write-Host "accepted_production_mount_ready=$ready"
    if (-not $ready) { throw 'Production ClickHouse data mount is not the accepted named volume.' }
}

function Assert-RawConsumersStopped {
    $runningTotal = 0
    foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
        $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure
        if ($probe.exit_code -ne 0) { throw "Unable to inspect Raw consumer service $service." }
        $running = 0
        foreach ($containerId in @($probe.lines | Where-Object { $_.Trim() })) {
            $state = Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$containerId.Trim()) -AllowFailure
            if ($state.exit_code -ne 0) { throw "Unable to inspect Raw consumer container for $service." }
            if (((@($state.lines) -join '').Trim().ToLowerInvariant()) -eq 'true') { $running++ }
        }
        $runningTotal += $running
        Write-Host "raw_consumer_service=$service running_count=$running"
    }
    Write-Host "running_raw_consumer_count=$runningTotal"
    if ($runningTotal -ne 0) { throw "All Raw consumer services must remain absent/stopped; observed $runningTotal." }
}

function Get-AllContainerMounts {
    $idsProbe = Invoke-NativeText 'docker' @('ps','-a','-q') -AllowFailure
    if ($idsProbe.exit_code -ne 0) { throw 'Unable to enumerate Docker containers.' }
    $entries = @()
    foreach ($containerId in @($idsProbe.lines | Where-Object { $_.Trim() })) {
        $id = $containerId.Trim()
        $inspectProbe = Invoke-NativeText 'docker' @('inspect','--format','{{json .}}',$id) -AllowFailure
        if ($inspectProbe.exit_code -ne 0) { throw "Unable to inspect container $id." }
        $json = (@($inspectProbe.lines) -join "`n").Trim()
        if (-not $json) { throw "Docker inspect produced no JSON for $id." }
        try { $container = $json | ConvertFrom-Json }
        catch { throw "Docker inspect produced invalid JSON for ${id}: $($_.Exception.Message)" }
        $state = Get-OptionalPropertyValue $container 'State'
        if ($null -eq $state) { throw "Docker inspect omitted State for $id." }
        $runningValue = Get-OptionalPropertyValue $state 'Running'
        if ($null -eq $runningValue) { throw "Docker inspect omitted State.Running for $id." }
        foreach ($mount in @(Get-OptionalArrayProperty $container 'Mounts')) {
            $source = [string](Get-OptionalPropertyValue $mount 'Source')
            $entries += [pscustomobject]@{
                container_id=[string](Get-OptionalPropertyValue $container 'Id')
                container_name=([string](Get-OptionalPropertyValue $container 'Name')).TrimStart('/')
                running=[bool]$runningValue
                source=$source
                normalized_source=(Normalize-HostPath $source)
                destination=[string](Get-OptionalPropertyValue $mount 'Destination')
                mount_type=[string](Get-OptionalPropertyValue $mount 'Type')
                volume_name=[string](Get-OptionalPropertyValue $mount 'Name')
            }
        }
    }
    return @($entries)
}

function Get-ComposeBindMounts {
    $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','config','--format','json') -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Unable to resolve current Docker Compose model.' }
    try { $config = ((@($probe.lines) -join "`n") | ConvertFrom-Json) }
    catch { throw "Current Docker Compose model is invalid JSON: $($_.Exception.Message)" }
    $services = Get-OptionalPropertyValue $config 'services'
    if ($null -eq $services) { throw 'Current Docker Compose model omitted services.' }
    $entries = @()
    foreach ($serviceProperty in @($services.PSObject.Properties)) {
        foreach ($mount in @(Get-OptionalArrayProperty $serviceProperty.Value 'volumes')) {
            if ([string](Get-OptionalPropertyValue $mount 'type') -ne 'bind') { continue }
            $source = [string](Get-OptionalPropertyValue $mount 'source')
            $target = [string](Get-OptionalPropertyValue $mount 'target')
            if (-not $source -or -not $target) { throw "Compose bind for service $($serviceProperty.Name) omitted source or target." }
            $entries += [pscustomobject]@{ service=[string]$serviceProperty.Name; source=$source; normalized_source=(Normalize-HostPath $source); target=$target }
        }
    }
    return @($entries)
}

function Assert-ComposeRawBindings([object[]]$ComposeBinds, [string]$ProtectedVisualProcessed) {
    foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
        $raw = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/raw' })
        if ($raw.Count -ne 1 -or -not (Normalize-HostPath $raw[0].normalized_source).Equals((Normalize-HostPath $RawTargetRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/raw for $service does not resolve exactly to accepted F Raw target."
        }
    }
    foreach ($service in @('api','worker','mark-image-worker')) {
        $visualRaw = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/visual-raw' })
        if ($visualRaw.Count -ne 1 -or -not (Normalize-HostPath $visualRaw[0].normalized_source).Equals((Normalize-HostPath $RawTargetRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/visual-raw for $service does not resolve exactly to accepted F Raw target."
        }
        $visualProcessed = @($ComposeBinds | Where-Object { $_.service -eq $service -and $_.target -eq '/data/visual-processed' })
        if ($visualProcessed.Count -ne 1 -or -not (Normalize-HostPath $visualProcessed[0].normalized_source).Equals($ProtectedVisualProcessed, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Compose /data/visual-processed for $service does not resolve exactly to protected D subtree."
        }
    }
}

function Assert-NoReparsePoints([string]$Root) {
    $normalized = Normalize-HostPath $Root
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized -PathType Container)) { throw "Required directory missing: $Root" }
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($normalized)
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        $attributes = [System.IO.File]::GetAttributes($directory)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point found in Phase2D tree: $directory" }
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $entryAttributes = [System.IO.File]::GetAttributes($entry)
            if (($entryAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point found in Phase2D tree: $entry" }
            if (($entryAttributes -band [System.IO.FileAttributes]::Directory) -ne 0) { $stack.Push($entry) }
        }
    }
}

function Get-RawDeletionManifest([string]$SourceRoot, [string]$ProtectedRoot) {
    $source = Normalize-HostPath $SourceRoot
    $protected = Normalize-HostPath $ProtectedRoot
    if (-not $source -or -not (Test-Path -LiteralPath $source -PathType Container)) { throw 'Legacy D Raw source directory is missing.' }
    if (-not $protected -or -not (Test-PathContains $source $protected)) { throw 'Protected visual_processed path is not a child of legacy D Raw root.' }
    $prefix = $source + '\'
    $entries = @()
    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($source, '*', [System.IO.SearchOption]::AllDirectories)) {
        $fullPath = [System.IO.Path]::GetFullPath($filePath)
        if (Test-PathContains $protected $fullPath) { continue }
        $info = New-Object System.IO.FileInfo($fullPath)
        $entries += [pscustomobject]@{
            relative_path=$fullPath.Substring($prefix.Length)
            source_path=$fullPath
            length=[int64]$info.Length
            last_write_utc_ticks=[int64]$info.LastWriteTimeUtc.Ticks
        }
    }
    return @($entries | Sort-Object relative_path)
}

function Compare-RawMetadataManifests([object[]]$Left, [object[]]$Right) {
    if (@($Left).Count -ne @($Right).Count) { return $false }
    for ($index = 0; $index -lt @($Left).Count; $index++) {
        $a = $Left[$index]
        $b = $Right[$index]
        if (-not ([string]$a.relative_path).Equals([string]$b.relative_path, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        if ([int64]$a.length -ne [int64]$b.length -or [int64]$a.last_write_utc_ticks -ne [int64]$b.last_write_utc_ticks) { return $false }
    }
    return $true
}

function Find-AcceptedSafePhase1Receipt {
    $reportsRoot = Join-Path $repoRoot 'reports'
    if (-not (Test-Path -LiteralPath $reportsRoot -PathType Container)) { throw 'reports directory missing; accepted Phase1E safe-delete receipt required.' }
    $directories = @(Get-ChildItem -LiteralPath $reportsRoot -Directory -Filter 'production_rebalance_phase1_e_reparse_safe_delete_apply_*' | Sort-Object LastWriteTime -Descending)
    foreach ($directory in $directories) {
        $path = Join-Path $directory.FullName 'production_rebalance_phase1_e_reparse_safe_delete_apply.json'
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try { $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { continue }
        if ([string]$receipt.receipt_version -ne 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY_V1') { continue }
        if ([string]$receipt.decision -ne 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_GO') { continue }
        if ([string]$receipt.next_gate -ne 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_APPLY') { continue }
        if (-not [bool]$receipt.apply_accepted -or -not [bool]$receipt.mutation_performed) { continue }
        if (-not [bool]$receipt.boundary_manifest_match -or [int64]$receipt.native_lx_verified_count -ne 63) { continue }
        if (-not [bool]$receipt.capacity.recommended_floor_met -or -not [bool]$receipt.production_invariant_preserved -or -not [bool]$receipt.env_unchanged) { continue }
        return [ordered]@{ path=$path; receipt=$receipt }
    }
    throw 'No accepted reparse-safe Phase1E GO receipt found.'
}

function Assert-Phase1ReceiptProvenance([object]$Receipt) {
    $phase1Sha = ([string]$Receipt.engine_sha).Trim().ToLowerInvariant()
    if ($phase1Sha -notmatch '^[0-9a-f]{40}$') { throw 'Accepted Phase1E receipt engine SHA is invalid.' }
    $ancestorProbe = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$phase1Sha,$ExpectedMainSha) -AllowFailure
    if ($ancestorProbe.exit_code -ne 0) { throw 'Accepted Phase1E receipt SHA is not an ancestor of current exact main.' }
    $diffProbe = Invoke-NativeText 'git' @('diff','--name-only',"${phase1Sha}..$ExpectedMainSha")
    $allowed = @(
        'scripts/preflight-production-rebalance-phase2-d-full-sha256.ps1',
        'tests/test_production_rebalance_phase2_d_full_sha256_preflight_contract.py',
        '.github/workflows/production-rebalance-phase2-d-full-sha256-preflight-runtime.yml'
    )
    $changed = @($diffProbe.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $allowed })
    Write-Host "phase1_receipt_engine_sha=$phase1Sha"
    Write-Host "phase1_to_current_changed_file_count=$($changed.Count)"
    Write-Host "phase1_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    if ($unexpected.Count -ne 0) { throw "Phase1E acceptance provenance invalidated by non-Phase2-preflight changes: $($unexpected -join ', ')" }
}

function Invoke-FreshCandidateInventory([string]$RunId) {
    $childRelativeRoot = Join-Path (Join-Path 'reports' '_p2pre') (Join-Path $RunId 'inventory')
    $childAbsoluteRoot = Join-Path $repoRoot $childRelativeRoot
    New-Item -ItemType Directory -Force -Path $childAbsoluteRoot | Out-Null
    Write-Host 'phase2_preflight_stage=fresh_candidate_inventory'
    Write-Host "candidate_inventory_evidence_root=$childAbsoluteRoot"
    $scriptPath = Join-Path $PSScriptRoot 'profile-production-storage-rebalance-candidates.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-LegacyRawRoot',$LegacyRawRoot,
        '-RawTargetRoot',$RawTargetRoot,
        '-LegacyEHotRoot',$LegacyEHotRoot,
        '-LegacyEHotLogsRoot',$LegacyEHotLogsRoot,
        '-EvidenceRoot',$childRelativeRoot
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& powershell.exe @childArgs 2>&1)
        $childExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    foreach ($line in @($outputLines | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($childExitCode -ne 0) { throw "Fresh production rebalance inventory exited $childExitCode." }
    $dirs = @(Get-ChildItem -LiteralPath $childAbsoluteRoot -Directory -Filter 'production_storage_rebalance_inventory_*' | Sort-Object LastWriteTime -Descending)
    if ($dirs.Count -ne 1) { throw "Expected exactly one fresh rebalance inventory directory; observed $($dirs.Count)." }
    $receiptPath = Join-Path $dirs[0].FullName 'production_storage_rebalance_candidate_inventory.json'
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'Fresh rebalance inventory receipt missing.' }
    try { $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Fresh rebalance inventory receipt invalid JSON: $($_.Exception.Message)" }
    return [ordered]@{ path=$receiptPath; receipt=$receipt }
}

try {
    if ($ContractOnly) {
        if (-not (Test-PathContains 'D:\root' 'D:\root\child')) { throw 'Path containment contract failed.' }
        if (Test-PathContains 'D:\root\protected' 'D:\root\other') { throw 'Protected path containment contract failed.' }
        Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_CONTRACT_OK'
        return
    }

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D FULL SHA256 PREFLIGHT ====='
    Write-Host 'read_only=True'
    Write-Host 'apply_supported=False'
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase2D full-SHA256 preflight must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase2D full-SHA256 preflight requires elevated Administrator PowerShell.' }

    $legacyRaw = Normalize-HostPath $LegacyRawRoot
    $rawTarget = Normalize-HostPath $RawTargetRoot
    $eHot = Normalize-HostPath $LegacyEHotRoot
    $eLogs = Normalize-HostPath $LegacyEHotLogsRoot
    $protectedVisualProcessed = Normalize-HostPath (Join-Path $legacyRaw 'visual_processed')
    if (-not $legacyRaw.Equals('D:\yoomarks\markorbit-data-engine\raw_data', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyRawRoot must remain exact approved D Raw root.' }
    if (-not $rawTarget.Equals('F:\MarkOrbitData\raw', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RawTargetRoot must remain exact accepted F Raw root.' }
    if (-not $eHot.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot changed.' }
    if (-not $eLogs.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot changed.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $runId = "${timestamp}_$PID"
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase2_d_full_sha256_preflight_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env is required for current Raw binding verification.' }
    $envHashBefore = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $envLines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    $envValues = Get-DotEnvValues $envLines

    Write-Host 'phase2_preflight_stage=accepted_phase1_provenance'
    $phase1 = Find-AcceptedSafePhase1Receipt
    Write-Host "accepted_phase1_receipt=$($phase1.path)"
    Assert-Phase1ReceiptProvenance $phase1.receipt

    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$productionBefore.ready)"
    Write-Host "production_clickhouse_health_before=$($productionBefore.health)"
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before Phase2D preflight.' }
    Assert-AcceptedProductionMount $productionBefore.container_id

    $inventoryResult = Invoke-FreshCandidateInventory $runId
    $inventory = $inventoryResult.receipt
    if ([string]$inventory.receipt_version -ne 'PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1') { throw 'Unexpected rebalance candidate receipt version.' }
    if (-not [bool]$inventory.read_only -or -not [bool]$inventory.production_invariant_preserved -or -not [bool]$inventory.env_unchanged) { throw 'Fresh candidate inventory lost read-only production invariants.' }
    if ([int64]$inventory.deficits.e_additional_free_recommended_bytes -ne 0) { throw 'Phase2D requires E recommended coexistence deficit to be zero.' }
    if ([bool]$inventory.preferred_candidates.E.legacy_ntfs_clickhouse.stats.exists -or [bool]$inventory.preferred_candidates.E.legacy_ntfs_clickhouse_logs.stats.exists) { throw 'Legacy E roots must remain absent after accepted Phase1E.' }
    $dCandidate = $inventory.preferred_candidates.D.legacy_raw
    if (-not [bool]$dCandidate.preferred_candidate -or -not [bool]$dCandidate.metadata_parity_exact) { throw 'D legacy Raw candidate is not an exact duplicate candidate.' }
    if (-not [bool]$inventory.preferred_candidates.D.hard_deficit_covered) { throw 'D duplicate Raw no longer covers temporary 20-percent hard-floor deficit.' }
    if ([bool]$inventory.preferred_candidates.D.recommended_deficit_covered) { throw 'Phase2D preflight expected a remaining preferred 30-percent coexistence gap; sizing semantics changed and require review.' }

    Write-Host 'phase2_preflight_stage=current_bindings_and_references'
    $composeBinds = @(Get-ComposeBindMounts)
    Assert-ComposeRawBindings $composeBinds $protectedVisualProcessed
    $containerMounts = @(Get-AllContainerMounts)
    $dContainerRefs = @($containerMounts | Where-Object { $_.normalized_source -and (Test-PathsOverlap $legacyRaw $_.normalized_source) })
    $dComposeRefs = @($composeBinds | Where-Object { $_.normalized_source -and (Test-PathsOverlap $legacyRaw $_.normalized_source) })
    $unexpectedContainerRefs = @($dContainerRefs | Where-Object { -not (Test-PathContains $protectedVisualProcessed $_.normalized_source) })
    $unexpectedComposeRefs = @($dComposeRefs | Where-Object { -not (Test-PathContains $protectedVisualProcessed $_.normalized_source) })
    Write-Host "phase2_d_unexpected_container_reference_count=$($unexpectedContainerRefs.Count)"
    Write-Host "phase2_d_unexpected_compose_reference_count=$($unexpectedComposeRefs.Count)"
    if ($unexpectedContainerRefs.Count -ne 0 -or $unexpectedComposeRefs.Count -ne 0) { throw 'Legacy D Raw has references outside protected visual_processed subtree.' }

    $rawEnv = if ($envValues.ContainsKey('RAW_DATA_PATH')) { Normalize-HostPath ([string]$envValues['RAW_DATA_PATH']) } else { '' }
    $visualRawEnv = if ($envValues.ContainsKey('VISUAL_RAW_PATH')) { Normalize-HostPath ([string]$envValues['VISUAL_RAW_PATH']) } else { $rawEnv }
    $visualProcessedEnv = if ($envValues.ContainsKey('VISUAL_PROCESSED_PATH')) { Normalize-HostPath ([string]$envValues['VISUAL_PROCESSED_PATH']) } else { $protectedVisualProcessed }
    if (-not $rawEnv.Equals($rawTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'RAW_DATA_PATH no longer points to accepted F Raw target.' }
    if (-not $visualRawEnv.Equals($rawTarget, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_RAW_PATH no longer points to accepted F Raw target.' }
    if (-not $visualProcessedEnv.Equals($protectedVisualProcessed, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'VISUAL_PROCESSED_PATH no longer points to protected D subtree.' }

    Write-Host 'phase2_preflight_stage=no_reparse_inventory'
    Assert-NoReparsePoints $legacyRaw
    Assert-NoReparsePoints $rawTarget

    Write-Host 'phase2_preflight_stage=full_sha256_parity'
    $sourceBefore = @(Get-RawDeletionManifest $legacyRaw $protectedVisualProcessed)
    $sourceFileCount = $sourceBefore.Count
    $sourceBytesMeasure = ($sourceBefore | Measure-Object -Property length -Sum).Sum
    $sourceBytes = if ($null -eq $sourceBytesMeasure) { [int64]0 } else { [int64]$sourceBytesMeasure }
    Write-Host "phase2_d_deletable_file_count=$sourceFileCount"
    Write-Host "phase2_d_deletable_bytes=$sourceBytes"
    if ($sourceFileCount -eq 0) { throw 'Phase2D found no deletable D duplicate files.' }

    $verified = @()
    $verifiedBytes = [int64]0
    $mismatches = [int64]0
    $index = 0
    foreach ($entry in $sourceBefore) {
        $index++
        $targetPath = Join-Path $rawTarget ([string]$entry.relative_path)
        if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) { throw "F Raw counterpart missing: $($entry.relative_path)" }
        $targetInfo = Get-Item -LiteralPath $targetPath -Force
        if ([int64]$targetInfo.Length -ne [int64]$entry.length) { throw "F Raw counterpart size mismatch: $($entry.relative_path)" }
        $sourceHash = (Get-FileHash -LiteralPath $entry.source_path -Algorithm SHA256).Hash.ToLowerInvariant()
        $targetHash = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $equal = [bool]($sourceHash -eq $targetHash)
        if ($equal) { $verifiedBytes += [int64]$entry.length } else { $mismatches++ }
        $verified += [pscustomobject]@{ relative_path=[string]$entry.relative_path; source_path=[string]$entry.source_path; target_path=[string]$targetPath; length=[int64]$entry.length; source_sha256=$sourceHash; target_sha256=$targetHash; hash_equal=$equal }
        if (($index % 100) -eq 0 -or $index -eq $sourceFileCount) { Write-Host "phase2_d_hash_progress=$index/$sourceFileCount" }
    }
    Write-Host "phase2_d_hash_mismatch_count=$mismatches"
    Write-Host "phase2_d_verified_bytes=$verifiedBytes"
    if ($mismatches -ne 0 -or $verifiedBytes -ne $sourceBytes) { throw 'Phase2D full SHA-256 parity failed.' }

    $sourceAfter = @(Get-RawDeletionManifest $legacyRaw $protectedVisualProcessed)
    $sourceStable = Compare-RawMetadataManifests $sourceBefore $sourceAfter
    Write-Host "phase2_d_source_manifest_stable=$sourceStable"
    if (-not $sourceStable) { throw 'Legacy D deletion-candidate manifest changed during SHA-256 verification.' }

    $verifiedManifestPath = Join-Path $evidenceDir 'phase2_d_verified_sha256_manifest.json'
    @($verified) | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $verifiedManifestPath -Encoding UTF8

    Assert-RawConsumersStopped
    $productionFinal = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_final=$([bool]$productionFinal.ready)"
    if (-not [bool]$productionFinal.ready) { throw 'Production ClickHouse lost health during Phase2D preflight.' }
    Assert-AcceptedProductionMount $productionFinal.container_id

    $driveD = Get-DriveSnapshot 'D'
    $requiredHardFree = [int64]([int64]$inventory.drives.D.free_bytes + [int64]$inventory.deficits.d_additional_free_hard_bytes)
    $requiredRecommendedFree = [int64]([int64]$inventory.drives.D.free_bytes + [int64]$inventory.deficits.d_additional_free_recommended_bytes)
    $projectedFree = [int64]([int64]$driveD.free_bytes + $verifiedBytes)
    $hardResidual = [int64][math]::Max([int64]0, [int64]($requiredHardFree - $projectedFree))
    $recommendedResidual = [int64][math]::Max([int64]0, [int64]($requiredRecommendedFree - $projectedFree))
    Write-Host "d_free_before_bytes=$($driveD.free_bytes)"
    Write-Host "d_required_hard_free_bytes=$requiredHardFree"
    Write-Host "d_required_recommended_free_bytes=$requiredRecommendedFree"
    Write-Host "d_projected_free_after_verified_reclaim_bytes=$projectedFree"
    Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
    Write-Host "d_recommended_residual_after_projected_bytes=$recommendedResidual"
    if ($hardResidual -ne 0) { throw 'Verified D duplicate reclaim no longer clears temporary 20-percent hard-floor coexistence target.' }

    $envHashAfter = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during read-only Phase2D preflight.' }
    Assert-ExactMain 'exit'

    $receipt = [ordered]@{
        receipt_version='PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_V1'
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_READY'
        next_gate='PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_DESIGN'
        read_only=$true
        mutation_performed=$false
        accepted_phase1_receipt_path=$phase1.path
        accepted_phase1_engine_sha=[string]$phase1.receipt.engine_sha
        fresh_candidate_receipt_path=$inventoryResult.path
        verified_sha256_manifest_path=$verifiedManifestPath
        d=[ordered]@{
            source_root=$legacyRaw
            target_root=$rawTarget
            protected_visual_processed=$protectedVisualProcessed
            deletable_file_count=[int64]$sourceFileCount
            deletable_bytes=$sourceBytes
            verified_file_count=@($verified | Where-Object { [bool]$_.hash_equal }).Count
            verified_bytes=$verifiedBytes
            hash_mismatch_count=$mismatches
            source_manifest_stable=$sourceStable
            unexpected_container_reference_count=$unexpectedContainerRefs.Count
            unexpected_compose_reference_count=$unexpectedComposeRefs.Count
            free_before_bytes=[int64]$driveD.free_bytes
            required_hard_free_bytes=$requiredHardFree
            required_recommended_free_bytes=$requiredRecommendedFree
            projected_free_after_verified_reclaim_bytes=$projectedFree
            hard_residual_after_projected_bytes=$hardResidual
            recommended_residual_after_projected_bytes=$recommendedResidual
        }
        e=[ordered]@{ recommended_deficit_bytes=[int64]$inventory.deficits.e_additional_free_recommended_bytes; hot_root_exists=[bool]$inventory.preferred_candidates.E.legacy_ntfs_clickhouse.stats.exists; logs_root_exists=[bool]$inventory.preferred_candidates.E.legacy_ntfs_clickhouse_logs.stats.exists }
        production=[ordered]@{ clickhouse_ready_before=[bool]$productionBefore.ready; clickhouse_ready_final=[bool]$productionFinal.ready; accepted_volume=$AcceptedVolume; accepted_production_mount_ready=$true; running_raw_consumer_count=0 }
        constraints=[ordered]@{
            phase2_d_file_delete_authorized=$false
            recursive_legacy_raw_root_delete_authorized=$false
            visual_processed_delete_authorized=$false
            env_write_authorized=$false
            accepted_volume_delete_authorized=$false
            accepted_volume_move_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            vhdx_create_authorized=$false
            vhdx_delete_authorized=$false
            vhdx_move_authorized=$false
            wsl_shutdown_authorized=$false
            wsl_unmount_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
        production_invariant_preserved=$true
        env_unchanged=$true
    }
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase2_d_full_sha256_preflight.json'
    $receipt | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE PHASE2 D FULL SHA256 PREFLIGHT RESULT ====='
    Write-Host 'decision=PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_READY'
    Write-Host 'next_gate=PRODUCTION_REBALANCE_PHASE2_D_RESUMABLE_APPLY_DESIGN'
    Write-Host "verified_file_count=$($receipt.d.verified_file_count)"
    Write-Host "verified_source_bytes=$verifiedBytes"
    Write-Host "hash_mismatch_count=$mismatches"
    Write-Host "source_manifest_stable=$sourceStable"
    Write-Host "d_projected_free_after_verified_reclaim_bytes=$projectedFree"
    Write-Host "d_hard_residual_after_projected_bytes=$hardResidual"
    Write-Host "d_recommended_residual_after_projected_bytes=$recommendedResidual"
    Write-Host 'mutation_performed=False'
    Write-Host 'phase2_d_file_delete_authorized=False'
    Write-Host 'recursive_legacy_raw_root_delete_authorized=False'
    Write-Host 'visual_processed_delete_authorized=False'
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_PREFLIGHT_DONE'
}
finally { Pop-Location }
