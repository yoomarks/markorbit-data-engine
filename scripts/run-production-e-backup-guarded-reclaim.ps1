[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedProvenanceReceiptPath,
    [string]$AcceptedBoundaryReceiptPath,
    [string]$ResumeJournalPath,
    [switch]$Apply,
    [switch]$AcknowledgeSupersededEBackupDelete,
    [switch]$AcknowledgeResumePartialReclaim,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',
    [string]$CurrentDockerDesktopWslRoot = 'D:\DockerData\DockerDesktopWSL',
    [string]$ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedProvenanceEngineSha = 'a371fcbc2a35bd67f64ca1954ed497ed9ebe5444'
$script:AcceptedProvenanceReceiptVersion = 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_V1'
$script:BoundaryReceiptVersion = 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_BOUNDARY_V1'
$script:JournalVersion = 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_JOURNAL_V1'
$script:ApplyReceiptVersion = 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_APPLY_V1'
$script:AllowedToolingFiles = @(
    'scripts/run-production-e-backup-guarded-reclaim.ps1',
    'tests/test_production_e_backup_guarded_reclaim_contract.py',
    '.github/workflows/production-e-backup-guarded-reclaim-runtime.yml'
)
$script:ExpectedRelativePaths = @(
    'settings-store.json',
    'disk\docker_data.empty.vhdx',
    'main\ext4.vhdx',
    'disk\docker_data.vhdx'
)

function Import-FunctionDefinitions([string]$Path, [string[]]$Names, [string]$Label) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "$Label helper source no longer parses." }
    $functions = $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Names -contains $node.Name
    }, $true)
    foreach ($name in $Names) {
        $matches = @($functions | Where-Object { $_.Name -eq $name })
        if ($matches.Count -ne 1) { throw "Expected exactly one $Label helper definition: $name" }
        $definitionText = [string]$matches[0].Extent.Text
        $pattern = '^(\s*function\s+)' + [regex]::Escape($name) + '(?=\s*(?:\(|\{))'
        $replacement = '${1}script:' + $name
        $scriptScopedDefinition = [regex]::Replace(
            $definitionText,
            $pattern,
            $replacement,
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        if ($scriptScopedDefinition -eq $definitionText) { throw "Unable to scope $Label helper definition: $name" }
        Invoke-Expression $scriptScopedDefinition
    }
}

function Import-AcceptedHelpers {
    Import-FunctionDefinitions `
        (Join-Path $PSScriptRoot 'preflight-production-rebalance-phase2-d-full-sha256.ps1') `
        @(
            'Invoke-NativeText','Assert-ExactMain','Normalize-HostPath','Test-PathContains','Test-PathsOverlap',
            'Get-OptionalPropertyValue','Get-OptionalArrayProperty','Get-DotEnvValues',
            'Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped',
            'Get-AllContainerMounts','Get-ComposeBindMounts'
        ) `
        'Phase2D'

    Import-FunctionDefinitions `
        (Join-Path $PSScriptRoot 'preflight-production-storage-reserve-exception-review.ps1') `
        @('Get-Sha256','Read-JsonFile','Get-WslBasePaths','Get-BackupReferenceInventory') `
        'layout-replan'

    Import-FunctionDefinitions `
        (Join-Path $PSScriptRoot 'preflight-production-e-backup-reclaim.ps1') `
        @('Initialize-AllocatedSizeNative','Get-AllocatedFileBytes','Get-RelativePathSafe','Get-DirectoryInventoryNoFollowAllocated') `
        'E-backup-preflight'

    Import-FunctionDefinitions `
        (Join-Path $PSScriptRoot 'preflight-production-e-backup-functional-recovery-provenance.ps1') `
        @('Get-ProcessBackupReferences','Get-CurrentBackupReferences','Get-DiskImageEvidence','Get-BackupRole') `
        'functional-provenance'
}

function Assert-ToolingProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedProvenanceEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted functional provenance SHA is not an ancestor of current exact main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedProvenanceEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedToolingFiles })
    $missing = @($script:AllowedToolingFiles | Where-Object { $_ -notin $changed })
    Write-Host "accepted_provenance_to_current_changed_file_count=$($changed.Count)"
    Write-Host "accepted_provenance_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "accepted_provenance_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'Guarded E reclaim tooling changed outside the exact 3-file boundary.'
    }
}

