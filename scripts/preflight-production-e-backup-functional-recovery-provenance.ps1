[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedEBackupPreflightReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',
    [string]$CurrentDockerDesktopWslRoot = 'D:\DockerData\DockerDesktopWSL',
    [string]$FRecoveryRoot = 'F:\MarkOrbitData\recovery',
    [string]$ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedPreflightEngineSha = '5c516d461149f9b46c22cbe4ab654670800f6f84'
$script:AcceptedPreflightReceiptVersion = 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_V1'
$script:ReceiptVersion = 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_V1'
$script:AllowedToolingFiles = @(
    'scripts/preflight-production-e-backup-functional-recovery-provenance.ps1',
    'tests/test_production_e_backup_functional_recovery_provenance_contract.py',
    '.github/workflows/production-e-backup-functional-recovery-provenance-runtime.yml'
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
            'Invoke-NativeText','Assert-ExactMain','Normalize-HostPath','Test-PathContains',
            'Get-OptionalPropertyValue','Get-OptionalArrayProperty','Get-DotEnvValues',
            'Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped',
            'Get-AllContainerMounts','Get-ComposeBindMounts'
        ) `
        'Phase2D'

    Import-FunctionDefinitions `
        (Join-Path $PSScriptRoot 'preflight-production-storage-reserve-exception-review.ps1') `
        @('Get-Sha256','Read-JsonFile','Get-BackupReferenceInventory') `
        'layout-replan'

    Import-FunctionDefinitions `
        (Join-Path $PSScriptRoot 'preflight-production-e-backup-reclaim.ps1') `
        @('Initialize-AllocatedSizeNative','Get-AllocatedFileBytes','Get-RelativePathSafe','Get-DirectoryInventoryNoFollowAllocated') `
        'E-backup-preflight'
}

function Get-ProcessBackupReferences([string]$BackupRoot) {
    $needle = Normalize-HostPath $BackupRoot
    $refs = @()
    foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
        $commandLine = [string]$process.CommandLine
        if ($commandLine -and $commandLine.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $refs += [pscustomobject]@{
                source='process_command_line'
                identity="$([string]$process.Name):$([int]$process.ProcessId)"
                path=$needle
            }
        }
    }
    return @($refs)
}

function Get-CurrentBackupReferences([string]$BackupRoot) {
    $compose = @(Get-ComposeBindMounts)
    $containers = @(Get-AllContainerMounts)
    $envPath = Join-Path $repoRoot '.env'
    $envLines = if (Test-Path -LiteralPath $envPath -PathType Leaf) { @(Get-Content -LiteralPath $envPath -Encoding UTF8) } else { @() }
    $envValues = Get-DotEnvValues -Lines $envLines
    $refs = @(Get-BackupReferenceInventory $BackupRoot $containers $compose $envValues)
    $refs += @(Get-ProcessBackupReferences $BackupRoot)
    return @($refs)
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
    return [ordered]@{ production=$production; references=$refs }
}

function Assert-ToolingProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedPreflightEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted E backup preflight SHA is not an ancestor of current exact main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedPreflightEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedToolingFiles })
    $missing = @($script:AllowedToolingFiles | Where-Object { $_ -notin $changed })
    Write-Host "accepted_preflight_to_current_changed_file_count=$($changed.Count)"
    Write-Host "accepted_preflight_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "accepted_preflight_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'Functional recovery provenance tooling changed outside the exact 3-file boundary.'
    }
}

function Resolve-AcceptedPreflightReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedEBackupPreflightReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted E backup reclaim preflight receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedPreflightReceiptVersion) { throw 'Unexpected E backup preflight receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedPreflightEngineSha) { throw 'Accepted E backup preflight engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_BLOCKED') { throw 'Accepted E backup preflight decision changed.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_REVIEW') { throw 'Accepted E backup preflight next gate changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Accepted E backup preflight lost read-only contract.' }
    if ([bool]$receipt.e_backup.delete_authorized) { throw 'Accepted preflight unexpectedly authorized E deletion.' }
    if ([bool]$receipt.duplicate_identity.proven) { throw 'Accepted preflight unexpectedly proved byte identity.' }
    if ([int64]$receipt.e_backup.vhdx_count -ne 3 -or [int64]$receipt.e_backup.file_count -ne 4) { throw 'Accepted E backup multiplicity changed.' }
    if (-not [bool]$receipt.capacity.projected_recommended_fit -or -not [bool]$receipt.capacity.projected_hard_fit) { throw 'Accepted E reclaim no longer satisfies projected capacity.' }
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); receipt=$receipt }
}

function Get-BackupRole([string]$RelativePath) {
    $normalized = $RelativePath.Replace('/','\').TrimStart('\').ToLowerInvariant()
    if ($normalized -eq 'disk\docker_data.vhdx') { return 'docker_data_primary_snapshot' }
    if ($normalized -eq 'disk\docker_data.empty.vhdx') { return 'docker_data_empty_placeholder_snapshot' }
    if ($normalized -eq 'main\ext4.vhdx') { return 'docker_desktop_system_distro_snapshot' }
    return 'unresolved'
}

function Get-DiskImageEvidence([string]$Path) {
    $command = Get-Command Get-DiskImage -ErrorAction SilentlyContinue
    if ($null -eq $command) { return [ordered]@{ proof_available=$false; attached=$null; reason='Get-DiskImage unavailable' } }
    try {
        $image = Get-DiskImage -ImagePath $Path -ErrorAction Stop
        return [ordered]@{ proof_available=$true; attached=[bool]$image.Attached; reason=$null }
    }
    catch { return [ordered]@{ proof_available=$false; attached=$null; reason=$_.Exception.Message } }
}

function Get-VhdMetadataReadOnly([string]$Path) {
    $result = [ordered]@{
        path=$Path
        get_vhd_available=$false
        metadata_ready=$false
        vhd_format=$null
        vhd_type=$null
        file_size=$null
        virtual_size=$null
        minimum_size=$null
        logical_sector_size=$null
        physical_sector_size=$null
        block_size=$null
        parent_path=$null
        disk_identifier=$null
        fragmentation_percentage=$null
        attached=$null
        reason=$null
    }
    $command = Get-Command Get-VHD -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $result.reason = 'Get-VHD unavailable'
        return $result
    }
    $result.get_vhd_available = $true
    try {
        $vhd = Get-VHD -Path $Path -ErrorAction Stop
        $result.metadata_ready = $true
        $result.vhd_format = [string](Get-OptionalPropertyValue $vhd 'VhdFormat')
        $result.vhd_type = [string](Get-OptionalPropertyValue $vhd 'VhdType')
        $result.file_size = Get-OptionalPropertyValue $vhd 'FileSize'
        $result.virtual_size = Get-OptionalPropertyValue $vhd 'Size'
        $result.minimum_size = Get-OptionalPropertyValue $vhd 'MinimumSize'
        $result.logical_sector_size = Get-OptionalPropertyValue $vhd 'LogicalSectorSize'
        $result.physical_sector_size = Get-OptionalPropertyValue $vhd 'PhysicalSectorSize'
        $result.block_size = Get-OptionalPropertyValue $vhd 'BlockSize'
        $result.parent_path = [string](Get-OptionalPropertyValue $vhd 'ParentPath')
        $identifier = Get-OptionalPropertyValue $vhd 'DiskIdentifier'
        $result.disk_identifier = if ($null -ne $identifier) { [string]$identifier } else { $null }
        $result.fragmentation_percentage = Get-OptionalPropertyValue $vhd 'FragmentationPercentage'
        $result.attached = Get-OptionalPropertyValue $vhd 'Attached'
    }
    catch { $result.reason = $_.Exception.Message }
    return $result
}

function Get-FileEvidence([string]$Path, [string]$RelativePath, [string]$Role, [string]$CurrentRoot) {
    $info = New-Object System.IO.FileInfo($Path)
    $attributes = [System.IO.File]::GetAttributes($Path)
    $reparse = [bool](($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    $allocated = if ($reparse) { [int64]0 } else { Get-AllocatedFileBytes $Path }
    $currentPath = Join-Path $CurrentRoot $RelativePath
    $currentExists = Test-Path -LiteralPath $currentPath -PathType Leaf
    $current = $null
    if ($currentExists) {
        $currentInfo = New-Object System.IO.FileInfo($currentPath)
        $currentAttributes = [System.IO.File]::GetAttributes($currentPath)
        $currentReparse = [bool](($currentAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
        $current = [ordered]@{
            path=$currentInfo.FullName
            length=[int64]$currentInfo.Length
            allocated_bytes=if ($currentReparse) { [int64]0 } else { Get-AllocatedFileBytes $currentInfo.FullName }
            last_write_utc=$currentInfo.LastWriteTimeUtc.ToString('o')
            reparse=$currentReparse
            disk_image=(Get-DiskImageEvidence $currentInfo.FullName)
            vhd=if ($currentInfo.Extension.Equals('.vhdx',[System.StringComparison]::OrdinalIgnoreCase)) { Get-VhdMetadataReadOnly $currentInfo.FullName } else { $null }
        }
    }
    return [ordered]@{
        relative_path=$RelativePath
        path=$info.FullName
        role=$Role
        length=[int64]$info.Length
        allocated_bytes=[int64]$allocated
        creation_utc=$info.CreationTimeUtc.ToString('o')
        last_write_utc=$info.LastWriteTimeUtc.ToString('o')
        reparse=$reparse
        current_counterpart_exists=$currentExists
        current_counterpart=$current
        disk_image=if ($info.Extension.Equals('.vhdx',[System.StringComparison]::OrdinalIgnoreCase)) { Get-DiskImageEvidence $info.FullName } else { $null }
        vhd=if ($info.Extension.Equals('.vhdx',[System.StringComparison]::OrdinalIgnoreCase)) { Get-VhdMetadataReadOnly $info.FullName } else { $null }
    }
}

function Get-SmallFilePreview([string]$Path) {
    $info = New-Object System.IO.FileInfo($Path)
    $preview = $null
    if ($info.Length -le 65536) {
        try {
            $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop
            if ($text -and -not $text.Contains([char]0)) { $preview = $text.Substring(0, [math]::Min(2048, $text.Length)) }
        }
        catch { $preview = $null }
    }
    return [ordered]@{ path=$info.FullName; length=[int64]$info.Length; sha256=(Get-Sha256 $info.FullName); utf8_preview=$preview }
}

function Compare-VhdLineage([object]$Left, [object]$Right) {
    if ($null -eq $Left -or $null -eq $Right) { return [ordered]@{ state='UNRESOLVED'; reason='missing_metadata' } }
    if (-not [bool]$Left.metadata_ready -or -not [bool]$Right.metadata_ready) { return [ordered]@{ state='UNRESOLVED'; reason='Get-VHD metadata unavailable on one or both files' } }
    $leftId = [string]$Left.disk_identifier
    $rightId = [string]$Right.disk_identifier
    if ($leftId -and $rightId -and $leftId.Equals($rightId,[System.StringComparison]::OrdinalIgnoreCase)) {
        return [ordered]@{ state='SAME_VIRTUAL_DISK_IDENTIFIER'; reason='DiskIdentifier equal' }
    }
    $shapeEqual = [bool](
        [string]$Left.vhd_format -eq [string]$Right.vhd_format -and
        [int64]$Left.virtual_size -eq [int64]$Right.virtual_size -and
        [int64]$Left.logical_sector_size -eq [int64]$Right.logical_sector_size -and
        [int64]$Left.physical_sector_size -eq [int64]$Right.physical_sector_size -and
        [int64]$Left.block_size -eq [int64]$Right.block_size
    )
    if ($shapeEqual) { return [ordered]@{ state='COMPATIBLE_VIRTUAL_DISK_SHAPE_ONLY'; reason='format/virtual size/sector/block metadata equal but DiskIdentifier differs or unavailable' } }
    return [ordered]@{ state='DISTINCT_VIRTUAL_DISK_METADATA'; reason='VHD identity/shape differs' }
}

function Invoke-ContractFixture {
    foreach ($name in @(
        'Assert-ExactMain','Normalize-HostPath','Get-OptionalArrayProperty','Get-AllContainerMounts','Get-ComposeBindMounts',
        'Get-BackupReferenceInventory','Get-DirectoryInventoryNoFollowAllocated','Get-AllocatedFileBytes'
    )) {
        if ($null -eq (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Required helper missing: $name" }
    }
    if ($script:AllowedToolingFiles.Count -ne 3) { throw 'Functional provenance tooling boundary count changed.' }
    if ((Get-BackupRole 'disk\docker_data.vhdx') -ne 'docker_data_primary_snapshot') { throw 'docker_data role fixture failed.' }
    if ((Get-BackupRole 'disk\docker_data.empty.vhdx') -ne 'docker_data_empty_placeholder_snapshot') { throw 'empty disk role fixture failed.' }
    if ((Get-BackupRole 'main\ext4.vhdx') -ne 'docker_desktop_system_distro_snapshot') { throw 'system distro role fixture failed.' }
    if ((Get-BackupRole 'other\unknown.vhdx') -ne 'unresolved') { throw 'unresolved role fixture failed.' }
    $left = [ordered]@{ metadata_ready=$true; disk_identifier='abc'; vhd_format='VHDX'; virtual_size=[int64]1024; logical_sector_size=[int64]512; physical_sector_size=[int64]4096; block_size=[int64]1048576 }
    $right = [ordered]@{ metadata_ready=$true; disk_identifier='ABC'; vhd_format='VHDX'; virtual_size=[int64]1024; logical_sector_size=[int64]512; physical_sector_size=[int64]4096; block_size=[int64]1048576 }
    if ((Compare-VhdLineage $left $right).state -ne 'SAME_VIRTUAL_DISK_IDENTIFIER') { throw 'VHD lineage identifier fixture failed.' }
    Write-Host 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION E BACKUP FUNCTIONAL RECOVERY PROVENANCE REVIEW ====='
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'e_backup_delete_authorized=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'accepted_volume_mutation_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'docker_prune_authorized=False'
    Write-Host 'wsl_mutation_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Functional recovery provenance review must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-ToolingProvenance

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Functional recovery provenance review requires elevated Administrator PowerShell.' }

    $eBackup = Normalize-HostPath $EBackupRoot
    $currentRoot = Normalize-HostPath $CurrentDockerDesktopWslRoot
    $fRecovery = Normalize-HostPath $FRecoveryRoot
    $expectedF = Normalize-HostPath $ExpectedFRecoveryVhdx
    if (-not $eBackup.Equals('E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'EBackupRoot changed.' }
    if (-not $currentRoot.Equals('D:\DockerData\DockerDesktopWSL',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'CurrentDockerDesktopWslRoot changed.' }
    if (-not $fRecovery.Equals('F:\MarkOrbitData\recovery',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'FRecoveryRoot changed.' }
    if (-not $expectedF.Equals('F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'ExpectedFRecoveryVhdx changed.' }

    $accepted = Resolve-AcceptedPreflightReceipt
    Write-Host "accepted_e_backup_preflight_receipt=$($accepted.path)"
    Write-Host "accepted_e_backup_preflight_receipt_sha256=$($accepted.sha256)"

    $envPath = Join-Path $repoRoot '.env'
    $envShaBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { Get-Sha256 $envPath } else { $null }

    Assert-ExactMain 'review_before'
    $beforeBoundary = Assert-ProductionBoundary 'before'

    $inventory = Get-DirectoryInventoryNoFollowAllocated $eBackup
    if (-not [bool]$inventory.exists) { throw 'E backup disappeared before provenance review.' }
    if ([int64]$inventory.reparse_count -ne 0) { throw 'E backup gained a reparse point.' }
    if ([int64]$inventory.file_count -ne [int64]$accepted.receipt.e_backup.file_count -or [int64]$inventory.vhdx_count -ne [int64]$accepted.receipt.e_backup.vhdx_count) { throw 'E backup multiplicity changed from accepted preflight.' }
    if ([int64]$inventory.logical_bytes -ne [int64]$accepted.receipt.e_backup.logical_bytes -or [int64]$inventory.allocated_bytes -ne [int64]$accepted.receipt.e_backup.allocated_bytes) { throw 'E backup byte inventory changed from accepted preflight.' }

    $vhdxEvidence = @()
    $unresolvedRoleCount = 0
    $counterpartMissingCount = 0
    $attachedCount = 0
    foreach ($file in @($inventory.vhdx)) {
        $role = Get-BackupRole $file.relative_path
        if ($role -eq 'unresolved') { $unresolvedRoleCount++ }
        $evidence = Get-FileEvidence $file.path $file.relative_path $role $currentRoot
        if (-not [bool]$evidence.current_counterpart_exists) { $counterpartMissingCount++ }
        if ($evidence.disk_image -and [bool]$evidence.disk_image.proof_available -and [bool]$evidence.disk_image.attached) { $attachedCount++ }
        $vhdxEvidence += [pscustomobject]$evidence
        Write-Host "e_vhdx relative_path=$($evidence.relative_path) role=$($evidence.role) bytes=$($evidence.length) allocated_bytes=$($evidence.allocated_bytes) current_counterpart_exists=$($evidence.current_counterpart_exists) attached=$($evidence.disk_image.attached) get_vhd_ready=$($evidence.vhd.metadata_ready) disk_identifier=$($evidence.vhd.disk_identifier) virtual_size=$($evidence.vhd.virtual_size) vhd_type=$($evidence.vhd.vhd_type) parent_path=$($evidence.vhd.parent_path)"
        if ($evidence.current_counterpart) {
            Write-Host "d_counterpart relative_path=$($evidence.relative_path) bytes=$($evidence.current_counterpart.length) allocated_bytes=$($evidence.current_counterpart.allocated_bytes) attached=$($evidence.current_counterpart.disk_image.attached) get_vhd_ready=$($evidence.current_counterpart.vhd.metadata_ready) disk_identifier=$($evidence.current_counterpart.vhd.disk_identifier) virtual_size=$($evidence.current_counterpart.vhd.virtual_size) vhd_type=$($evidence.current_counterpart.vhd.vhd_type)"
        }
    }

    $smallFileEvidence = @()
    foreach ($file in @($inventory.files | Where-Object { -not [bool]$_.is_vhdx })) {
        $small = Get-SmallFilePreview $file.path
        $smallFileEvidence += [pscustomobject]$small
        $previewOneLine = if ($small.utf8_preview) { ([string]$small.utf8_preview).Replace("`r",' ').Replace("`n",' ') } else { '' }
        Write-Host "e_non_vhdx path=$($small.path) bytes=$($small.length) sha256=$($small.sha256) preview=$previewOneLine"
    }

    if (-not (Test-Path -LiteralPath $expectedF -PathType Leaf)) { throw 'Expected retained F recovery VHDX disappeared.' }
    $fInfo = New-Object System.IO.FileInfo($expectedF)
    $fAttributes = [System.IO.File]::GetAttributes($expectedF)
    $fReparse = [bool](($fAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    if ($fReparse) { throw 'Expected retained F recovery VHDX became a reparse point.' }
    $fDiskImage = Get-DiskImageEvidence $expectedF
    $fVhd = Get-VhdMetadataReadOnly $expectedF
    Write-Host "f_recovery_vhdx path=$expectedF bytes=$([int64]$fInfo.Length) allocated_bytes=$(Get-AllocatedFileBytes $expectedF) attached=$($fDiskImage.attached) get_vhd_ready=$($fVhd.metadata_ready) disk_identifier=$($fVhd.disk_identifier) virtual_size=$($fVhd.virtual_size) vhd_type=$($fVhd.vhd_type) parent_path=$($fVhd.parent_path)"

    $eDockerData = @($vhdxEvidence | Where-Object { $_.role -eq 'docker_data_primary_snapshot' })
    $lineage = [ordered]@{ state='UNRESOLVED'; reason='Expected exactly one E docker_data primary snapshot.' }
    if ($eDockerData.Count -eq 1) { $lineage = Compare-VhdLineage $eDockerData[0].vhd $fVhd }
    Write-Host "e_f_docker_data_lineage_state=$($lineage.state)"
    Write-Host "e_f_docker_data_lineage_reason=$($lineage.reason)"

    $knownRoleCount = @($vhdxEvidence | Where-Object { $_.role -ne 'unresolved' }).Count
    $expectedRoleSetReady = [bool](
        @($vhdxEvidence | Where-Object { $_.role -eq 'docker_data_primary_snapshot' }).Count -eq 1 -and
        @($vhdxEvidence | Where-Object { $_.role -eq 'docker_data_empty_placeholder_snapshot' }).Count -eq 1 -and
        @($vhdxEvidence | Where-Object { $_.role -eq 'docker_desktop_system_distro_snapshot' }).Count -eq 1
    )
    $allDetachedProofReady = [bool](
        @($vhdxEvidence | Where-Object { -not [bool]$_.disk_image.proof_available -or [bool]$_.disk_image.attached }).Count -eq 0 -and
        [bool]$fDiskImage.proof_available -and -not [bool]$fDiskImage.attached
    )
    $currentCounterpartsReady = [bool]($counterpartMissingCount -eq 0)
    Write-Host "e_vhdx_expected_role_set_ready=$expectedRoleSetReady"
    Write-Host "e_vhdx_known_role_count=$knownRoleCount"
    Write-Host "e_vhdx_unresolved_role_count=$unresolvedRoleCount"
    Write-Host "e_vhdx_current_counterpart_missing_count=$counterpartMissingCount"
    Write-Host "e_and_f_vhdx_detached_proof_ready=$allDetachedProofReady"

    $inventoryAfter = Get-DirectoryInventoryNoFollowAllocated $eBackup
    $inventoryStable = [bool](
        [string]$inventoryAfter.metadata_manifest_sha256 -eq [string]$inventory.metadata_manifest_sha256 -and
        [int64]$inventoryAfter.file_count -eq [int64]$inventory.file_count -and
        [int64]$inventoryAfter.logical_bytes -eq [int64]$inventory.logical_bytes -and
        [int64]$inventoryAfter.allocated_bytes -eq [int64]$inventory.allocated_bytes
    )
    Write-Host "e_backup_inventory_stable=True" -NoNewline:$false
    if (-not $inventoryStable) { throw 'E backup inventory changed during provenance review.' }

    Assert-ExactMain 'review_final'
    $finalBoundary = Assert-ProductionBoundary 'final'
    $envShaAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { Get-Sha256 $envPath } else { $null }
    $envUnchanged = [bool]($envShaBefore -eq $envShaAfter)
    Write-Host "env_unchanged=$envUnchanged"

    $decision = 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_BLOCKED'
    $nextGate = 'PRODUCTION_E_BACKUP_PRESERVE_AND_REPLAN'
    if ($expectedRoleSetReady -and $currentCounterpartsReady -and $allDetachedProofReady -and $inventoryStable -and $envUnchanged) {
        $decision = 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_READY_FOR_OPERATOR_DECISION'
        $nextGate = 'PRODUCTION_E_BACKUP_RECLAIM_OPERATOR_ACK_REVIEW'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_e_backup_functional_recovery_provenance_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        read_only=$true
        mutation_performed=$false
        accepted_e_backup_preflight_receipt_path=$accepted.path
        accepted_e_backup_preflight_receipt_sha256=$accepted.sha256
        e_backup=[ordered]@{
            root=$eBackup
            file_count=[int64]$inventory.file_count
            vhdx_count=[int64]$inventory.vhdx_count
            logical_bytes=[int64]$inventory.logical_bytes
            allocated_bytes=[int64]$inventory.allocated_bytes
            metadata_manifest_sha256=$inventory.metadata_manifest_sha256
            inventory_stable=$inventoryStable
            expected_role_set_ready=$expectedRoleSetReady
            current_counterparts_ready=$currentCounterpartsReady
            all_detached_proof_ready=$allDetachedProofReady
            vhdx=@($vhdxEvidence)
            non_vhdx=@($smallFileEvidence)
            delete_authorized=$false
        }
        f_recovery=[ordered]@{
            path=$expectedF
            bytes=[int64]$fInfo.Length
            allocated_bytes=[int64](Get-AllocatedFileBytes $expectedF)
            disk_image=$fDiskImage
            vhd=$fVhd
        }
        docker_data_lineage=$lineage
        production_invariant_preserved=[bool]($beforeBoundary.production.ready -and $finalBoundary.production.ready)
        env_unchanged=$envUnchanged
        constraints=[ordered]@{
            e_backup_delete_authorized=$false
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
    $receiptPath = Join-Path $evidenceDir 'production_e_backup_functional_recovery_provenance.json'
    $receipt | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Assert-ExactMain 'exit'
    Write-Host '===== PRODUCTION E BACKUP FUNCTIONAL RECOVERY PROVENANCE RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host "expected_role_set_ready=$expectedRoleSetReady"
    Write-Host "current_counterparts_ready=$currentCounterpartsReady"
    Write-Host "all_detached_proof_ready=$allDetachedProofReady"
    Write-Host "docker_data_lineage_state=$($lineage.state)"
    Write-Host 'e_backup_delete_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_DONE'
}
finally {
    Pop-Location
}
