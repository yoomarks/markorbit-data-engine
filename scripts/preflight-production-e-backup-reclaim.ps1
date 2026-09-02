[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedLayoutReplanReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',
    [string]$FRecoveryRoot = 'F:\MarkOrbitData\recovery',
    [string]$ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedLayoutReplanEngineSha = 'a3b9de462f1be1a7f7627446280c8e0df7f3fbf9'
$script:AcceptedLayoutReplanReceiptVersion = 'PRODUCTION_STORAGE_LAYOUT_REPLAN_V1'
$script:PreflightReceiptVersion = 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_V1'
$script:AllowedToolingFiles = @(
    'scripts/preflight-production-e-backup-reclaim.ps1',
    'tests/test_production_e_backup_reclaim_preflight_contract.py',
    '.github/workflows/production-e-backup-reclaim-preflight-runtime.yml'
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
            'Get-OptionalPropertyValue','Get-OptionalArrayProperty','Get-DotEnvValues','Get-DriveSnapshot','Get-ProductionClickHouseHealth',
            'Assert-AcceptedProductionMount','Assert-RawConsumersStopped','Get-AllContainerMounts','Get-ComposeBindMounts'
        ) `
        'Phase2D'

    Import-FunctionDefinitions `
        (Join-Path $PSScriptRoot 'preflight-production-storage-reserve-exception-review.ps1') `
        @('Get-Sha256','Read-JsonFile','Get-ReserveBytes','Get-NewAllocationBudget','Get-WslBasePaths','Get-BackupReferenceInventory') `
        'layout-replan'
}

function Initialize-AllocatedSizeNative {
    if ($null -ne ('MarkOrbit.NativeFileAllocation' -as [type])) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace MarkOrbit {
    public static class NativeFileAllocation {
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern uint GetCompressedFileSizeW(string lpFileName, out uint lpFileSizeHigh);

        public static long GetAllocatedBytes(string path) {
            uint high;
            uint low = GetCompressedFileSizeW(path, out high);
            int error = Marshal.GetLastWin32Error();
            if (low == UInt32.MaxValue && error != 0) {
                throw new Win32Exception(error, "GetCompressedFileSizeW failed for " + path);
            }
            ulong value = ((ulong)high << 32) | (ulong)low;
            if (value > Int64.MaxValue) throw new OverflowException("Allocated size exceeds Int64.");
            return (long)value;
        }
    }
}
'@
}

function Get-AllocatedFileBytes([string]$Path) {
    Initialize-AllocatedSizeNative
    return [int64][MarkOrbit.NativeFileAllocation]::GetAllocatedBytes($Path)
}