function Resolve-AcceptedProvenanceReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedProvenanceReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted E backup functional provenance receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedProvenanceReceiptVersion) { throw 'Unexpected functional provenance receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedProvenanceEngineSha) { throw 'Functional provenance engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_READY_FOR_OPERATOR_DECISION') { throw 'Functional provenance receipt is not ready for operator decision.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_E_BACKUP_RECLAIM_OPERATOR_ACK_REVIEW') { throw 'Functional provenance next gate changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Functional provenance receipt lost read-only contract.' }
    if (-not [bool]$receipt.e_backup.inventory_stable -or -not [bool]$receipt.e_backup.expected_role_set_ready -or -not [bool]$receipt.e_backup.current_counterparts_ready -or -not [bool]$receipt.e_backup.all_detached_proof_ready) { throw 'Functional provenance safety evidence is incomplete.' }
    if ([bool]$receipt.e_backup.delete_authorized) { throw 'Functional provenance unexpectedly authorized deletion.' }
    if (-not [bool]$receipt.production_invariant_preserved -or -not [bool]$receipt.env_unchanged) { throw 'Functional provenance production/env invariant is not accepted.' }
    if ([int64]$receipt.e_backup.file_count -ne 4 -or [int64]$receipt.e_backup.vhdx_count -ne 3) { throw 'Accepted E backup multiplicity changed.' }
    if ([int64]$receipt.e_backup.logical_bytes -ne 853980217998 -or [int64]$receipt.e_backup.allocated_bytes -ne 853980217998) { throw 'Accepted E backup byte boundary changed.' }
    if ([string]$receipt.f_recovery.path -ne 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx' -or [int64]$receipt.f_recovery.bytes -ne 961542094848) { throw 'Accepted F recovery boundary changed.' }
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); receipt=$receipt }
}

