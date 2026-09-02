[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [int]$ExpectedReparsePointCount = 63,
    [string]$EvidenceRoot = 'reports'
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

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $headSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMainSha = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$headSha"
    Write-Host "origin/main=$originMainSha"
    Write-Host "expected=$expected"
    if ($headSha -ne $expected -or $originMainSha -ne $expected) { throw "Exact main drift detected during $Phase." }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
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
    $mounts = ((@($probe.lines) -join "`n") | ConvertFrom-Json)
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
    if ($runningTotal -ne 0) { throw "All Raw consumer services must be absent/stopped; observed $runningTotal." }
}

function Get-AllContainerMounts {
    $idsProbe = Invoke-NativeText 'docker' @('ps','-a','-q') -AllowFailure
    if ($idsProbe.exit_code -ne 0) { throw 'Unable to enumerate Docker containers.' }
    $entries = @()
    foreach ($containerId in @($idsProbe.lines | Where-Object { $_.Trim() })) {
        $trimmedId = $containerId.Trim()
        $inspectProbe = Invoke-NativeText 'docker' @('inspect','--format','{{json .}}',$trimmedId) -AllowFailure
        if ($inspectProbe.exit_code -ne 0) { throw "Unable to inspect container $trimmedId." }
        $inspectJson = (@($inspectProbe.lines) -join "`n").Trim()
        if (-not $inspectJson) { throw "Docker inspect produced no JSON for $trimmedId." }
        try { $container = $inspectJson | ConvertFrom-Json }
        catch { throw "Docker inspect produced invalid JSON for ${trimmedId}: $($_.Exception.Message)" }
        $state = Get-OptionalPropertyValue $container 'State'
        if ($null -eq $state) { throw "Docker inspect omitted State for $trimmedId." }
        $runningValue = Get-OptionalPropertyValue $state 'Running'
        if ($null -eq $runningValue) { throw "Docker inspect omitted State.Running for $trimmedId." }
        foreach ($mount in @(Get-OptionalArrayProperty $container 'Mounts')) {
            $source = [string](Get-OptionalPropertyValue $mount 'Source')
            $entries += [ordered]@{
                container_id=[string](Get-OptionalPropertyValue $container 'Id')
                container_name=([string](Get-OptionalPropertyValue $container 'Name')).TrimStart('/')
                running=[bool]$runningValue
                mount_type=[string](Get-OptionalPropertyValue $mount 'Type')
                source=$source
                normalized_source=(Normalize-HostPath $source)
                destination=[string](Get-OptionalPropertyValue $mount 'Destination')
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
            $entries += [ordered]@{ service=[string]$serviceProperty.Name; source=$source; normalized_source=(Normalize-HostPath $source); target=$target }
        }
    }
    return @($entries)
}

function Get-PathReferences([string]$CandidatePath, [object[]]$ContainerMounts, [object[]]$ComposeBinds) {
    $allContainer = @($ContainerMounts | Where-Object { $_.normalized_source -and (Test-PathsOverlap $CandidatePath $_.normalized_source) })
    $runningContainer = @($allContainer | Where-Object { [bool]$_.running })
    $compose = @($ComposeBinds | Where-Object { $_.normalized_source -and (Test-PathsOverlap $CandidatePath $_.normalized_source) })
    return [ordered]@{
        all_container_reference_count=[int64]$allContainer.Count
        running_container_reference_count=[int64]$runningContainer.Count
        compose_reference_count=[int64]$compose.Count
        all_container_references=@($allContainer)
        running_container_references=@($runningContainer)
        compose_references=@($compose)
    }
}

function Convert-RelativePathToBase64([string]$RelativePath) {
    return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($RelativePath))
}

function Get-RelativeDepth([string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath)) { return 0 }
    return @($RelativePath -split '[\\/]').Count
}