function Get-RelativePathSafe([string]$Root, [string]$Path) {
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    $rootUri = New-Object System.Uri($rootFull)
    $pathUri = New-Object System.Uri($pathFull)
    $relative = [System.Uri]::UnescapeDataString($rootUri.MakeRelativeUri($pathUri).ToString()).Replace('/','\')
    return $relative
}

function Get-DirectoryInventoryNoFollowAllocated([string]$Root) {
    $normalized = Normalize-HostPath $Root
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized -PathType Container)) {
        return [ordered]@{
            exists=$false; root=$normalized; file_count=[int64]0; directory_count=[int64]0;
            logical_bytes=[int64]0; allocated_bytes=[int64]0; non_vhdx_file_count=[int64]0;
            non_vhdx_logical_bytes=[int64]0; vhdx_count=[int64]0; reparse_count=[int64]0;
            metadata_manifest_sha256=$null; files=@(); vhdx=@()
        }
    }
    $rootAttributes = [System.IO.File]::GetAttributes($normalized)
    if (($rootAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Inventory root is a reparse point: $normalized"
    }

    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($normalized)
    $directoryCount = [int64]1
    $reparseCount = [int64]0
    $files = @()
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $attributes = [System.IO.File]::GetAttributes($entry)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                $reparseCount++
                continue
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $directoryCount++
                $stack.Push($entry)
                continue
            }
            $info = New-Object System.IO.FileInfo($entry)
            $allocated = Get-AllocatedFileBytes $info.FullName
            $relative = Get-RelativePathSafe $normalized $info.FullName
            $files += [pscustomobject]@{
                relative_path=$relative
                path=$info.FullName
                length=[int64]$info.Length
                allocated_bytes=[int64]$allocated
                last_write_utc_ticks=[int64]$info.LastWriteTimeUtc.Ticks
                is_vhdx=[bool]$info.Extension.Equals('.vhdx', [System.StringComparison]::OrdinalIgnoreCase)
            }
        }
    }

    $files = @($files | Sort-Object relative_path)
    $logicalBytes = [int64]0
    $allocatedBytes = [int64]0
    $nonVhdxCount = [int64]0
    $nonVhdxBytes = [int64]0
    foreach ($file in $files) {
        $logicalBytes += [int64]$file.length
        $allocatedBytes += [int64]$file.allocated_bytes
        if (-not [bool]$file.is_vhdx) {
            $nonVhdxCount++
            $nonVhdxBytes += [int64]$file.length
        }
    }
    $vhdx = @($files | Where-Object { [bool]$_.is_vhdx })

    $manifestLines = @($files | ForEach-Object {
        "$($_.relative_path)|$($_.length)|$($_.allocated_bytes)|$($_.last_write_utc_ticks)|$($_.is_vhdx)"
    })
    $manifestText = ($manifestLines -join "`n")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($manifestText)
        $hash = $sha.ComputeHash($bytes)
        $manifestSha = ([System.BitConverter]::ToString($hash)).Replace('-','').ToLowerInvariant()
    }
    finally { $sha.Dispose() }

    return [ordered]@{
        exists=$true
        root=$normalized
        file_count=[int64]$files.Count
        directory_count=$directoryCount
        logical_bytes=$logicalBytes
        allocated_bytes=$allocatedBytes
        non_vhdx_file_count=$nonVhdxCount
        non_vhdx_logical_bytes=$nonVhdxBytes
        vhdx_count=[int64]$vhdx.Count
        reparse_count=$reparseCount
        metadata_manifest_sha256=$manifestSha
        files=$files
        vhdx=$vhdx
    }
}

function Get-Sha256WithProgress([string]$Path, [string]$Label) {
    $infoBefore = New-Object System.IO.FileInfo($Path)
    $expectedLength = [int64]$infoBefore.Length
    $expectedWriteTicks = [int64]$infoBefore.LastWriteTimeUtc.Ticks
    $bufferBytes = 16MB
    $progressStep = [int64](64GB)
    $nextProgress = $progressStep
    $buffer = New-Object byte[] $bufferBytes
    $stream = New-Object System.IO.FileStream(
        $infoBefore.FullName,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read,
        $bufferBytes,
        [System.IO.FileOptions]::SequentialScan
    )
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $total = [int64]0
    try {
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $null = $sha.TransformBlock($buffer, 0, $read, $buffer, 0)
            $total += [int64]$read
            if ($total -ge $nextProgress -or $total -eq $expectedLength) {
                Write-Host "${Label}_hash_progress_bytes=$total/$expectedLength"
                while ($nextProgress -le $total) { $nextProgress += $progressStep }
            }
        }
        $empty = New-Object byte[] 0
        $null = $sha.TransformFinalBlock($empty, 0, 0)
        if ($total -ne $expectedLength) { throw "$Label length changed while hashing." }
        $hash = ([System.BitConverter]::ToString($sha.Hash)).Replace('-','').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
        $stream.Dispose()
    }
    $infoAfter = New-Object System.IO.FileInfo($Path)
    if ([int64]$infoAfter.Length -ne $expectedLength -or [int64]$infoAfter.LastWriteTimeUtc.Ticks -ne $expectedWriteTicks) {
        throw "$Label metadata changed during hashing."
    }
    Write-Host "${Label}_sha256=$hash"
    return $hash
}

function Get-ProcessBackupReferences([string]$BackupRoot) {
    $needle = (Normalize-HostPath $BackupRoot)
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

function Get-VhdxAttachmentEvidence([string]$Path) {
    $command = Get-Command Get-DiskImage -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return [ordered]@{ proof_available=$false; attached=$null; reason='Get-DiskImage unavailable' }
    }
    try {
        $image = Get-DiskImage -ImagePath $Path -ErrorAction Stop
        return [ordered]@{ proof_available=$true; attached=[bool]$image.Attached; reason=$null }
    }
    catch {
        return [ordered]@{ proof_available=$false; attached=$null; reason=$_.Exception.Message }
    }
}

