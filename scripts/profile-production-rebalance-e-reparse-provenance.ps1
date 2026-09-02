[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
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
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& $Command @Arguments 2>&1)
        $nativeExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    $renderedLines = @($outputLines | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $nativeExitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code ${nativeExitCode}: $($renderedLines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$nativeExitCode; lines=@($renderedLines) }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $headSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMainSha = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$headSha"
    Write-Host "origin/main=$originMainSha"
    Write-Host "expected=$expected"
    if ($headSha -ne $expected -or $originMainSha -ne $expected) {
        throw "Exact main drift detected during $Phase."
    }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}

function Normalize-HostPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $candidate = $Path.Trim()
    if ($candidate.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $candidate = $candidate.Substring(4)
    }
    if ($candidate.StartsWith('\??\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $candidate = $candidate.Substring(4)
    }
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

function Get-OptionalPropertyValue([object]$Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Resolve-ReparseTargetLexically([string]$LinkPath, [string]$RawTarget) {
    if ([string]::IsNullOrWhiteSpace($RawTarget)) { return '' }
    $target = $RawTarget.Trim()
    if ($target.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $target = $target.Substring(4)
    }
    if ($target.StartsWith('\??\', [System.StringComparison]::OrdinalIgnoreCase)) {
        $target = $target.Substring(4)
    }
    if ($target -match '^[A-Za-z]:[\\/]') {
        return Normalize-HostPath $target
    }
    if ([System.IO.Path]::IsPathRooted($target)) {
        return ''
    }
    $linkNormalized = Normalize-HostPath $LinkPath
    if (-not $linkNormalized) { return '' }
    $parent = [System.IO.Path]::GetDirectoryName($linkNormalized)
    if (-not $parent) { return '' }
    try { return Normalize-HostPath ([System.IO.Path]::GetFullPath((Join-Path $parent $target))) }
    catch { return '' }
}

function Get-ProductionClickHouseHealth {
    $idProbe = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids = @($idProbe['lines'] | Where-Object { $_.Trim() })
    if ($idProbe['exit_code'] -ne 0 -or $ids.Count -ne 1) {
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

function Assert-AcceptedProductionMount([string]$ContainerId) {
    $probe = Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$ContainerId) -AllowFailure
    if ($probe['exit_code'] -ne 0) { throw 'Unable to inspect production ClickHouse mounts.' }
    try { $mounts = ((@($probe['lines']) -join "`n") | ConvertFrom-Json) }
    catch { throw 'Production ClickHouse mount inspection returned invalid JSON.' }
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    $ready = [bool](
        $matches.Count -eq 1 -and
        [string]$matches[0].Type -eq 'volume' -and
        [string]$matches[0].Name -eq $AcceptedVolume
    )
    Write-Host "accepted_production_mount_ready=$ready"
    if (-not $ready) { throw 'Production ClickHouse data mount is not the accepted named volume.' }
}

function Assert-RawConsumersStopped {
    $services = @('api','worker','mark-image-worker','qcc-acquisition')
    $runningTotal = 0
    foreach ($service in $services) {
        $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure
        if ($probe['exit_code'] -ne 0) { throw "Unable to inspect Raw consumer service $service." }
        $runningCount = 0
        foreach ($containerId in @($probe['lines'] | Where-Object { $_.Trim() })) {
            $stateProbe = Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$containerId.Trim()) -AllowFailure
            if ($stateProbe['exit_code'] -ne 0) { throw "Unable to inspect Raw consumer container for $service." }
            if (((@($stateProbe['lines']) -join '').Trim().ToLowerInvariant()) -eq 'true') { $runningCount++ }
        }
        $runningTotal += $runningCount
        Write-Host "raw_consumer_service=$service running_count=$runningCount"
    }
    Write-Host "running_raw_consumer_count=$runningTotal"
    if ($runningTotal -ne 0) { throw "All Raw consumer services must be absent/stopped; observed $runningTotal running containers." }
}

function Get-ReparseEntry([string]$CandidateRoot, [string]$EntryPath) {
    $candidate = Normalize-HostPath $CandidateRoot
    $entry = Normalize-HostPath $EntryPath
    if (-not $candidate -or -not $entry) { throw 'Unable to normalize reparse provenance path.' }

    $item = Get-Item -LiteralPath $entry -Force
    $linkTypeValue = Get-OptionalPropertyValue $item 'LinkType'
    $targetValue = Get-OptionalPropertyValue $item 'Target'
    $rawTargets = @()
    foreach ($targetEntry in @($targetValue)) {
        $rendered = [string]$targetEntry
        if (-not [string]::IsNullOrWhiteSpace($rendered)) { $rawTargets += $rendered }
    }

    $rawTarget = if ($rawTargets.Count -eq 1) { $rawTargets[0] } else { $null }
    $lexicalTarget = if ($rawTargets.Count -eq 1) { Resolve-ReparseTargetLexically $entry $rawTarget } else { '' }
    $targetExists = [bool]($lexicalTarget -and (Test-Path -LiteralPath $lexicalTarget))
    $targetInsideCandidate = [bool]($lexicalTarget -and (Test-PathContains $candidate $lexicalTarget))
    $targetDrive = if ($lexicalTarget -match '^([A-Za-z]):') { $Matches[1].ToUpperInvariant() + ':' } else { $null }

    $fsutil = Invoke-NativeText 'fsutil.exe' @('reparsepoint','query',$entry) -AllowFailure
    return [ordered]@{
        candidate_root=$candidate
        path=$entry
        link_type=if ($null -eq $linkTypeValue) { $null } else { [string]$linkTypeValue }
        raw_target_count=$rawTargets.Count
        raw_targets=@($rawTargets)
        lexical_target=$lexicalTarget
        lexical_target_drive=$targetDrive
        target_exists=$targetExists
        lexical_target_inside_candidate_root=$targetInsideCandidate
        dangling=[bool]($lexicalTarget -and -not $targetExists)
        target_unresolved=[bool](-not $lexicalTarget)
        fsutil_exit_code=[int]$fsutil['exit_code']
        fsutil_output=@($fsutil['lines'])
    }
}

function Get-ReparsePointInventory([string]$Root) {
    $normalized = Normalize-HostPath $Root
    $result = [ordered]@{
        root=$normalized
        exists=$false
        root_is_reparse_point=$false
        reparse_points=@()
        reparse_point_count=0
        enumeration_complete=$true
        enumeration_error=$null
    }
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized)) { return $result }
    $result.exists = $true

    try {
        $rootAttributes = [System.IO.File]::GetAttributes($normalized)
        if (($rootAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            $result.root_is_reparse_point = $true
            $result.reparse_points = @((Get-ReparseEntry $normalized $normalized))
            $result.reparse_point_count = 1
            return $result
        }
        if (($rootAttributes -band [System.IO.FileAttributes]::Directory) -eq 0) { return $result }

        $found = @()
        $stack = New-Object 'System.Collections.Generic.Stack[string]'
        $stack.Push($normalized)
        while ($stack.Count -gt 0) {
            $directory = $stack.Pop()
            foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
                $attributes = [System.IO.File]::GetAttributes($entry)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    $found += (Get-ReparseEntry $normalized $entry)
                    continue
                }
                if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { $stack.Push($entry) }
            }
        }
        $result.reparse_points = @($found)
        $result.reparse_point_count = @($found).Count
    }
    catch {
        $result.enumeration_complete = $false
        $result.enumeration_error = $_.Exception.Message
    }
    return $result
}