function Get-NonTraversingDeletionInventory {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ManifestPath
    )
    $normalized = Normalize-HostPath $Root
    $result = [ordered]@{
        root=$normalized
        exists=$false
        root_is_directory=$false
        root_is_reparse_point=$false
        enumeration_complete=$true
        enumeration_error=$null
        regular_file_count=[int64]0
        regular_file_bytes=[int64]0
        regular_directory_count=[int64]0
        reparse_point_count=[int64]0
        manifest_entry_count=[int64]0
        max_relative_depth=[int64]0
        reparse_paths=@()
        manifest_path=$ManifestPath
        manifest_sha256=$null
        non_traversing=$true
        deletion_strategy='POSTORDER_NORMAL_OBJECTS_NATIVE_UNLINK_REPARSE_NO_FOLLOW'
    }

    $manifestDirectory = Split-Path -Parent $ManifestPath
    if ($manifestDirectory) { New-Item -ItemType Directory -Force -Path $manifestDirectory | Out-Null }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $writer = New-Object System.IO.StreamWriter($ManifestPath, $false, $encoding)
    try {
        if (-not $normalized -or -not (Test-Path -LiteralPath $normalized)) { return $result }
        $result.exists = $true
        $rootAttributes = [System.IO.File]::GetAttributes($normalized)
        $result.root_is_reparse_point = [bool](($rootAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
        $result.root_is_directory = [bool](($rootAttributes -band [System.IO.FileAttributes]::Directory) -ne 0)
        if ($result.root_is_reparse_point -or -not $result.root_is_directory) { return $result }

        $prefix = $normalized + '\'
        $stack = New-Object 'System.Collections.Generic.Stack[string]'
        $stack.Push($normalized)
        while ($stack.Count -gt 0) {
            $directory = $stack.Pop()
            $entries = [string[]]@([System.IO.Directory]::EnumerateFileSystemEntries($directory))
            [Array]::Sort($entries, [System.StringComparer]::OrdinalIgnoreCase)
            foreach ($entry in $entries) {
                $fullPath = [System.IO.Path]::GetFullPath($entry)
                if (-not (Test-PathContains $normalized $fullPath)) { throw "Deletion inventory escaped approved root: $fullPath" }
                $relative = if ($fullPath.Length -gt $prefix.Length) { $fullPath.Substring($prefix.Length) } else { '' }
                $depth = Get-RelativeDepth $relative
                if ($depth -gt $result.max_relative_depth) { $result.max_relative_depth = [int64]$depth }
                $attributes = [System.IO.File]::GetAttributes($fullPath)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    $result.reparse_point_count++
                    $result.manifest_entry_count++
                    $result.reparse_paths += $fullPath
                    $writer.WriteLine("R`t0`t$([int64]$attributes)`t$(Convert-RelativePathToBase64 $relative)")
                    continue
                }
                if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                    $result.regular_directory_count++
                    $result.manifest_entry_count++
                    $writer.WriteLine("D`t0`t$([int64]$attributes)`t$(Convert-RelativePathToBase64 $relative)")
                    $stack.Push($fullPath)
                    continue
                }
                $info = New-Object System.IO.FileInfo($fullPath)
                $result.regular_file_count++
                $result.regular_file_bytes += [int64]$info.Length
                $result.manifest_entry_count++
                $writer.WriteLine("F`t$([int64]$info.Length)`t$([int64]$attributes)`t$(Convert-RelativePathToBase64 $relative)")
            }
        }
    }
    catch {
        $result.enumeration_complete = $false
        $result.enumeration_error = $_.Exception.Message
    }
    finally {
        $writer.Dispose()
    }
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) {
        $result.manifest_sha256 = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $result
}