function Assert-ToolingProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedLayoutReplanEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted layout-replan SHA is not an ancestor of current exact main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedLayoutReplanEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedToolingFiles })
    $missing = @($script:AllowedToolingFiles | Where-Object { $_ -notin $changed })
    Write-Host "layout_replan_to_current_changed_file_count=$($changed.Count)"
    Write-Host "layout_replan_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "layout_replan_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'E backup reclaim preflight provenance changed outside the exact 3-file tooling boundary.'
    }
}

function Resolve-AcceptedLayoutReplanReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedLayoutReplanReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted storage layout replan receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedLayoutReplanReceiptVersion) { throw 'Unexpected layout replan receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedLayoutReplanEngineSha) { throw 'Layout replan receipt engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_STORAGE_LAYOUT_REPLAN_READY') { throw 'Layout replan receipt is not READY.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT') { throw 'Layout replan next gate changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Layout replan receipt lost read-only contract.' }
    if (-not [bool]$receipt.e_backup.technical_reclaim_candidate) { throw 'Layout replan did not mark E backup as a technical reclaim candidate.' }
    if ([bool]$receipt.e_backup.delete_authorized -or [bool]$receipt.e_backup.duplicate_identity_proven) { throw 'Layout replan unexpectedly claimed E delete/duplicate authority.' }
    if (-not [bool]$receipt.f_recovery.expected_vhdx_ready) { throw 'Layout replan did not retain the expected F recovery VHDX.' }
    if (-not [bool]$receipt.drives.D.recommended_fit -or -not [bool]$receipt.drives.D.hard_fit) { throw 'Accepted D Hot scenario no longer fits the layout receipt.' }
    if (-not [bool]$receipt.drives.E.projected_recommended_fit -or -not [bool]$receipt.drives.E.projected_hard_fit) { throw 'Accepted E projected layout did not fit.' }
    if ([bool]$receipt.constraints.e_backup_delete_authorized -or [bool]$receipt.constraints.cn_warm_move_authorized -or [bool]$receipt.constraints.vhdx_create_authorized) {
        throw 'Layout receipt unexpectedly authorized a mutation.'
    }
    return [ordered]@{ path=$path; sha256=(Get-Sha256 $path); receipt=$receipt }
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
    if ($refs.Count -ne 0) { throw "E backup gained a current runtime reference during $Label." }
    return [ordered]@{ production=$production; references=$refs }
}