function Get-FrozenManifest([object]$Receipt) {
    $entries = @()
    foreach ($item in @($Receipt.e_backup.vhdx)) {
        $entries += [pscustomobject]@{
            relative_path=[string]$item.relative_path
            length=[int64]$item.length
            allocated_bytes=[int64]$item.allocated_bytes
            is_vhdx=$true
            role=[string]$item.role
            sha256=$null
        }
    }
    foreach ($item in @($Receipt.e_backup.non_vhdx)) {
        $relative = Get-RelativePathSafe ([string]$Receipt.e_backup.root) ([string]$item.path)
        $entries += [pscustomobject]@{
            relative_path=$relative
            length=[int64]$item.length
            allocated_bytes=[int64]$item.length
            is_vhdx=$false
            role='settings_provenance'
            sha256=[string]$item.sha256
        }
    }
    $entries = @($entries | Sort-Object { [array]::IndexOf($script:ExpectedRelativePaths, [string]$_.relative_path) })
    if ($entries.Count -ne 4) { throw 'Frozen E reclaim manifest must contain exactly four files.' }
    for ($i = 0; $i -lt $script:ExpectedRelativePaths.Count; $i++) {
        if (-not ([string]$entries[$i].relative_path).Equals($script:ExpectedRelativePaths[$i],[System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Frozen manifest relative-path boundary changed at index $i."
        }
    }
    $bytes = [int64]0
    foreach ($entry in $entries) { $bytes += [int64]$entry.length }
    if ($bytes -ne 853980217998) { throw 'Frozen E reclaim manifest byte total changed.' }
    return @($entries)
}

function Assert-PathFileExact([string]$Root, [object]$Entry) {
    $path = Join-Path $Root ([string]$Entry.relative_path)
    $full = [System.IO.Path]::GetFullPath($path)
    if (-not (Test-PathContains $Root $full)) { throw "Manifest path escaped E backup root: $($Entry.relative_path)" }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "Pending manifest file missing: $($Entry.relative_path)" }
    $attributes = [System.IO.File]::GetAttributes($full)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Manifest file became a reparse point: $($Entry.relative_path)" }
    $info = New-Object System.IO.FileInfo($full)
    if ([int64]$info.Length -ne [int64]$Entry.length) { throw "Manifest file length changed: $($Entry.relative_path)" }
    $allocated = Get-AllocatedFileBytes $full
    if ([int64]$allocated -ne [int64]$Entry.allocated_bytes) { throw "Manifest file allocated bytes changed: $($Entry.relative_path)" }
    if (-not [bool]$Entry.is_vhdx -and [string]$Entry.sha256) {
        $sha = Get-Sha256 $full
        if (-not $sha.Equals(([string]$Entry.sha256).ToLowerInvariant(),[System.StringComparison]::OrdinalIgnoreCase)) { throw "Small provenance file SHA changed: $($Entry.relative_path)" }
    }
    return $full
}

function Assert-CurrentDRuntimeCounterparts([object[]]$Manifest) {
    $root = Normalize-HostPath $CurrentDockerDesktopWslRoot
    if (-not $root.Equals('D:\DockerData\DockerDesktopWSL',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'Current Docker Desktop WSL root changed.' }
    foreach ($entry in @($Manifest | Where-Object { [bool]$_.is_vhdx })) {
        $path = Join-Path $root ([string]$entry.relative_path)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Current D runtime counterpart missing: $($entry.relative_path)" }
        $attributes = [System.IO.File]::GetAttributes($path)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Current D runtime counterpart became a reparse point: $($entry.relative_path)" }
        $info = New-Object System.IO.FileInfo($path)
        if ([int64]$info.Length -lt [int64]$entry.length) { throw "Current D runtime counterpart is smaller than retained snapshot: $($entry.relative_path)" }
        Write-Host "d_runtime_counterpart relative_path=$($entry.relative_path) bytes=$([int64]$info.Length)"
    }
}

function Assert-FRecoveryPreserved([object]$AcceptedReceipt) {
    $path = Normalize-HostPath $ExpectedFRecoveryVhdx
    if (-not $path.Equals('F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'Expected F recovery path changed.' }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Expected F recovery VHDX disappeared.' }
    $attributes = [System.IO.File]::GetAttributes($path)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Expected F recovery VHDX became a reparse point.' }
    $info = New-Object System.IO.FileInfo($path)
    if ([int64]$info.Length -ne [int64]$AcceptedReceipt.f_recovery.bytes) { throw 'Expected F recovery VHDX length changed.' }
    $attachment = Get-DiskImageEvidence $path
    if (-not [bool]$attachment.proof_available -or [bool]$attachment.attached) { throw 'Expected F recovery VHDX detached proof is unavailable or attached.' }
    Write-Host "f_recovery_preserved=True"
    Write-Host "f_recovery_bytes=$([int64]$info.Length)"
}

function Assert-EVhdxDetached([string]$Root, [object[]]$Manifest) {
    foreach ($entry in @($Manifest | Where-Object { [bool]$_.is_vhdx })) {
        $path = Join-Path $Root ([string]$entry.relative_path)
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $attachment = Get-DiskImageEvidence $path
            if (-not [bool]$attachment.proof_available -or [bool]$attachment.attached) { throw "E VHDX detached proof unavailable or attached: $($entry.relative_path)" }
            Write-Host "e_vhdx_detached relative_path=$($entry.relative_path) attached=False"
        }
    }
}

function Assert-ProductionBoundary([string]$Label) {
    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_${Label}=$([bool]$production.ready)"
    if (-not [bool]$production.ready) { throw "Production ClickHouse must be healthy during $Label." }
    Assert-AcceptedProductionMount $production.container_id
    $refs = @(Get-CurrentBackupReferences $EBackupRoot)
    Write-Host "e_backup_reference_count_${Label}=$($refs.Count)"
    if ($refs.Count -ne 0) { throw "E backup gained a runtime reference during $Label." }
    return $production
}

function Assert-FullInventoryMatches([string]$Root, [object]$AcceptedReceipt, [object[]]$Manifest) {
    $inventory = Get-DirectoryInventoryNoFollowAllocated $Root
    if (-not [bool]$inventory.exists) { throw 'E backup root disappeared.' }
    if ([int64]$inventory.file_count -ne 4 -or [int64]$inventory.vhdx_count -ne 3 -or [int64]$inventory.reparse_count -ne 0) { throw 'E backup file/reparse multiplicity changed.' }
    if ([int64]$inventory.logical_bytes -ne 853980217998 -or [int64]$inventory.allocated_bytes -ne 853980217998) { throw 'E backup byte inventory changed.' }
    if ([string]$inventory.metadata_manifest_sha256 -ne [string]$AcceptedReceipt.e_backup.metadata_manifest_sha256) { throw 'E backup metadata manifest SHA changed.' }
    $actualPaths = @($inventory.files | ForEach-Object { [string]$_.relative_path } | Sort-Object)
    $expectedPaths = @($Manifest | ForEach-Object { [string]$_.relative_path } | Sort-Object)
    if (($actualPaths -join "`n") -ne ($expectedPaths -join "`n")) { throw 'E backup manifest path set changed.' }
    foreach ($entry in $Manifest) { $null = Assert-PathFileExact $Root $entry }
    Write-Host 'e_backup_full_inventory_matches=True'
    return $inventory
}

function New-BoundaryReceipt([object]$Accepted, [object[]]$Manifest, [string]$EnvSha, [int64]$EFreeBytes) {
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $dir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_e_backup_guarded_reclaim_boundary_$timestamp")
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:BoundaryReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_E_BACKUP_GUARDED_RECLAIM_BOUNDARY_READY_FOR_APPLY'
        next_gate='PRODUCTION_E_BACKUP_GUARDED_RECLAIM_APPLY'
        read_only=$true
        mutation_performed=$false
        accepted_provenance_receipt_path=$Accepted.path
        accepted_provenance_receipt_sha256=$Accepted.sha256
        e_backup_root=(Normalize-HostPath $EBackupRoot)
        manifest=@($Manifest)
        manifest_file_count=[int64]4
        manifest_bytes=[int64]853980217998
        env_sha256=$EnvSha
        e_free_before_bytes=$EFreeBytes
        e_backup_delete_authorized=$false
    }
    $path = Join-Path $dir 'production_e_backup_guarded_reclaim_boundary.json'
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $path -Encoding UTF8
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); receipt=$receipt }
}