function Get-ProvenanceClassification([object[]]$Inventories) {
    $points = @()
    $complete = $true
    foreach ($inventory in $Inventories) {
        if (-not [bool]$inventory.enumeration_complete) { $complete = $false }
        $points += @($inventory.reparse_points)
    }
    if (-not $complete) { return 'REBALANCE_E_REPARSE_PROVENANCE_ENUMERATION_INCOMPLETE' }
    if ($points.Count -eq 0) { return 'REBALANCE_E_REPARSE_PROVENANCE_NONE' }

    $unresolved = @($points | Where-Object {
        [bool]$_.target_unresolved -or [int64]$_.raw_target_count -ne 1 -or -not $_.link_type
    })
    if ($unresolved.Count -gt 0) { return 'REBALANCE_E_REPARSE_PROVENANCE_UNRESOLVED' }

    $dangling = @($points | Where-Object { [bool]$_.dangling })
    if ($dangling.Count -gt 0) { return 'REBALANCE_E_REPARSE_PROVENANCE_DANGLING' }

    $escaping = @($points | Where-Object { -not [bool]$_.lexical_target_inside_candidate_root })
    if ($escaping.Count -gt 0) { return 'REBALANCE_E_REPARSE_PROVENANCE_ESCAPES_DELETION_ROOT' }

    return 'REBALANCE_E_REPARSE_PROVENANCE_INTERNAL_LEXICAL_TARGETS'
}