function Invoke-ContractFixture {
    foreach ($name in @(
        'Assert-ExactMain','Normalize-HostPath','Get-OptionalPropertyValue','Get-OptionalArrayProperty',
        'Get-ProductionClickHouseHealth','Get-AllContainerMounts','Get-ComposeBindMounts','Get-BackupReferenceInventory',
        'Get-DirectoryInventoryNoFollowAllocated','Get-Sha256WithProgress'
    )) {
        if ($null -eq (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Required helper missing: $name" }
    }
    if ($script:AllowedToolingFiles.Count -ne 3) { throw 'E backup preflight tooling boundary count changed.' }

    $optionalFixture = [pscustomobject]@{
        Mounts=@([pscustomobject]@{ Source='C:\fixture\mount'; Destination='/fixture' })
    }
    $optionalMounts = @(Get-OptionalArrayProperty $optionalFixture 'Mounts')
    if ($optionalMounts.Count -ne 1 -or [string]$optionalMounts[0].Source -ne 'C:\fixture\mount') {
        throw 'Imported optional-array helper fixture failed.'
    }
    if (@(Get-OptionalArrayProperty $optionalFixture 'Missing').Count -ne 0) {
        throw 'Imported optional-array missing-property fixture failed.'
    }

    $fixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('markorbit_e_backup_preflight_' + [Guid]::NewGuid().ToString('N'))
    $fixtureE = Join-Path $fixtureRoot 'e'
    $fixtureF = Join-Path $fixtureRoot 'f'
    [System.IO.Directory]::CreateDirectory($fixtureE) | Out-Null
    [System.IO.Directory]::CreateDirectory($fixtureF) | Out-Null
    $left = Join-Path $fixtureE 'backup.vhdx'
    $right = Join-Path $fixtureF 'recovery.vhdx'
    [System.IO.File]::WriteAllText($left, 'markorbit-e-backup-preflight-fixture', [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($right, 'markorbit-e-backup-preflight-fixture', [System.Text.Encoding]::UTF8)
    $inventory = Get-DirectoryInventoryNoFollowAllocated $fixtureE
    if ($inventory.file_count -ne 1 -or $inventory.vhdx_count -ne 1 -or $inventory.reparse_count -ne 0) { throw 'No-follow allocated inventory fixture failed.' }
    if ($inventory.allocated_bytes -lt 0 -or -not $inventory.metadata_manifest_sha256) { throw 'Allocated-byte/manifest fixture failed.' }
    $leftHash = Get-Sha256WithProgress $left 'fixture_e'
    $rightHash = Get-Sha256WithProgress $right 'fixture_f'
    if ($leftHash -ne $rightHash) { throw 'Full SHA identity fixture failed.' }
    $budget = Get-NewAllocationBudget 1000 800 30
    if ($budget -ne 500) { throw 'Recommended allocation budget fixture failed.' }
    Write-Host 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_PS51_CONTRACT_PASS'
}

try {
    Import-AcceptedHelpers

    if ($ContractOnly) {
        Invoke-ContractFixture
        return
    }

    Write-Host '===== PRODUCTION E BACKUP RECLAIM PREFLIGHT ====='
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
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ((git branch --show-current).Trim() -ne 'main') { throw 'E backup reclaim preflight must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-ToolingProvenance

    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'E backup reclaim preflight requires elevated Administrator PowerShell.' }

    $eBackup = Normalize-HostPath $EBackupRoot
    $fRecovery = Normalize-HostPath $FRecoveryRoot
    $expectedF = Normalize-HostPath $ExpectedFRecoveryVhdx
    if (-not $eBackup.Equals('E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'EBackupRoot changed.' }
    if (-not $fRecovery.Equals('F:\MarkOrbitData\recovery', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'FRecoveryRoot changed.' }
    if (-not $expectedF.Equals('F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'ExpectedFRecoveryVhdx changed.' }
    if (-not (Test-PathContains $fRecovery $expectedF)) { throw 'Expected F recovery VHDX escaped recovery root.' }

    $accepted = Resolve-AcceptedLayoutReplanReceipt
    Write-Host "accepted_layout_replan_receipt=$($accepted.path)"
    Write-Host "accepted_layout_replan_receipt_sha256=$($accepted.sha256)"

    $envPath = Join-Path $repoRoot '.env'
    $envShaBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { Get-Sha256 $envPath } else { $null }

    Assert-ExactMain 'preflight_before'
    $beforeBoundary = Assert-ProductionBoundary 'before'

    $eInventory = Get-DirectoryInventoryNoFollowAllocated $eBackup
    $fInventory = Get-DirectoryInventoryNoFollowAllocated $fRecovery
    Write-Host "e_backup_exists=$([bool]$eInventory.exists)"
    Write-Host "e_backup_file_count=$([int64]$eInventory.file_count)"
    Write-Host "e_backup_directory_count=$([int64]$eInventory.directory_count)"
    Write-Host "e_backup_logical_bytes=$([int64]$eInventory.logical_bytes)"
    Write-Host "e_backup_allocated_bytes=$([int64]$eInventory.allocated_bytes)"
    Write-Host "e_backup_non_vhdx_file_count=$([int64]$eInventory.non_vhdx_file_count)"
    Write-Host "e_backup_non_vhdx_logical_bytes=$([int64]$eInventory.non_vhdx_logical_bytes)"
    Write-Host "e_backup_vhdx_count=$([int64]$eInventory.vhdx_count)"
    Write-Host "e_backup_reparse_count=$([int64]$eInventory.reparse_count)"
    Write-Host "e_backup_metadata_manifest_sha256=$($eInventory.metadata_manifest_sha256)"
    Write-Host "f_recovery_exists=$([bool]$fInventory.exists)"
    Write-Host "f_recovery_file_count=$([int64]$fInventory.file_count)"
    Write-Host "f_recovery_logical_bytes=$([int64]$fInventory.logical_bytes)"
    Write-Host "f_recovery_vhdx_count=$([int64]$fInventory.vhdx_count)"
    Write-Host "f_recovery_reparse_count=$([int64]$fInventory.reparse_count)"

    $receiptE = $accepted.receipt.e_backup
    $inventoryMatchesReceipt = [bool](
        [bool]$eInventory.exists -and
        [int64]$eInventory.file_count -eq [int64]$receiptE.file_count -and
        [int64]$eInventory.logical_bytes -eq [int64]$receiptE.logical_bytes -and
        [int64]$eInventory.vhdx_count -eq [int64]$receiptE.vhdx_count -and
        [int64]$eInventory.reparse_count -eq [int64]$receiptE.reparse_count
    )
    Write-Host "e_backup_inventory_matches_accepted_replan=$inventoryMatchesReceipt"

    $expectedFReady = [bool](Test-Path -LiteralPath $expectedF -PathType Leaf)
    $expectedFInfo = $null
    $expectedFReparse = $false
    if ($expectedFReady) {
        $attributes = [System.IO.File]::GetAttributes($expectedF)
        $expectedFReparse = [bool](($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
        $expectedFInfo = New-Object System.IO.FileInfo($expectedF)
    }
    Write-Host "expected_f_recovery_vhdx_ready=$expectedFReady"
    Write-Host "expected_f_recovery_vhdx_reparse=$expectedFReparse"
    if ($expectedFInfo) {
        Write-Host "expected_f_recovery_vhdx_bytes=$([int64]$expectedFInfo.Length)"
        Write-Host "expected_f_recovery_vhdx_allocated_bytes=$(Get-AllocatedFileBytes $expectedF)"
    }

    $eSingleVhdx = $null
    if ([int64]$eInventory.vhdx_count -eq 1) { $eSingleVhdx = @($eInventory.vhdx)[0] }
    if ($eSingleVhdx) {
        Write-Host "e_backup_vhdx_path=$($eSingleVhdx.path)"
        Write-Host "e_backup_vhdx_bytes=$([int64]$eSingleVhdx.length)"
        Write-Host "e_backup_vhdx_allocated_bytes=$([int64]$eSingleVhdx.allocated_bytes)"
    }

    $attachment = if ($eSingleVhdx) { Get-VhdxAttachmentEvidence $eSingleVhdx.path } else { [ordered]@{ proof_available=$false; attached=$null; reason='Expected exactly one E VHDX.' } }
    Write-Host "e_backup_vhdx_attachment_proof_available=$([bool]$attachment.proof_available)"
    Write-Host "e_backup_vhdx_attached=$($attachment.attached)"
    if ($attachment.reason) { Write-Host "e_backup_vhdx_attachment_reason=$($attachment.reason)" }

    $preHashReferences = @(Get-CurrentBackupReferences $eBackup)
    Write-Host "e_backup_reference_count_pre_hash=$($preHashReferences.Count)"

    $structuralSafe = [bool](
        [bool]$eInventory.exists -and
        $inventoryMatchesReceipt -and
        [int64]$eInventory.reparse_count -eq 0 -and
        [int64]$fInventory.reparse_count -eq 0 -and
        [int64]$eInventory.vhdx_count -eq 1 -and
        $expectedFReady -and
        -not $expectedFReparse -and
        $preHashReferences.Count -eq 0 -and
        [bool]$attachment.proof_available -and
        -not [bool]$attachment.attached
    )
    Write-Host "e_backup_structural_safety_ready=$structuralSafe"

    $lengthEqual = $false
    if ($eSingleVhdx -and $expectedFInfo) {
        $lengthEqual = [bool]([int64]$eSingleVhdx.length -eq [int64]$expectedFInfo.Length)
    }
    Write-Host "e_f_recovery_vhdx_length_equal=$lengthEqual"

    $eHash = $null
    $fHash = $null
    $identityProven = $false
    $identityReason = 'structural_safety_not_ready'
    if ($structuralSafe -and -not $lengthEqual) {
        $identityReason = 'vhdx_length_mismatch_requires_functional_recovery_provenance_review'
    }
    elseif ($structuralSafe -and $lengthEqual) {
        Write-Host 'full_sha_identity_scan_started=True'
        $eHash = Get-Sha256WithProgress $eSingleVhdx.path 'e_backup_vhdx'
        $fHash = Get-Sha256WithProgress $expectedF 'f_recovery_vhdx'
        $identityProven = [bool]($eHash -eq $fHash)
        $identityReason = if ($identityProven) { 'full_sha256_equal' } else { 'full_sha256_mismatch' }
    }
    Write-Host "e_f_recovery_duplicate_identity_proven=$identityProven"
    Write-Host "e_f_recovery_identity_reason=$identityReason"

    $eAfter = Get-DirectoryInventoryNoFollowAllocated $eBackup
    $eInventoryStable = [bool](
        [string]$eAfter.metadata_manifest_sha256 -eq [string]$eInventory.metadata_manifest_sha256 -and
        [int64]$eAfter.file_count -eq [int64]$eInventory.file_count -and
        [int64]$eAfter.logical_bytes -eq [int64]$eInventory.logical_bytes -and
        [int64]$eAfter.allocated_bytes -eq [int64]$eInventory.allocated_bytes
    )
    Write-Host "e_backup_inventory_stable_after_hash=$eInventoryStable"

    $fInfoAfter = if ($expectedFReady) { New-Object System.IO.FileInfo($expectedF) } else { $null }
    $fMetadataStable = [bool](
        $expectedFInfo -and $fInfoAfter -and
        [int64]$fInfoAfter.Length -eq [int64]$expectedFInfo.Length -and
        [int64]$fInfoAfter.LastWriteTimeUtc.Ticks -eq [int64]$expectedFInfo.LastWriteTimeUtc.Ticks
    )
    Write-Host "f_recovery_vhdx_metadata_stable_after_hash=$fMetadataStable"

    $driveE = Get-DriveSnapshot 'E'
    $warmPhysicalRequired = [int64]$accepted.receipt.copy_model.e_warm_physical_required_bytes
    $projectedFreeAllocated = [int64][math]::Min(
        [double]$driveE.total_bytes,
        [double]([int64]$driveE.free_bytes + [int64]$eInventory.allocated_bytes)
    )
    $projectedRecommendedBudget = Get-NewAllocationBudget ([int64]$driveE.total_bytes) $projectedFreeAllocated 30
    $projectedHardBudget = Get-NewAllocationBudget ([int64]$driveE.total_bytes) $projectedFreeAllocated 20
    $recommendedMargin = [int64]($projectedRecommendedBudget - $warmPhysicalRequired)
    $hardMargin = [int64]($projectedHardBudget - $warmPhysicalRequired)
    $recommendedFit = [bool]($recommendedMargin -ge 0)
    $hardFit = [bool]($hardMargin -ge 0)
    Write-Host "drive_E_total_bytes=$([int64]$driveE.total_bytes)"
    Write-Host "drive_E_free_before_bytes=$([int64]$driveE.free_bytes)"
    Write-Host "e_backup_actual_allocated_reclaim_bytes=$([int64]$eInventory.allocated_bytes)"
    Write-Host "drive_E_projected_free_after_actual_allocated_reclaim_bytes=$projectedFreeAllocated"
    Write-Host "scenario_e_warm_physical_required_bytes=$warmPhysicalRequired"
    Write-Host "scenario_e_projected_recommended_budget_after_actual_reclaim_bytes=$projectedRecommendedBudget"
    Write-Host "scenario_e_projected_hard_budget_after_actual_reclaim_bytes=$projectedHardBudget"
    Write-Host "scenario_e_projected_recommended_margin_after_actual_reclaim_bytes=$recommendedMargin"
    Write-Host "scenario_e_projected_hard_margin_after_actual_reclaim_bytes=$hardMargin"
    Write-Host "scenario_e_projected_recommended_fit_after_actual_reclaim=$recommendedFit"
    Write-Host "scenario_e_projected_hard_fit_after_actual_reclaim=$hardFit"

    Assert-ExactMain 'preflight_final'
    $finalBoundary = Assert-ProductionBoundary 'final'
    $finalReferences = @(Get-CurrentBackupReferences $eBackup)
    $envShaAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { Get-Sha256 $envPath } else { $null }
    $envUnchanged = [bool]($envShaBefore -eq $envShaAfter)
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host "e_backup_reference_count_final=$($finalReferences.Count)"

    $decision = 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_BLOCKED'
    $nextGate = 'PRODUCTION_E_BACKUP_FUNCTIONAL_RECOVERY_PROVENANCE_REVIEW'
    if ($identityProven -and $eInventoryStable -and $fMetadataStable -and $envUnchanged -and $finalReferences.Count -eq 0 -and $recommendedFit -and $hardFit) {
        $decision = 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_READY'
        $nextGate = 'PRODUCTION_E_BACKUP_RECLAIM_APPLY_IMPLEMENTATION'
    }
    elseif ($identityProven -and (-not $recommendedFit -or -not $hardFit)) {
        $nextGate = 'PRODUCTION_STORAGE_COEXISTENCE_REDESIGN'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_e_backup_reclaim_preflight_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:PreflightReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        read_only=$true
        mutation_performed=$false
        accepted_layout_replan_receipt_path=$accepted.path
        accepted_layout_replan_receipt_sha256=$accepted.sha256
        e_backup=[ordered]@{
            root=$eBackup
            exists=[bool]$eInventory.exists
            file_count=[int64]$eInventory.file_count
            directory_count=[int64]$eInventory.directory_count
            logical_bytes=[int64]$eInventory.logical_bytes
            allocated_bytes=[int64]$eInventory.allocated_bytes
            non_vhdx_file_count=[int64]$eInventory.non_vhdx_file_count
            non_vhdx_logical_bytes=[int64]$eInventory.non_vhdx_logical_bytes
            vhdx_count=[int64]$eInventory.vhdx_count
            reparse_count=[int64]$eInventory.reparse_count
            metadata_manifest_sha256=$eInventory.metadata_manifest_sha256
            inventory_matches_accepted_replan=$inventoryMatchesReceipt
            attachment_proof_available=[bool]$attachment.proof_available
            attached=$attachment.attached
            reference_count_final=[int]$finalReferences.Count
            inventory_stable_after_hash=$eInventoryStable
            delete_authorized=$false
        }
        f_recovery=[ordered]@{
            root=$fRecovery
            expected_vhdx=$expectedF
            expected_vhdx_ready=$expectedFReady
            expected_vhdx_reparse=$expectedFReparse
            expected_vhdx_bytes=if ($expectedFInfo) { [int64]$expectedFInfo.Length } else { [int64]0 }
            metadata_stable_after_hash=$fMetadataStable
        }
        duplicate_identity=[ordered]@{
            length_equal=$lengthEqual
            e_sha256=$eHash
            f_sha256=$fHash
            proven=$identityProven
            reason=$identityReason
        }
        capacity=[ordered]@{
            e_total_bytes=[int64]$driveE.total_bytes
            e_free_before_bytes=[int64]$driveE.free_bytes
            actual_allocated_reclaim_bytes=[int64]$eInventory.allocated_bytes
            projected_free_after_actual_allocated_reclaim_bytes=$projectedFreeAllocated
            warm_physical_required_bytes=$warmPhysicalRequired
            projected_recommended_budget_bytes=$projectedRecommendedBudget
            projected_hard_budget_bytes=$projectedHardBudget
            projected_recommended_margin_bytes=$recommendedMargin
            projected_hard_margin_bytes=$hardMargin
            projected_recommended_fit=$recommendedFit
            projected_hard_fit=$hardFit
        }
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
            cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_e_backup_reclaim_preflight.json'
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Assert-ExactMain 'exit'
    Write-Host '===== PRODUCTION E BACKUP RECLAIM PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host "e_backup_duplicate_identity_proven=$identityProven"
    Write-Host "e_backup_actual_allocated_reclaim_bytes=$([int64]$eInventory.allocated_bytes)"
    Write-Host "scenario_e_projected_recommended_fit_after_actual_reclaim=$recommendedFit"
    Write-Host "scenario_e_projected_recommended_margin_after_actual_reclaim_bytes=$recommendedMargin"
    Write-Host 'e_backup_delete_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_E_BACKUP_RECLAIM_PREFLIGHT_DONE'
}
finally {
    Pop-Location
}