function Resolve-BoundaryReceipt([object]$Accepted, [object[]]$Manifest) {
    if (-not $AcceptedBoundaryReceiptPath) { throw 'Apply requires -AcceptedBoundaryReceiptPath.' }
    $path = [System.IO.Path]::GetFullPath($AcceptedBoundaryReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted E guarded reclaim boundary receipt'
    if ([string]$receipt.receipt_version -ne $script:BoundaryReceiptVersion) { throw 'Unexpected guarded reclaim boundary receipt version.' }
    if ([string]$receipt.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Boundary receipt engine SHA differs from exact main.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_BOUNDARY_READY_FOR_APPLY') { throw 'Boundary receipt is not READY_FOR_APPLY.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_APPLY') { throw 'Boundary receipt next gate changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed -or [bool]$receipt.e_backup_delete_authorized) { throw 'Boundary receipt lost read-only/no-authority contract.' }
    if ([string]$receipt.accepted_provenance_receipt_sha256 -ne $Accepted.sha256) { throw 'Boundary receipt provenance receipt SHA changed.' }
    if ([int64]$receipt.manifest_file_count -ne 4 -or [int64]$receipt.manifest_bytes -ne 853980217998) { throw 'Boundary receipt manifest dimensions changed.' }
    $receiptPaths = @($receipt.manifest | ForEach-Object { [string]$_.relative_path })
    $expectedPaths = @($Manifest | ForEach-Object { [string]$_.relative_path })
    if (($receiptPaths -join "`n") -ne ($expectedPaths -join "`n")) { throw 'Boundary receipt manifest paths changed.' }
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); receipt=$receipt }
}

function Save-JournalAtomic([string]$Path, [object]$Journal) {
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { [System.IO.Directory]::CreateDirectory($directory) | Out-Null }
    $json = $Journal | ConvertTo-Json -Depth 14
    $tmp = "$Path.tmp"
    $backup = "$Path.bak"
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        if (Test-Path -LiteralPath $backup -PathType Leaf) { [System.IO.File]::Delete($backup) }
        [System.IO.File]::Replace($tmp, $Path, $backup, $true)
    }
    else {
        [System.IO.File]::Move($tmp, $Path)
    }
}

function New-ApplyJournal([object]$Accepted, [object]$Boundary, [object[]]$Manifest, [string]$EnvSha, [int64]$EFreeBytes, [string]$EvidenceDir) {
    $path = Join-Path $EvidenceDir 'production_e_backup_guarded_reclaim_journal.json'
    $journal = [ordered]@{
        journal_version=$script:JournalVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        accepted_provenance_receipt_path=$Accepted.path
        accepted_provenance_receipt_sha256=$Accepted.sha256
        accepted_boundary_receipt_path=$Boundary.path
        accepted_boundary_receipt_sha256=$Boundary.sha256
        e_backup_root=(Normalize-HostPath $EBackupRoot)
        manifest=@($Manifest)
        state='MUTATING'
        mutation_started=$true
        completed_relative_paths=@()
        deleted_file_count=[int64]0
        deleted_bytes=[int64]0
        inflight_relative_path=$null
        failure_relative_path=$null
        failure_message=$null
        env_sha256=$EnvSha
        e_free_before_bytes=$EFreeBytes
        started_utc=(Get-Date).ToUniversalTime().ToString('o')
        updated_utc=(Get-Date).ToUniversalTime().ToString('o')
    }
    Save-JournalAtomic $path $journal
    return [ordered]@{ path=$path; journal=$journal }
}