try {
    Write-Host '===== PRODUCTION REBALANCE E REPARSE PROVENANCE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'E reparse provenance must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'E reparse provenance requires elevated Administrator PowerShell.'
    }

    $hot = Normalize-HostPath $LegacyEHotRoot
    $logs = Normalize-HostPath $LegacyEHotLogsRoot
    if (-not $hot.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'LegacyEHotRoot must remain the exact approved legacy E ClickHouse root.'
    }
    if (-not $logs.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'LegacyEHotLogsRoot must remain the exact approved legacy E ClickHouse log root.'
    }

    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$productionBefore['ready'])"
    Write-Host "production_clickhouse_health_before=$($productionBefore['health'])"
    if (-not [bool]$productionBefore['ready']) { throw 'Production ClickHouse must be healthy before E reparse provenance.' }
    Assert-AcceptedProductionMount $productionBefore['container_id']

    Write-Host 'reparse_provenance_stage=non_traversing_inventory'
    $hotInventory = Get-ReparsePointInventory $hot
    $logsInventory = Get-ReparsePointInventory $logs
    if (-not [bool]$hotInventory.enumeration_complete -or -not [bool]$logsInventory.enumeration_complete) {
        Write-Host "hot_enumeration_error=$($hotInventory.enumeration_error)"
        Write-Host "logs_enumeration_error=$($logsInventory.enumeration_error)"
    }

    foreach ($inventory in @($hotInventory,$logsInventory)) {
        Write-Host "reparse_root=$($inventory.root)"
        Write-Host "reparse_root_exists=$([bool]$inventory.exists)"
        Write-Host "reparse_point_count=$([int64]$inventory.reparse_point_count)"
        foreach ($point in @($inventory.reparse_points)) {
            Write-Host "reparse_path=$($point.path)"
            Write-Host "reparse_link_type=$($point.link_type)"
            Write-Host "reparse_raw_target_count=$([int64]$point.raw_target_count)"
            Write-Host "reparse_raw_target=$((@($point.raw_targets) -join ' | '))"
            Write-Host "reparse_lexical_target=$($point.lexical_target)"
            Write-Host "reparse_target_exists=$([bool]$point.target_exists)"
            Write-Host "reparse_target_inside_candidate_root=$([bool]$point.lexical_target_inside_candidate_root)"
            Write-Host "reparse_dangling=$([bool]$point.dangling)"
            Write-Host "reparse_target_unresolved=$([bool]$point.target_unresolved)"
            Write-Host "reparse_fsutil_exit_code=$([int]$point.fsutil_exit_code)"
        }
    }

    $decision = Get-ProvenanceClassification @($hotInventory,$logsInventory)
    $nextGate = if ($decision -eq 'REBALANCE_E_REPARSE_PROVENANCE_INTERNAL_LEXICAL_TARGETS') {
        'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_DESIGN'
    } elseif ($decision -eq 'REBALANCE_E_REPARSE_PROVENANCE_NONE') {
        'PRODUCTION_REBALANCE_PHASE1_E_DRY_RUN_RETRY'
    } else {
        'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_REVIEW_REQUIRED'
    }

    $productionAfter = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_after=$([bool]$productionAfter['ready'])"
    Write-Host "production_clickhouse_health_after=$($productionAfter['health'])"
    if (-not [bool]$productionAfter['ready']) { throw 'Production ClickHouse must remain healthy after E reparse provenance.' }
    Assert-AcceptedProductionMount $productionAfter['container_id']
    Assert-RawConsumersStopped

    $envHashAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during read-only E reparse provenance.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_e_reparse_provenance_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_e_reparse_provenance.json'
    $receipt = [ordered]@{
        receipt_version='PRODUCTION_REBALANCE_E_REPARSE_PROVENANCE_V1'
        decision=$decision
        next_gate=$nextGate
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        read_only=$true
        non_traversing_inventory=$true
        legacy_e_hot=$hotInventory
        legacy_e_logs=$logsInventory
        production=[ordered]@{
            clickhouse_ready_before=[bool]$productionBefore['ready']
            clickhouse_ready_after=[bool]$productionAfter['ready']
            accepted_volume=$AcceptedVolume
            accepted_production_mount_ready=$true
            running_raw_consumer_count=0
        }
        constraints=[ordered]@{
            phase1_delete_authorized=$false
            reparse_delete_authorized=$false
            legacy_e_hot_delete_authorized=$false
            legacy_raw_delete_authorized=$false
            accepted_volume_delete_authorized=$false
            accepted_volume_move_authorized=$false
            docker_cold_backup_delete_authorized=$false
            docker_cold_backup_move_authorized=$false
            vhdx_create_authorized=$false
            vhdx_delete_authorized=$false
            vhdx_move_authorized=$false
            wsl_shutdown_authorized=$false
            wsl_unmount_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
            mutation_performed=$false
        }
        env_unchanged=$envUnchanged
    }
    $receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE E REPARSE PROVENANCE RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "hot_reparse_point_count=$([int64]$hotInventory.reparse_point_count)"
    Write-Host "logs_reparse_point_count=$([int64]$logsInventory.reparse_point_count)"
    Write-Host 'phase1_delete_authorized=False'
    Write-Host 'reparse_delete_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host "production_invariant_preserved=$([bool]($productionBefore['ready'] -and $productionAfter['ready']))"
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_E_REPARSE_PROVENANCE_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