function Compare-PathSets {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Expected,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Actual
    )
    $expectedMap = @{}
    $actualMap = @{}
    $expectedDuplicateCount = 0
    $actualDuplicateCount = 0
    foreach ($path in @($Expected)) {
        $normalized = Normalize-HostPath $path
        if (-not $normalized) { throw "Expected path set contains non-host path: $path" }
        $key = $normalized.ToLowerInvariant()
        if ($expectedMap.ContainsKey($key)) { $expectedDuplicateCount++ } else { $expectedMap[$key] = $normalized }
    }
    foreach ($path in @($Actual)) {
        $normalized = Normalize-HostPath $path
        if (-not $normalized) { throw "Actual path set contains non-host path: $path" }
        $key = $normalized.ToLowerInvariant()
        if ($actualMap.ContainsKey($key)) { $actualDuplicateCount++ } else { $actualMap[$key] = $normalized }
    }
    $missing = @()
    foreach ($key in @($expectedMap.Keys)) { if (-not $actualMap.ContainsKey($key)) { $missing += [string]$expectedMap[$key] } }
    $unexpected = @()
    foreach ($key in @($actualMap.Keys)) { if (-not $expectedMap.ContainsKey($key)) { $unexpected += [string]$actualMap[$key] } }
    $missing = @($missing | Sort-Object)
    $unexpected = @($unexpected | Sort-Object)
    $exact = [bool]($expectedDuplicateCount -eq 0 -and $actualDuplicateCount -eq 0 -and $missing.Count -eq 0 -and $unexpected.Count -eq 0 -and $expectedMap.Count -eq $actualMap.Count)
    return [ordered]@{
        exact=$exact
        expected_count=[int64]$expectedMap.Count
        actual_count=[int64]$actualMap.Count
        expected_duplicate_count=[int64]$expectedDuplicateCount
        actual_duplicate_count=[int64]$actualDuplicateCount
        missing_count=[int64]$missing.Count
        unexpected_count=[int64]$unexpected.Count
        missing_paths=@($missing)
        unexpected_paths=@($unexpected)
    }
}

function Invoke-FreshLxMappingReceipt([string]$RunId) {
    if ($RunId -notmatch '^[0-9A-Za-z_-]+$') { throw 'RunId contains unsupported characters.' }
    $childRelativeRoot = Join-Path (Join-Path 'reports' '_esafe') $RunId
    $childAbsoluteRoot = Join-Path $repoRoot $childRelativeRoot
    New-Item -ItemType Directory -Force -Path $childAbsoluteRoot | Out-Null
    Write-Host 'safe_delete_stage=fresh_lx_mapping_receipt'
    Write-Host 'lx_mapping_evidence_strategy=SHALLOW_REPO_REPORTS'
    Write-Host "lx_mapping_evidence_root=$childAbsoluteRoot"
    $scriptPath = Join-Path $PSScriptRoot 'profile-production-rebalance-e-lx-target-mapping.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
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
    if ($childExitCode -ne 0) { throw "Fresh LX target mapping exited $childExitCode." }
    $directories = @(Get-ChildItem -LiteralPath $childAbsoluteRoot -Directory -Filter 'production_rebalance_e_lx_target_mapping_*' -Recurse | Sort-Object LastWriteTime -Descending)
    if ($directories.Count -ne 1) { throw "Expected exactly one fresh LX target mapping directory; observed $($directories.Count)." }
    $receiptPath = Join-Path $directories[0].FullName 'production_rebalance_e_lx_target_mapping.json'
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'Fresh LX target mapping receipt is missing.' }
    try { $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Fresh LX target mapping receipt is invalid JSON: $($_.Exception.Message)" }
    return [ordered]@{ path=$receiptPath; receipt=$receipt; evidence_root=$childAbsoluteRoot }
}