function Resolve-ResumeJournal([object]$Accepted, [object]$Boundary, [object[]]$Manifest) {
    if (-not $ResumeJournalPath) { throw 'Resume path missing.' }
    $path = [System.IO.Path]::GetFullPath($ResumeJournalPath)
    $journal = Read-JsonFile $path 'E guarded reclaim resume journal'
    if ([string]$journal.journal_version -ne $script:JournalVersion) { throw 'Resume journal version changed.' }
    if ([string]$journal.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Resume journal engine SHA differs from exact main.' }
    if ([string]$journal.state -ne 'PARTIAL_FAILURE' -or -not [bool]$journal.mutation_started) { throw 'Resume requires PARTIAL_FAILURE journal with mutation_started=true.' }
    if ([string]$journal.accepted_provenance_receipt_sha256 -ne $Accepted.sha256 -or [string]$journal.accepted_boundary_receipt_sha256 -ne $Boundary.sha256) { throw 'Resume journal receipt provenance changed.' }
    $journalPaths = @($journal.manifest | ForEach-Object { [string]$_.relative_path })
    $expectedPaths = @($Manifest | ForEach-Object { [string]$_.relative_path })
    if (($journalPaths -join "`n") -ne ($expectedPaths -join "`n")) { throw 'Resume journal manifest changed.' }
    return [ordered]@{ path=$path; journal=$journal }
}

function Reconcile-ResumeState([string]$Root, [object]$Journal) {
    $completed = @($Journal.completed_relative_paths | ForEach-Object { [string]$_ })
    foreach ($relative in $completed) {
        if (Test-Path -LiteralPath (Join-Path $Root $relative)) { throw "Completed E reclaim file reappeared: $relative" }
    }
    $inflight = [string]$Journal.inflight_relative_path
    if ($inflight) {
        $entry = @($Journal.manifest | Where-Object { ([string]$_.relative_path).Equals($inflight,[System.StringComparison]::OrdinalIgnoreCase) })
        if ($entry.Count -ne 1) { throw 'Resume inflight path is not in frozen manifest.' }
        $path = Join-Path $Root $inflight
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            if ($completed -notcontains $inflight) {
                $Journal.completed_relative_paths = @($completed + $inflight)
                $Journal.deleted_file_count = [int64]$Journal.deleted_file_count + 1
                $Journal.deleted_bytes = [int64]$Journal.deleted_bytes + [int64]$entry[0].length
            }
            $Journal.inflight_relative_path = $null
            $Journal.updated_utc = (Get-Date).ToUniversalTime().ToString('o')
            Save-JournalAtomic $script:ActiveJournalPath $Journal
            Write-Host "resume_recovered_absent_inflight=$inflight"
        }
        else {
            $null = Assert-PathFileExact $Root $entry[0]
            Write-Host "resume_retry_inflight=$inflight"
        }
    }
    $completed = @($Journal.completed_relative_paths | ForEach-Object { [string]$_ })
    foreach ($entry in @($Journal.manifest)) {
        $relative = [string]$entry.relative_path
        if ($completed -contains $relative) { continue }
        if ([string]$Journal.inflight_relative_path -and $relative.Equals([string]$Journal.inflight_relative_path,[System.StringComparison]::OrdinalIgnoreCase)) { continue }
        $null = Assert-PathFileExact $Root $entry
    }
}

function Delete-ExactManifestFile([string]$Root, [object]$Entry, [object]$Journal) {
    $relative = [string]$Entry.relative_path
    $completed = @($Journal.completed_relative_paths | ForEach-Object { [string]$_ })
    if ($completed -contains $relative) { return }
    $path = Assert-PathFileExact $Root $Entry
    $Journal.inflight_relative_path = $relative
    $Journal.updated_utc = (Get-Date).ToUniversalTime().ToString('o')
    Save-JournalAtomic $script:ActiveJournalPath $Journal
    Write-Host "e_backup_delete_inflight=$relative"
    [System.IO.File]::Delete($path)
    if (Test-Path -LiteralPath $path) { throw "Exact E backup file still exists after delete: $relative" }
    $Journal.completed_relative_paths = @($completed + $relative)
    $Journal.deleted_file_count = [int64]$Journal.deleted_file_count + 1
    $Journal.deleted_bytes = [int64]$Journal.deleted_bytes + [int64]$Entry.length
    $Journal.inflight_relative_path = $null
    $Journal.updated_utc = (Get-Date).ToUniversalTime().ToString('o')
    Save-JournalAtomic $script:ActiveJournalPath $Journal
    Write-Host "e_backup_delete_completed=$relative"
}

function Remove-OnlyEmptySnapshotDirectories([string]$Root) {
    $dirs = @(
        (Join-Path $Root 'disk'),
        (Join-Path $Root 'main')
    )
    foreach ($dir in $dirs) {
        if (Test-Path -LiteralPath $dir -PathType Container) {
            $entries = @([System.IO.Directory]::GetFileSystemEntries($dir))
            if ($entries.Count -ne 0) { throw "Snapshot subdirectory is not empty after manifest delete: $dir" }
            [System.IO.Directory]::Delete($dir, $false)
        }
    }
    if (Test-Path -LiteralPath $Root -PathType Container) {
        $rootEntries = @([System.IO.Directory]::GetFileSystemEntries($Root))
        if ($rootEntries.Count -ne 0) { throw 'E backup root is not empty after exact manifest/subdirectory cleanup.' }
        [System.IO.Directory]::Delete($Root, $false)
    }
}