try {
    Write-Host '===== PRODUCTION REBALANCE PHASE1 E REPARSE SAFE DELETE PREFLIGHT ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase1E reparse-safe preflight must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase1E reparse-safe preflight requires elevated Administrator PowerShell.' }
    if ($ExpectedReparsePointCount -lt 0) { throw 'ExpectedReparsePointCount must be non-negative.' }

    $hot = Normalize-HostPath $LegacyEHotRoot
    $logs = Normalize-HostPath $LegacyEHotLogsRoot
    if (-not $hot.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot must remain the exact approved legacy E ClickHouse root.' }
    if (-not $logs.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot must remain the exact approved legacy E ClickHouse log root.' }
    if (Test-PathsOverlap $hot $logs) { throw 'Legacy E data/log roots must remain disjoint.' }

    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $runId = "${timestamp}_$PID"
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase1_e_reparse_safe_preflight_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$productionBefore.ready)"
    Write-Host "production_clickhouse_health_before=$($productionBefore.health)"
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before Phase1E safe-delete preflight.' }
    Assert-AcceptedProductionMount $productionBefore.container_id

    $mappingResult = Invoke-FreshLxMappingReceipt $runId
    $mapping = $mappingResult.receipt
    Write-Host "fresh_lx_mapping_receipt=$($mappingResult.path)"

    $blockers = @()
    if ([string]$mapping.receipt_version -ne 'PRODUCTION_REBALANCE_E_LX_TARGET_MAPPING_V1') { $blockers += 'LX_MAPPING_RECEIPT_VERSION_MISMATCH' }
    if ([string]$mapping.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { $blockers += 'LX_MAPPING_RECEIPT_SHA_MISMATCH' }
    if (-not [bool]$mapping.read_only) { $blockers += 'LX_MAPPING_RECEIPT_NOT_READ_ONLY' }
    if ([string]$mapping.decision -ne 'REBALANCE_E_LX_MAPPING_INTERNAL_TARGETS') { $blockers += 'LX_MAPPING_NOT_INTERNAL_TARGETS' }
    if ([string]$mapping.next_gate -ne 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_DESIGN') { $blockers += 'LX_MAPPING_NEXT_GATE_MISMATCH' }
    if ([int64]$mapping.point_count -ne [int64]$ExpectedReparsePointCount) { $blockers += 'LX_MAPPING_POINT_COUNT_CHANGED' }
    if ([int64]$mapping.mapping_ready_count -ne [int64]$mapping.point_count) { $blockers += 'LX_MAPPING_NOT_ALL_READY' }
    foreach ($field in @('native_blocked_count','version_blocked_count','prefix_blocked_count','missing_target_count','reparse_target_count','outside_target_count','mapping_error_count')) {
        if ([int64](Get-OptionalPropertyValue $mapping $field) -ne 0) { $blockers += "LX_MAPPING_$($field.ToUpperInvariant())_NONZERO" }
    }
    if (-not [bool]$mapping.production.accepted_production_mount_ready -or [int64]$mapping.production.running_raw_consumer_count -ne 0) { $blockers += 'LX_MAPPING_PRODUCTION_BOUNDARY_NOT_PRESERVED' }
    if (-not [bool]$mapping.env_unchanged -or [bool]$mapping.constraints.mutation_performed) { $blockers += 'LX_MAPPING_READ_ONLY_BOUNDARY_NOT_PRESERVED' }

    Write-Host 'safe_delete_stage=non_traversing_physical_manifest'
    $hotManifestPath = Join-Path $evidenceDir 'phase1_e_hot_non_traversing_manifest.tsv'
    $logsManifestPath = Join-Path $evidenceDir 'phase1_e_logs_non_traversing_manifest.tsv'
    $hotInventory = Get-NonTraversingDeletionInventory -Root $hot -ManifestPath $hotManifestPath
    $logsInventory = Get-NonTraversingDeletionInventory -Root $logs -ManifestPath $logsManifestPath
    foreach ($inventory in @($hotInventory,$logsInventory)) {
        Write-Host "safe_delete_root=$($inventory.root)"
        Write-Host "safe_delete_root_exists=$([bool]$inventory.exists)"
        Write-Host "safe_delete_root_is_directory=$([bool]$inventory.root_is_directory)"
        Write-Host "safe_delete_root_is_reparse_point=$([bool]$inventory.root_is_reparse_point)"
        Write-Host "safe_delete_regular_file_count=$([int64]$inventory.regular_file_count)"
        Write-Host "safe_delete_regular_file_bytes=$([int64]$inventory.regular_file_bytes)"
        Write-Host "safe_delete_regular_directory_count=$([int64]$inventory.regular_directory_count)"
        Write-Host "safe_delete_reparse_point_count=$([int64]$inventory.reparse_point_count)"
        Write-Host "safe_delete_manifest_entry_count=$([int64]$inventory.manifest_entry_count)"
        Write-Host "safe_delete_manifest_sha256=$($inventory.manifest_sha256)"
        Write-Host "safe_delete_enumeration_complete=$([bool]$inventory.enumeration_complete)"
    }
    if (-not [bool]$hotInventory.exists -or -not [bool]$hotInventory.root_is_directory -or [bool]$hotInventory.root_is_reparse_point) { $blockers += 'LEGACY_E_HOT_ROOT_INVALID' }
    if (-not [bool]$logsInventory.exists -or -not [bool]$logsInventory.root_is_directory -or [bool]$logsInventory.root_is_reparse_point) { $blockers += 'LEGACY_E_LOGS_ROOT_INVALID' }
    if (-not [bool]$hotInventory.enumeration_complete -or -not [bool]$logsInventory.enumeration_complete) { $blockers += 'NON_TRAVERSING_ENUMERATION_INCOMPLETE' }

    $expectedReparsePaths = @($mapping.points | ForEach-Object { [string]$_.path })
    $actualReparsePaths = @($hotInventory.reparse_paths) + @($logsInventory.reparse_paths)
    $reparseSet = Compare-PathSets -Expected $expectedReparsePaths -Actual $actualReparsePaths
    Write-Host "accepted_reparse_set_exact=$([bool]$reparseSet.exact)"
    Write-Host "accepted_reparse_expected_count=$([int64]$reparseSet.expected_count)"
    Write-Host "accepted_reparse_actual_count=$([int64]$reparseSet.actual_count)"
    Write-Host "accepted_reparse_missing_count=$([int64]$reparseSet.missing_count)"
    Write-Host "accepted_reparse_unexpected_count=$([int64]$reparseSet.unexpected_count)"
    Write-Host "accepted_reparse_expected_duplicate_count=$([int64]$reparseSet.expected_duplicate_count)"
    Write-Host "accepted_reparse_actual_duplicate_count=$([int64]$reparseSet.actual_duplicate_count)"
    if (-not [bool]$reparseSet.exact) { $blockers += 'REPARSE_PATH_SET_MISMATCH' }
    if ([int64]$reparseSet.actual_count -ne [int64]$ExpectedReparsePointCount) { $blockers += 'ACTUAL_REPARSE_POINT_COUNT_CHANGED' }

    Write-Host 'safe_delete_stage=reference_boundary'
    $containerMounts = @(Get-AllContainerMounts)
    $composeBinds = @(Get-ComposeBindMounts)
    $hotRefs = Get-PathReferences $hot $containerMounts $composeBinds
    $logsRefs = Get-PathReferences $logs $containerMounts $composeBinds
    Write-Host "safe_delete_hot_container_reference_count=$([int64]$hotRefs.all_container_reference_count)"
    Write-Host "safe_delete_hot_compose_reference_count=$([int64]$hotRefs.compose_reference_count)"
    Write-Host "safe_delete_logs_container_reference_count=$([int64]$logsRefs.all_container_reference_count)"
    Write-Host "safe_delete_logs_compose_reference_count=$([int64]$logsRefs.compose_reference_count)"
    if ([int64]$hotRefs.all_container_reference_count -ne 0 -or [int64]$hotRefs.compose_reference_count -ne 0) { $blockers += 'LEGACY_E_HOT_REFERENCE_PRESENT' }
    if ([int64]$logsRefs.all_container_reference_count -ne 0 -or [int64]$logsRefs.compose_reference_count -ne 0) { $blockers += 'LEGACY_E_LOGS_REFERENCE_PRESENT' }

    Assert-RawConsumersStopped
    $productionAfter = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_after=$([bool]$productionAfter.ready)"
    Write-Host "production_clickhouse_health_after=$($productionAfter.health)"
    if (-not [bool]$productionAfter.ready) { throw 'Production ClickHouse must remain healthy after Phase1E safe-delete preflight.' }
    Assert-AcceptedProductionMount $productionAfter.container_id

    $envHashAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during Phase1E reparse-safe preflight.' }

    $blockers = @($blockers | Select-Object -Unique)
    $ready = [bool]($blockers.Count -eq 0)
    $decision = if ($ready) { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_READY' } else { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_BLOCKED' }
    $nextGate = if ($ready) { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY_DESIGN' } else { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_REVIEW_REQUIRED' }

    $receipt = [ordered]@{
        receipt_version='PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_V1'
        decision=$decision
        next_gate=$nextGate
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        read_only=$true
        ready_for_safe_delete_apply_design=$ready
        expected_reparse_point_count=[int64]$ExpectedReparsePointCount
        fresh_lx_mapping_receipt_path=$mappingResult.path
        fresh_lx_mapping_decision=[string]$mapping.decision
        deletion_contract=[ordered]@{
            exact_hot_root=$hot
            exact_logs_root=$logs
            traversal_policy='DO_NOT_DESCEND_INTO_REPARSE_POINTS'
            normal_object_order='POSTORDER_CHILDREN_BEFORE_PARENT'
            reparse_object_action='NATIVE_UNLINK_OBJECT_ONLY_NO_TARGET_DEREFERENCE'
            manifest_encoding='TYPE_LENGTH_ATTRIBUTES_BASE64_RELATIVE_PATH'
            require_same_main_manifest_regeneration_before_apply=$true
        }
        hot=$hotInventory
        logs=$logsInventory
        accepted_reparse_set=$reparseSet
        references=[ordered]@{ hot=$hotRefs; logs=$logsRefs }
        production=[ordered]@{
            clickhouse_ready_before=[bool]$productionBefore.ready
            clickhouse_ready_after=[bool]$productionAfter.ready
            accepted_volume=$AcceptedVolume
            accepted_production_mount_ready=$true
            running_raw_consumer_count=0
        }
        blockers=@($blockers)
        blocker_count=[int64]$blockers.Count
        constraints=[ordered]@{
            phase1_delete_authorized=$false
            reparse_delete_authorized=$false
            legacy_e_hot_delete_authorized=$false
            legacy_e_logs_delete_authorized=$false
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
            mutation_performed=$false
        }
        env_unchanged=$envUnchanged
    }
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase1_e_reparse_safe_delete_preflight.json'
    $receipt | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE PHASE1 E REPARSE SAFE DELETE PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "ready_for_safe_delete_apply_design=$ready"
    Write-Host "hot_regular_file_count=$([int64]$hotInventory.regular_file_count)"
    Write-Host "hot_regular_file_bytes=$([int64]$hotInventory.regular_file_bytes)"
    Write-Host "hot_regular_directory_count=$([int64]$hotInventory.regular_directory_count)"
    Write-Host "hot_reparse_point_count=$([int64]$hotInventory.reparse_point_count)"
    Write-Host "hot_manifest_sha256=$($hotInventory.manifest_sha256)"
    Write-Host "logs_regular_file_count=$([int64]$logsInventory.regular_file_count)"
    Write-Host "logs_regular_file_bytes=$([int64]$logsInventory.regular_file_bytes)"
    Write-Host "logs_regular_directory_count=$([int64]$logsInventory.regular_directory_count)"
    Write-Host "logs_reparse_point_count=$([int64]$logsInventory.reparse_point_count)"
    Write-Host "logs_manifest_sha256=$($logsInventory.manifest_sha256)"
    Write-Host "accepted_reparse_set_exact=$([bool]$reparseSet.exact)"
    Write-Host "blocker_count=$([int64]$blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'phase1_delete_authorized=False'
    Write-Host 'reparse_delete_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host "production_invariant_preserved=$([bool]($productionBefore.ready -and $productionAfter.ready))"
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