function Invoke-ContractFixture {
    foreach ($name in @(
        'Assert-ExactMain','Normalize-HostPath','Get-OptionalArrayProperty','Get-BackupReferenceInventory',
        'Get-DirectoryInventoryNoFollowAllocated','Get-AllocatedFileBytes','Get-DiskImageEvidence','Get-CurrentBackupReferences'
    )) {
        if ($null -eq (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Required helper missing: $name" }
    }
    if ($script:AllowedToolingFiles.Count -ne 3) { throw 'Guarded reclaim tooling boundary count changed.' }
    if ($script:ExpectedRelativePaths.Count -ne 4) { throw 'Guarded reclaim manifest path count changed.' }
    $fixtureJournal = [ordered]@{ state='MUTATING'; completed_relative_paths=@(); deleted_file_count=[int64]0; deleted_bytes=[int64]0; inflight_relative_path=$null }
    if ([string]$fixtureJournal.state -ne 'MUTATING') { throw 'Journal fixture failed.' }
    Write-Host 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION E BACKUP GUARDED RECLAIM ====='
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "resume_requested=$([bool]$ResumeJournalPath)"
    Write-Host 'recursive_backup_root_delete_authorized=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'accepted_volume_mutation_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'docker_prune_authorized=False'
    Write-Host 'wsl_mutation_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Guarded E reclaim must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-ToolingProvenance

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Guarded E reclaim requires elevated Administrator PowerShell.' }

    $root = Normalize-HostPath $EBackupRoot
    if (-not $root.Equals('E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'EBackupRoot changed.' }
    $accepted = Resolve-AcceptedProvenanceReceipt
    $manifest = Get-FrozenManifest $accepted.receipt
    Write-Host "accepted_provenance_receipt=$($accepted.path)"
    Write-Host "accepted_provenance_receipt_sha256=$($accepted.sha256)"
    Write-Host "frozen_manifest_file_count=$($manifest.Count)"
    Write-Host 'frozen_manifest_bytes=853980217998'

    $envPath = Join-Path $repoRoot '.env'
    $envShaBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { Get-Sha256 $envPath } else { $null }
    Assert-ExactMain 'boundary_before'
    $null = Assert-ProductionBoundary 'boundary_before'
    Assert-CurrentDRuntimeCounterparts $manifest
    Assert-FRecoveryPreserved $accepted.receipt
    Assert-EVhdxDetached $root $manifest

    if (-not $Apply) {
        if ($ResumeJournalPath -or $AcknowledgeSupersededEBackupDelete -or $AcknowledgeResumePartialReclaim -or $AcceptedBoundaryReceiptPath) { throw 'Read-only boundary mode must not include Apply/resume/delete acknowledgement parameters.' }
        $inventory = Assert-FullInventoryMatches $root $accepted.receipt $manifest
        $drive = New-Object System.IO.DriveInfo('E')
        $eFree = [int64]$drive.AvailableFreeSpace
        Assert-ExactMain 'boundary_final'
        $null = Assert-ProductionBoundary 'boundary_final'
        $envShaAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { Get-Sha256 $envPath } else { $null }
        if ($envShaAfter -ne $envShaBefore) { throw '.env changed during guarded reclaim boundary.' }
        $boundary = New-BoundaryReceipt $accepted $manifest $envShaBefore $eFree
        Assert-ExactMain 'exit'
        Write-Host '===== PRODUCTION E BACKUP GUARDED RECLAIM BOUNDARY RESULT ====='
        Write-Host 'decision=PRODUCTION_E_BACKUP_GUARDED_RECLAIM_BOUNDARY_READY_FOR_APPLY'
        Write-Host 'next_gate=PRODUCTION_E_BACKUP_GUARDED_RECLAIM_APPLY'
        Write-Host 'read_only=True'
        Write-Host 'mutation_performed=False'
        Write-Host 'e_backup_delete_authorized=False'
        Write-Host "boundary_receipt_path=$($boundary.path)"
        Write-Host "boundary_receipt_sha256=$($boundary.sha256)"
        Write-Host 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_BOUNDARY_DONE'
        return
    }

    if (-not $AcknowledgeSupersededEBackupDelete) { throw 'Apply requires -AcknowledgeSupersededEBackupDelete.' }
    if ($ResumeJournalPath -and -not $AcknowledgeResumePartialReclaim) { throw 'Resume requires -AcknowledgeResumePartialReclaim.' }
    if (-not $ResumeJournalPath -and $AcknowledgeResumePartialReclaim) { throw 'Resume acknowledgement supplied without -ResumeJournalPath.' }

    $boundary = Resolve-BoundaryReceipt $accepted $manifest
    if ([string]$boundary.receipt.env_sha256 -ne $envShaBefore) { throw '.env differs from accepted boundary receipt.' }
    $driveBefore = New-Object System.IO.DriveInfo('E')
    $eFreeBefore = [int64]$driveBefore.AvailableFreeSpace
    Assert-ExactMain 'apply_before_mutation'
    $null = Assert-ProductionBoundary 'apply_before_mutation'
    Assert-CurrentDRuntimeCounterparts $manifest
    Assert-FRecoveryPreserved $accepted.receipt
    Assert-EVhdxDetached $root $manifest

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_e_backup_guarded_reclaim_apply_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    $journalRecord = $null
    if ($ResumeJournalPath) {
        $journalRecord = Resolve-ResumeJournal $accepted $boundary $manifest
        $script:ActiveJournalPath = $journalRecord.path
        Reconcile-ResumeState $root $journalRecord.journal
        $journalRecord.journal.state = 'MUTATING'
        $journalRecord.journal.failure_relative_path = $null
        $journalRecord.journal.failure_message = $null
        $journalRecord.journal.updated_utc = (Get-Date).ToUniversalTime().ToString('o')
        Save-JournalAtomic $script:ActiveJournalPath $journalRecord.journal
    }
    else {
        $null = Assert-FullInventoryMatches $root $accepted.receipt $manifest
        $journalRecord = New-ApplyJournal $accepted $boundary $manifest $envShaBefore $eFreeBefore $evidenceDir
        $script:ActiveJournalPath = $journalRecord.path
    }

    try {
        foreach ($entry in @($journalRecord.journal.manifest)) {
            Delete-ExactManifestFile $root $entry $journalRecord.journal
        }

        foreach ($entry in @($journalRecord.journal.manifest)) {
            if (Test-Path -LiteralPath (Join-Path $root ([string]$entry.relative_path))) { throw "Final manifest file still exists: $($entry.relative_path)" }
        }
        Remove-OnlyEmptySnapshotDirectories $root
        if (Test-Path -LiteralPath $root) { throw 'E backup root still exists after empty-only cleanup.' }

        Assert-ExactMain 'apply_final'
        $null = Assert-ProductionBoundary 'apply_final'
        Assert-CurrentDRuntimeCounterparts $manifest
        Assert-FRecoveryPreserved $accepted.receipt
        $envShaAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { Get-Sha256 $envPath } else { $null }
        if ($envShaAfter -ne $envShaBefore) { throw '.env changed during guarded reclaim apply.' }

        $driveAfter = New-Object System.IO.DriveInfo('E')
        $eFreeAfter = [int64]$driveAfter.AvailableFreeSpace
        $gain = [int64]($eFreeAfter - $eFreeBefore)
        $journalRecord.journal.state = 'GO'
        $journalRecord.journal.inflight_relative_path = $null
        $journalRecord.journal.updated_utc = (Get-Date).ToUniversalTime().ToString('o')
        $journalRecord.journal.completed_utc = (Get-Date).ToUniversalTime().ToString('o')
        $journalRecord.journal.e_free_after_bytes = $eFreeAfter
        $journalRecord.journal.e_free_gain_bytes = $gain
        Save-JournalAtomic $script:ActiveJournalPath $journalRecord.journal

        $receipt = [ordered]@{
            receipt_version=$script:ApplyReceiptVersion
            engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
            decision='PRODUCTION_E_BACKUP_GUARDED_RECLAIM_GO'
            next_gate='PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT'
            data_mutation_performed=$true
            accepted_provenance_receipt_path=$accepted.path
            accepted_provenance_receipt_sha256=$accepted.sha256
            accepted_boundary_receipt_path=$boundary.path
            accepted_boundary_receipt_sha256=$boundary.sha256
            journal_path=$script:ActiveJournalPath
            journal_sha256=(Get-Sha256 $script:ActiveJournalPath)
            deleted_file_count=[int64]$journalRecord.journal.deleted_file_count
            deleted_bytes=[int64]$journalRecord.journal.deleted_bytes
            e_backup_root_removed=$true
            e_free_before_bytes=$eFreeBefore
            e_free_after_bytes=$eFreeAfter
            e_free_gain_bytes=$gain
            production_invariant_preserved=$true
            env_unchanged=$true
            constraints=[ordered]@{
                raw_delete_authorized=$false
                accepted_volume_mutation_authorized=$false
                docker_restart_authorized=$false
                docker_prune_authorized=$false
                wsl_mutation_authorized=$false
                vhdx_mutation_authorized=$false
                clickhouse_mutation_authorized=$false
                cn_warm_move_authorized=$false
                us_bulk_authorized=$false
            }
        }
        $receiptPath = Join-Path $evidenceDir 'production_e_backup_guarded_reclaim_apply.json'
        $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
        Assert-ExactMain 'exit'
        Write-Host '===== PRODUCTION E BACKUP GUARDED RECLAIM APPLY RESULT ====='
        Write-Host 'decision=PRODUCTION_E_BACKUP_GUARDED_RECLAIM_GO'
        Write-Host 'next_gate=PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT'
        Write-Host 'data_mutation_performed=True'
        Write-Host "deleted_file_count=$([int64]$journalRecord.journal.deleted_file_count)"
        Write-Host "deleted_bytes=$([int64]$journalRecord.journal.deleted_bytes)"
        Write-Host "e_free_before_bytes=$eFreeBefore"
        Write-Host "e_free_after_bytes=$eFreeAfter"
        Write-Host "e_free_gain_bytes=$gain"
        Write-Host 'e_backup_root_removed=True'
        Write-Host 'cn_warm_move_authorized=False'
        Write-Host 'vhdx_mutation_authorized=False'
        Write-Host "journal_path=$($script:ActiveJournalPath)"
        Write-Host "receipt_path=$receiptPath"
        Write-Host "Evidence directory: $evidenceDir"
        Write-Host 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_DONE'
    }
    catch {
        if ($journalRecord -and $journalRecord.journal -and [bool]$journalRecord.journal.mutation_started) {
            $journalRecord.journal.state = 'PARTIAL_FAILURE'
            $journalRecord.journal.failure_relative_path = [string]$journalRecord.journal.inflight_relative_path
            $journalRecord.journal.failure_message = $_.Exception.Message
            $journalRecord.journal.updated_utc = (Get-Date).ToUniversalTime().ToString('o')
            try { Save-JournalAtomic $script:ActiveJournalPath $journalRecord.journal } catch { }
            Write-Host 'decision=PRODUCTION_E_BACKUP_GUARDED_RECLAIM_PARTIAL_FAILURE'
            Write-Host "resume_journal_path=$($script:ActiveJournalPath)"
        }
        throw
    }
}
finally {
    Pop-Location
}
