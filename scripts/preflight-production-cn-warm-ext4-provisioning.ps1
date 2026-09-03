[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedCnWarmEquivalenceReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$ToolingDistro = 'Ubuntu-24.04',
    [string]$EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',
    [string]$ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedEquivalenceEngineSha = '96befe0ae4824dfe2f0ffed48d0b12cc0c508e0f'
$script:AcceptedEquivalenceReceiptVersion = 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_V1'
$script:AcceptedEquivalenceCommentId = '5519154978'
$script:ReceiptVersion = 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_V1'
$script:ArchitectureVersion = 'DEDICATED_ORDINARY_WSL2_CLICKHOUSE_EXT4_V1_ISSUES_402_410'
$script:ExpectedWarmCandidateManifestSha256 = '716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231'
$script:ExpectedWarmCandidateTableCount = [int64]4
$script:ExpectedWarmActiveCandidateTableCount = [int64]4
$script:ExpectedWarmCandidateRows = [int64]2430570761
$script:ExpectedWarmCandidateBytes = [int64]562600035674
$script:ExpectedWarmRequiredPhysicalBytes = [int64]618860039242
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:CopySafetyMarginPercent = [double]10
$script:FilesystemRuntimeOverheadPercent = [double]8
$script:MinimumFilesystemRuntimeOverheadBytes = [int64](64GB)
$script:FutureExpansionHeadroomPercent = [double]25
$script:ProposedWarmVhdxPath = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ProposedWarmMountName = 'markorbit_prod_warm_cn'
$script:DockerDesktopWslRoot = 'D:\DockerData\DockerDesktopWSL'
$script:ProtectedVhdxPaths = @(
    'D:\MarkOrbitData\spike\hot_cn_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_us_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_global_spike.vhdx',
    'E:\MarkOrbitData\spike\warm_spike.vhdx',
    'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike\ext4.vhdx',
    'E:\MarkOrbitData\wsl-tooling\Ubuntu-24.04\ext4.vhdx',
    'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
)
$script:AllowedToolingFiles = @(
    'scripts/preflight-production-cn-warm-ext4-provisioning.ps1',
    'tests/test_production_cn_warm_ext4_provisioning_preflight_contract.py',
    '.github/workflows/production-cn-warm-ext4-provisioning-preflight-runtime.yml'
)

function Get-StringSha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))
        return ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label file missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Normalize-WindowsPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $candidate = $Path.Trim()
    if ($candidate.StartsWith('\\?\')) { $candidate = $candidate.Substring(4) }
    if ($candidate.StartsWith('\??\')) { $candidate = $candidate.Substring(4) }
    if ($candidate -notmatch '^[A-Za-z]:[\\/]') { return '' }
    return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
}

function Test-PathContains([string]$ParentPath, [string]$ChildPath) {
    $parent = Normalize-WindowsPath $ParentPath
    $child = Normalize-WindowsPath $ChildPath
    if (-not $parent -or -not $child) { return $false }
    if ($child.Equals($parent, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $child.StartsWith($parent + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-PathsOverlap([string]$LeftPath, [string]$RightPath) {
    return [bool]((Test-PathContains $LeftPath $RightPath) -or (Test-PathContains $RightPath $LeftPath))
}

function Round-UpToGiB([int64]$Bytes) {
    if ($Bytes -lt 0) { throw 'Bytes must be non-negative.' }
    if ($Bytes -eq 0) { return [int64]0 }
    $gib = [int64](1GB)
    return [int64]([math]::Ceiling([double]$Bytes / [double]$gib) * [double]$gib)
}

function Get-RequiredCapacityBytes([int64]$PayloadBytes, [double]$MarginPercent) {
    if ($PayloadBytes -lt 0 -or $MarginPercent -lt 0 -or $MarginPercent -gt 100) { throw 'Invalid copy-safety inputs.' }
    return [int64][math]::Ceiling([double]$PayloadBytes * (1.0 + ($MarginPercent / 100.0)))
}

function Get-RecommendedBudget([int64]$TotalBytes, [int64]$FreeBytes) {
    if ($TotalBytes -le 0 -or $FreeBytes -lt 0 -or $FreeBytes -gt $TotalBytes) { throw 'Invalid E drive capacity.' }
    $reserve = [int64][math]::Ceiling([double]$TotalBytes * 0.30)
    return [int64][math]::Max([int64]0, [int64]($FreeBytes - $reserve))
}

function Get-ProvisioningSizeModel {
    param(
        [Parameter(Mandatory = $true)][int64]$PayloadBytes,
        [double]$CopySafetyMarginPercent = 10,
        [double]$FilesystemRuntimeOverheadPercent = 8,
        [int64]$MinimumFilesystemRuntimeOverheadBytes = 68719476736,
        [double]$FutureExpansionHeadroomPercent = 25
    )
    if ($PayloadBytes -le 0) { throw 'Warm payload must be positive.' }
    $copyRequired = Get-RequiredCapacityBytes $PayloadBytes $CopySafetyMarginPercent
    $percentageOverhead = [int64][math]::Ceiling([double]$copyRequired * ($FilesystemRuntimeOverheadPercent / 100.0))
    $filesystemRuntimeOverhead = [int64][math]::Max($MinimumFilesystemRuntimeOverheadBytes, $percentageOverhead)
    $futureHeadroom = [int64][math]::Ceiling([double]$copyRequired * ($FutureExpansionHeadroomPercent / 100.0))
    $unroundedMax = [int64]($copyRequired + $filesystemRuntimeOverhead + $futureHeadroom)
    $proposedMax = Round-UpToGiB $unroundedMax
    return [ordered]@{
        payload_bytes=$PayloadBytes
        copy_safety_margin_percent=$CopySafetyMarginPercent
        copy_required_bytes=$copyRequired
        filesystem_runtime_overhead_percent=$FilesystemRuntimeOverheadPercent
        minimum_filesystem_runtime_overhead_bytes=$MinimumFilesystemRuntimeOverheadBytes
        filesystem_runtime_overhead_bytes=$filesystemRuntimeOverhead
        future_expansion_headroom_percent=$FutureExpansionHeadroomPercent
        future_expansion_headroom_bytes=$futureHeadroom
        proposed_vhdx_max_bytes_unrounded=$unroundedMax
        proposed_vhdx_max_bytes=$proposedMax
        proposed_ext4_quota_bytes=$proposedMax
    }
}

function Get-CandidateManifestHash([object[]]$Candidates) {
    $lines = @()
    foreach ($row in @($Candidates | Sort-Object table)) {
        $lines += @(
            [string]$row.table,
            [string]$row.schema_fingerprint_sha256,
            [string]$row.active_parts,
            [string]$row.rows_from_parts,
            [string]$row.bytes_on_disk,
            (@($row.disk_names) -join ','),
            [string]$row.proposed_tier
        ) -join '|'
    }
    return Get-StringSha256 ($lines -join "`n")
}

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
        $scoped = [regex]::Replace($definitionText, $pattern, '${1}script:' + $name, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($scoped -eq $definitionText) { throw "Unable to scope $Label helper definition: $name" }
        Invoke-Expression $scoped
    }
}

function Import-AcceptedProductionHelpers {
    Import-FunctionDefinitions (Join-Path $PSScriptRoot 'preflight-production-rebalance-phase2-d-full-sha256.ps1') @(
        'Invoke-NativeText', 'Assert-ExactMain', 'Get-ProductionClickHouseHealth',
        'Assert-AcceptedProductionMount', 'Assert-RawConsumersStopped'
    ) 'Phase2D'
}

function Assert-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'CN Warm ext4 provisioning preflight requires Administrator PowerShell.'
    }
}

function Assert-ToolingProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedEquivalenceEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted equivalence SHA is not an ancestor of exact main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedEquivalenceEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedToolingFiles })
    $missing = @($script:AllowedToolingFiles | Where-Object { $_ -notin $changed })
    Write-Host "accepted_equivalence_to_current_changed_file_count=$($changed.Count)"
    Write-Host "accepted_equivalence_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "accepted_equivalence_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'CN Warm ext4 provisioning tooling changed outside the exact 3-file boundary.'
    }
}

function Resolve-AcceptedEquivalenceReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedCnWarmEquivalenceReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted CN Warm equivalence receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedEquivalenceReceiptVersion) { throw 'Unexpected CN Warm equivalence receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedEquivalenceEngineSha) { throw 'Accepted CN Warm equivalence engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_READY') { throw 'Accepted CN Warm equivalence decision changed.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT') { throw 'Accepted CN Warm equivalence next gate changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.data_mutation_performed) { throw 'Accepted CN Warm equivalence receipt is not read-only.' }
    if (-not [bool]$receipt.migration_equivalence_strategy_ready -or [bool]$receipt.migration_completed) { throw 'Accepted CN Warm equivalence readiness/completion state changed.' }
    if ([int64]$receipt.warm_candidate_table_count -ne $script:ExpectedWarmCandidateTableCount) { throw 'Warm candidate table count changed.' }
    if ([int64]$receipt.warm_active_candidate_table_count -ne $script:ExpectedWarmActiveCandidateTableCount) { throw 'Warm active candidate table count changed.' }
    if ([int64]$receipt.warm_candidate_rows -ne $script:ExpectedWarmCandidateRows) { throw 'Warm candidate row count changed.' }
    if ([int64]$receipt.warm_candidate_bytes -ne $script:ExpectedWarmCandidateBytes) { throw 'Warm candidate byte count changed.' }
    if ([string]$receipt.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmCandidateManifestSha256) { throw 'Warm candidate manifest SHA changed.' }
    if ([int64]$receipt.capacity.fresh_warm_required_physical_bytes -ne $script:ExpectedWarmRequiredPhysicalBytes) { throw 'Warm physical-copy requirement changed.' }
    if (-not [bool]$receipt.capacity.recommended_30_percent_admission) { throw 'Accepted equivalence did not preserve E 30 percent admission.' }
    if (-not [bool]$receipt.production_invariant_preserved -or -not [bool]$receipt.env_unchanged) { throw 'Accepted equivalence production/env invariant changed.' }
    if ([bool]$receipt.constraints.cn_warm_move_authorized -or [bool]$receipt.constraints.vhdx_mutation_authorized -or [bool]$receipt.constraints.clickhouse_mutation_authorized) { throw 'Accepted equivalence unexpectedly granted later mutation authority.' }

    $candidates = @($receipt.warm_candidates)
    if ($candidates.Count -ne $script:ExpectedWarmCandidateTableCount) { throw 'Accepted warm candidate array count changed.' }
    $sumRows = [int64](($candidates | Measure-Object -Property rows_from_parts -Sum).Sum)
    $sumBytes = [int64](($candidates | Measure-Object -Property bytes_on_disk -Sum).Sum)
    if ($sumRows -ne $script:ExpectedWarmCandidateRows) { throw 'Warm candidate rows do not re-sum to accepted total.' }
    if ($sumBytes -ne $script:ExpectedWarmCandidateBytes) { throw 'Warm candidate bytes do not re-sum to accepted total.' }
    foreach ($candidate in $candidates) {
        if (([int64]$candidate.active_parts -gt 0 -or [int64]$candidate.bytes_on_disk -gt 0) -and
            (-not [string]$candidate.source_disk -or @($candidate.disk_names).Count -ne 1)) {
            throw "Warm candidate source disk is no longer frozen: $($candidate.table)"
        }
    }
    $manifestSha = Get-CandidateManifestHash $candidates
    if ($manifestSha -ne $script:ExpectedWarmCandidateManifestSha256) { throw 'Warm candidate manifest failed canonical SHA recomputation.' }
    $physical = Get-RequiredCapacityBytes $sumBytes $script:CopySafetyMarginPercent
    if ($physical -ne $script:ExpectedWarmRequiredPhysicalBytes) { throw 'Warm physical-copy requirement failed recomputation.' }
    return [ordered]@{
        path=$path; sha256=(Get-FileSha256 $path); candidates=@($candidates)
        recomputed_manifest_sha256=$manifestSha; recomputed_rows=$sumRows
        recomputed_bytes=$sumBytes; recomputed_physical_bytes=$physical
    }
}

function Get-WslDistros {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $rows = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.DistributionName) { continue }
        $base = if ($item.BasePath) { Normalize-WindowsPath ([string]$item.BasePath) } else { '' }
        $rootVhdx = if ($base) { Join-Path $base 'ext4.vhdx' } else { '' }
        $rows += [ordered]@{
            name=[string]$item.DistributionName
            version=if ($null -ne $item.Version) { [int]$item.Version } else { $null }
            base_path=$base
            root_vhdx=$rootVhdx
            root_vhdx_exists=[bool]($rootVhdx -and (Test-Path -LiteralPath $rootVhdx -PathType Leaf))
        }
    }
    return @($rows | Sort-Object name)
}

function Test-WslFindmntReady([string]$DistroName) {
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$DistroName,'-u','root','--','sh','-lc','command -v findmnt >/dev/null 2>&1') -AllowFailure
    return [bool]($probe.exit_code -eq 0)
}

function Get-WslMountInventory([string]$DistroName) {
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$DistroName,'-u','root','--','findmnt','-rn','-o','SOURCE,TARGET,FSTYPE') -AllowFailure
    if ($probe.exit_code -ne 0) { return @() }
    $rows = @()
    foreach ($line in @($probe.lines)) {
        $text = ([string]$line).Trim()
        if (-not $text -or $text -notmatch '\s/mnt/wsl/') { continue }
        $parts = @($text -split '\s+', 3)
        $rows += [ordered]@{ source=$parts[0]; target=$parts[1]; filesystem=if ($parts.Count -ge 3) { $parts[2] } else { '' } }
    }
    return @($rows)
}

function Get-NoFollowVhdxInventory([string[]]$Roots) {
    $items = @()
    $visited = @{}
    foreach ($inputRoot in $Roots) {
        $root = Normalize-WindowsPath $inputRoot
        if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $stack = New-Object 'System.Collections.Generic.Stack[string]'
        $stack.Push($root)
        while ($stack.Count -gt 0) {
            $directory = $stack.Pop()
            $key = $directory.ToLowerInvariant()
            if ($visited.ContainsKey($key)) { continue }
            $visited[$key] = $true
            $dirAttributes = [System.IO.File]::GetAttributes($directory)
            if (($dirAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                $items += [ordered]@{ path=$directory; kind='reparse_directory_skipped'; bytes=$null }
                continue
            }
            foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
                $attributes = [System.IO.File]::GetAttributes($entry)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    $items += [ordered]@{ path=(Normalize-WindowsPath $entry); kind='reparse_entry_skipped'; bytes=$null }
                }
                elseif (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { $stack.Push($entry) }
                elseif ([System.IO.Path]::GetExtension($entry).Equals('.vhdx', [System.StringComparison]::OrdinalIgnoreCase)) {
                    $file = New-Object System.IO.FileInfo($entry)
                    $items += [ordered]@{ path=(Normalize-WindowsPath $entry); kind='vhdx'; bytes=[int64]$file.Length }
                }
            }
        }
    }
    return @($items | Sort-Object path)
}

function Get-DiskImageSnapshot([string]$Path) {
    $normalized = Normalize-WindowsPath $Path
    $exists = [bool]($normalized -and (Test-Path -LiteralPath $normalized -PathType Leaf))
    $supported = [bool](Get-Command Get-DiskImage -ErrorAction SilentlyContinue)
    if (-not $exists -or -not $supported) {
        return [ordered]@{ path=$normalized; exists=$exists; query_supported=$supported; attached=$null; query_error=$null }
    }
    try {
        $image = Get-DiskImage -ImagePath $normalized -ErrorAction Stop
        return [ordered]@{ path=$normalized; exists=$true; query_supported=$true; attached=[bool]$image.Attached; query_error=$null }
    }
    catch { return [ordered]@{ path=$normalized; exists=$true; query_supported=$true; attached=$null; query_error=$_.Exception.Message } }
}

function Get-ReparseAncestorBlocker([string]$Path) {
    $normalized = Normalize-WindowsPath $Path
    if (-not $normalized) { return 'PROPOSED_WARM_VHDX_PATH_INVALID' }
    $cursor = [System.IO.Path]::GetDirectoryName($normalized)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $attributes = [System.IO.File]::GetAttributes($cursor)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return 'PROPOSED_WARM_VHDX_PARENT_REPARSE_POINT' }
        }
        $next = [System.IO.Path]::GetDirectoryName($cursor)
        if (-not $next -or $next -eq $cursor) { break }
        $cursor = $next
    }
    return $null
}

function Get-ProposedPathBlockers {
    param(
        [Parameter(Mandatory = $true)][string]$ProposedPath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Distros,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$VhdxInventory
    )
    $blockers = @()
    $proposed = Normalize-WindowsPath $ProposedPath
    if (-not $proposed) { return @('PROPOSED_WARM_VHDX_PATH_INVALID') }
    if ((Split-Path -Qualifier $proposed).ToUpperInvariant() -ne 'E:') { $blockers += 'PROPOSED_WARM_VHDX_NOT_ON_E' }
    if (Test-Path -LiteralPath $proposed) { $blockers += 'PROPOSED_WARM_VHDX_PATH_ALREADY_EXISTS' }
    foreach ($protected in $script:ProtectedVhdxPaths) {
        if ($proposed.Equals((Normalize-WindowsPath $protected), [System.StringComparison]::OrdinalIgnoreCase)) {
            $blockers += 'PROPOSED_WARM_VHDX_COLLIDES_WITH_PROTECTED_VHDX'
        }
    }
    foreach ($row in @($VhdxInventory | Where-Object { $_.kind -eq 'vhdx' })) {
        if ($proposed.Equals((Normalize-WindowsPath ([string]$row.path)), [System.StringComparison]::OrdinalIgnoreCase)) {
            $blockers += 'PROPOSED_WARM_VHDX_COLLIDES_WITH_EXISTING_E_VHDX'
        }
    }
    $parent = Normalize-WindowsPath ([System.IO.Path]::GetDirectoryName($proposed))
    if (Test-PathsOverlap $script:DockerDesktopWslRoot $parent) { $blockers += 'PROPOSED_WARM_VHDX_OVERLAPS_DOCKER_DESKTOP_WSL_ROOT' }
    foreach ($distro in $Distros) {
        if ([string]$distro.base_path -and (Test-PathsOverlap ([string]$distro.base_path) $parent)) {
            $blockers += "PROPOSED_WARM_VHDX_OVERLAPS_WSL_DISTRO_ROOT:$($distro.name)"
        }
    }
    $reparse = Get-ReparseAncestorBlocker $proposed
    if ($reparse) { $blockers += $reparse }
    return @($blockers | Sort-Object -Unique)
}

function Invoke-ContractFixture {
    Import-AcceptedProductionHelpers
    foreach ($helper in @('Invoke-NativeText','Assert-ExactMain','Get-ProductionClickHouseHealth','Assert-AcceptedProductionMount','Assert-RawConsumersStopped')) {
        if (-not (Get-Command $helper -ErrorAction SilentlyContinue)) { throw "Imported helper missing in contract fixture: $helper" }
    }
    $size = Get-ProvisioningSizeModel -PayloadBytes ([int64](10GB)) -CopySafetyMarginPercent 10 -FilesystemRuntimeOverheadPercent 8 -MinimumFilesystemRuntimeOverheadBytes ([int64](1GB)) -FutureExpansionHeadroomPercent 25
    if ([int64]$size.proposed_vhdx_max_bytes -le [int64]$size.copy_required_bytes) { throw 'Provisioning size model did not add overhead/headroom.' }
    if (([int64]$size.proposed_vhdx_max_bytes % [int64](1GB)) -ne 0) { throw 'Provisioning max size did not round to GiB.' }
    if ((Get-RecommendedBudget ([int64](100GB)) ([int64](90GB))) -ne [int64](60GB)) { throw '30 percent reserve budget fixture failed.' }
    $fixture = Join-Path ([System.IO.Path]::GetTempPath()) ('markorbit_cn_warm_ext4_' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $fixture | Out-Null
        $retained = Join-Path $fixture 'retained.vhdx'
        [System.IO.File]::WriteAllBytes($retained, [byte[]]@(1,2,3,4))
        $inventory = @([ordered]@{ path=(Normalize-WindowsPath $retained); kind='vhdx'; bytes=[int64]4 })
        $found = @($inventory | Where-Object { (Normalize-WindowsPath ([string]$_.path)).Equals((Normalize-WindowsPath $retained), [System.StringComparison]::OrdinalIgnoreCase) }).Count
        if ($found -ne 1) { throw 'Retained VHDX collision fixture failed.' }
    }
    finally { if (Test-Path -LiteralPath $fixture) { [System.IO.Directory]::Delete($fixture, $true) } }
    Write-Host 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_CONTRACT_DIRECT_INVOCATION_OK'
}

try {
    Write-Host '===== PRODUCTION CN WARM EXT4 PROVISIONING PREFLIGHT ====='
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'apply_surface_present=False'
    Write-Host 'resume_surface_present=False'
    foreach ($marker in @(
        'vhdx_create_authorized=False','vhdx_resize_authorized=False','vhdx_mount_authorized=False',
        'vhdx_detach_authorized=False','vhdx_compact_authorized=False','vhdx_move_authorized=False',
        'vhdx_delete_authorized=False','wsl_mutation_authorized=False','clickhouse_mutation_authorized=False',
        'cn_warm_move_authorized=False','docker_restart_authorized=False','docker_prune_authorized=False',
        'accepted_volume_mutation_authorized=False','raw_delete_authorized=False','cn_replay_authorized=False','us_bulk_authorized=False'
    )) { Write-Host $marker }

    if ($ContractOnly) { Invoke-ContractFixture; exit 0 }

    Import-AcceptedProductionHelpers
    Assert-Administrator
    if ((git branch --show-current).Trim() -ne 'main') { throw 'CN Warm ext4 provisioning preflight must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-ToolingProvenance
    $accepted = Resolve-AcceptedEquivalenceReceipt

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env missing.' }
    $envShaBefore = Get-FileSha256 $envPath
    if (Test-Path -LiteralPath $EBackupRoot) { throw 'Superseded E backup root unexpectedly exists after accepted reclaim.' }
    if (-not (Test-Path -LiteralPath $ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX missing.' }
    $fInfo = New-Object System.IO.FileInfo($ExpectedFRecoveryVhdx)
    if ([int64]$fInfo.Length -ne $script:ExpectedFRecoveryBytes) { throw 'Retained F recovery VHDX length changed.' }
    if (($fInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Retained F recovery VHDX became a reparse point.' }

    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before provisioning preflight.' }
    Assert-AcceptedProductionMount $productionBefore.container_id
    Assert-ExactMain 'inventory_before'

    $distros = @(Get-WslDistros)
    $tooling = @($distros | Where-Object { $_.name -eq $ToolingDistro })
    $toolingRegistered = [bool]($tooling.Count -eq 1)
    $toolingVersion2 = [bool]($toolingRegistered -and [int]$tooling[0].version -eq 2)
    $toolingFindmntReady = if ($toolingVersion2) { Test-WslFindmntReady $ToolingDistro } else { $false }
    $wslVersion = Invoke-NativeText 'wsl.exe' @('--version') -AllowFailure
    $wslMounts = if ($toolingFindmntReady) { @(Get-WslMountInventory $ToolingDistro) } else { @() }

    $inventoryRoots = @(
        'E:\MarkOrbitData\spike','E:\MarkOrbitData\wsl-tooling','E:\MarkOrbitData\wsl-runtime',
        'E:\MarkOrbitData\production','E:\DockerData','E:\DockerDataBackup'
    )
    foreach ($distro in $distros) {
        $base = Normalize-WindowsPath ([string]$distro.base_path)
        if ($base -and $base.StartsWith('E:\', [System.StringComparison]::OrdinalIgnoreCase)) { $inventoryRoots += $base }
    }
    $eVhdxInventory = @(Get-NoFollowVhdxInventory @($inventoryRoots | Sort-Object -Unique))
    $diskImagePaths = @($script:ProtectedVhdxPaths + @($eVhdxInventory | Where-Object { $_.kind -eq 'vhdx' } | ForEach-Object { [string]$_.path }) | Sort-Object -Unique)
    $diskImageStates = @()
    foreach ($path in $diskImagePaths) { $diskImageStates += Get-DiskImageSnapshot $path }

    $pathBlockers = @(Get-ProposedPathBlockers -ProposedPath $script:ProposedWarmVhdxPath -Distros $distros -VhdxInventory $eVhdxInventory)
    $sizeModel = Get-ProvisioningSizeModel `
        -PayloadBytes $accepted.recomputed_bytes `
        -CopySafetyMarginPercent $script:CopySafetyMarginPercent `
        -FilesystemRuntimeOverheadPercent $script:FilesystemRuntimeOverheadPercent `
        -MinimumFilesystemRuntimeOverheadBytes $script:MinimumFilesystemRuntimeOverheadBytes `
        -FutureExpansionHeadroomPercent $script:FutureExpansionHeadroomPercent
    if ([int64]$sizeModel.copy_required_bytes -ne $script:ExpectedWarmRequiredPhysicalBytes) { throw 'Provisioning size model copy requirement diverged from accepted equivalence.' }

    $eDrive = New-Object System.IO.DriveInfo('E')
    $eTotal = [int64]$eDrive.TotalSize
    $eFree = [int64]$eDrive.AvailableFreeSpace
    $recommendedReserve = [int64][math]::Ceiling([double]$eTotal * 0.30)
    $recommendedBudget = Get-RecommendedBudget $eTotal $eFree
    $proposedMax = [int64]$sizeModel.proposed_vhdx_max_bytes
    $recommendedMarginAfterMax = [int64]($recommendedBudget - $proposedMax)
    $recommendedAdmission = [bool]($recommendedMarginAfterMax -ge 0)

    $blockers = @($pathBlockers)
    if ($wslVersion.exit_code -ne 0) { $blockers += 'WSL_VERSION_UNAVAILABLE' }
    if (-not $toolingRegistered) { $blockers += 'TOOLING_DISTRO_MISSING' }
    elseif (-not $toolingVersion2) { $blockers += 'TOOLING_DISTRO_NOT_WSL2' }
    elseif (-not $toolingFindmntReady) { $blockers += 'TOOLING_DISTRO_FINDMNT_UNAVAILABLE' }
    if ($proposedMax -le [int64]$accepted.recomputed_physical_bytes) { $blockers += 'PROPOSED_WARM_VHDX_MAX_HAS_NO_OVERHEAD_OR_HEADROOM' }
    if (-not $recommendedAdmission) { $blockers += 'PROPOSED_WARM_VHDX_MAX_EXCEEDS_30_PERCENT_ADMISSION_BUDGET' }
    $blockers = @($blockers | Sort-Object -Unique)

    Write-Host "accepted_equivalence_receipt=$($accepted.path)"
    Write-Host "accepted_equivalence_receipt_sha256=$($accepted.sha256)"
    Write-Host "accepted_equivalence_comment_id=$script:AcceptedEquivalenceCommentId"
    Write-Host "warm_candidate_manifest_sha256=$($accepted.recomputed_manifest_sha256)"
    Write-Host "warm_candidate_rows=$($accepted.recomputed_rows)"
    Write-Host "warm_candidate_bytes=$($accepted.recomputed_bytes)"
    Write-Host "warm_required_physical_bytes_with_safety=$($accepted.recomputed_physical_bytes)"
    foreach ($distro in $distros) { Write-Host "wsl_distro=$($distro.name)|version=$($distro.version)|base_path=$($distro.base_path)|root_vhdx=$($distro.root_vhdx)" }
    foreach ($mount in $wslMounts) { Write-Host "wsl_mount=$($mount.source)|$($mount.target)|$($mount.filesystem)" }
    foreach ($item in $eVhdxInventory) { Write-Host "e_vhdx_inventory=$($item.kind)|$($item.bytes)|$($item.path)" }
    foreach ($state in $diskImageStates) { Write-Host "disk_image_state=$($state.exists)|$($state.attached)|$($state.path)" }
    Write-Host "proposed_warm_vhdx_path=$script:ProposedWarmVhdxPath"
    Write-Host "proposed_warm_mount_name=$script:ProposedWarmMountName"
    Write-Host "proposed_ext4_quota_bytes=$($sizeModel.proposed_ext4_quota_bytes)"
    Write-Host "proposed_vhdx_max_bytes=$proposedMax"
    Write-Host "filesystem_runtime_overhead_bytes=$($sizeModel.filesystem_runtime_overhead_bytes)"
    Write-Host "future_expansion_headroom_bytes=$($sizeModel.future_expansion_headroom_bytes)"
    Write-Host "e_total_bytes=$eTotal"
    Write-Host "e_free_bytes=$eFree"
    Write-Host "e_recommended_30_percent_reserve_bytes=$recommendedReserve"
    Write-Host "e_recommended_allocation_budget_bytes=$recommendedBudget"
    Write-Host "e_recommended_margin_after_proposed_vhdx_max_bytes=$recommendedMarginAfterMax"
    Write-Host "recommended_30_percent_admission=$recommendedAdmission"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }

    $ready = [bool]($blockers.Count -eq 0)
    $decision = if ($ready) { 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_READY' } else { 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_BLOCKED' }
    $nextGate = if ($ready) { 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_IMPLEMENTATION' } else { 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_REVIEW' }

    Assert-RawConsumersStopped
    $productionAfter = Get-ProductionClickHouseHealth
    if (-not [bool]$productionAfter.ready) { throw 'Production ClickHouse must remain healthy after provisioning preflight.' }
    Assert-AcceptedProductionMount $productionAfter.container_id
    Assert-ExactMain 'final'
    if ((Get-FileSha256 $envPath) -ne $envShaBefore) { throw '.env changed during CN Warm ext4 provisioning preflight.' }
    if (Test-Path -LiteralPath $EBackupRoot) { throw 'Superseded E backup root reappeared.' }
    if (-not (Test-Path -LiteralPath $ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX disappeared.' }
    if (Test-Path -LiteralPath $script:ProposedWarmVhdxPath) { throw 'Proposed production Warm VHDX path was created during a read-only preflight.' }

    $timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_cn_warm_ext4_provisioning_preflight_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion; engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        architecture_version=$script:ArchitectureVersion; decision=$decision; next_gate=$nextGate
        read_only = $true; mutation_performed = $false; provisioning_completed = $false
        accepted_equivalence=[ordered]@{
            engine_sha=$script:AcceptedEquivalenceEngineSha; accepted_comment_id=$script:AcceptedEquivalenceCommentId
            receipt_path=$accepted.path; receipt_sha256=$accepted.sha256
            warm_candidate_manifest_sha256=$accepted.recomputed_manifest_sha256
            warm_candidate_table_count=$script:ExpectedWarmCandidateTableCount
            warm_active_candidate_table_count=$script:ExpectedWarmActiveCandidateTableCount
            warm_candidate_rows=$accepted.recomputed_rows; warm_candidate_bytes=$accepted.recomputed_bytes
            warm_required_physical_bytes_with_copy_safety=$accepted.recomputed_physical_bytes
            migration_completed=$false
        }
        production_invariants=[ordered]@{
            exact_clean_main=$true; administrator_powershell=$true; raw_consumers_stopped=$true
            production_clickhouse_ready=$true; accepted_named_volume_mounted=$true; env_unchanged=$true
            e_backup_root_absent=$true; f_recovery_preserved=$true; f_recovery_bytes=[int64]$fInfo.Length
        }
        architecture=[ordered]@{
            dedicated_ordinary_wsl2_clickhouse_required = $true
            docker_desktop_external_mnt_wsl_bind_rejected_by_prior_spike = $true
            tooling_distro=$ToolingDistro; tooling_distro_registered=$toolingRegistered
            tooling_distro_wsl2=$toolingVersion2; tooling_findmnt_ready=$toolingFindmntReady
            docker_desktop_wsl_root=$script:DockerDesktopWslRoot
            proposed_warm_mount_name=$script:ProposedWarmMountName; proposed_filesystem='ext4'
        }
        proposed_provisioning=[ordered]@{
            vhdx_path=$script:ProposedWarmVhdxPath; path_exists=$false
            ext4_quota_bytes=[int64]$sizeModel.proposed_ext4_quota_bytes; vhdx_max_bytes=$proposedMax
            dynamic_vhdx_intent=$true; create_performed=$false; resize_performed=$false
            mount_performed=$false; detach_performed=$false; compact_performed=$false
            move_performed=$false; delete_performed=$false
        }
        capacity=[ordered]@{
            warm_payload_bytes=[int64]$sizeModel.payload_bytes; copy_safety_margin_percent=[double]$sizeModel.copy_safety_margin_percent
            copy_required_bytes=[int64]$sizeModel.copy_required_bytes
            filesystem_runtime_overhead_percent=[double]$sizeModel.filesystem_runtime_overhead_percent
            minimum_filesystem_runtime_overhead_bytes=[int64]$sizeModel.minimum_filesystem_runtime_overhead_bytes
            filesystem_runtime_overhead_bytes=[int64]$sizeModel.filesystem_runtime_overhead_bytes
            future_expansion_headroom_percent=[double]$sizeModel.future_expansion_headroom_percent
            future_expansion_headroom_bytes=[int64]$sizeModel.future_expansion_headroom_bytes
            proposed_vhdx_max_bytes_unrounded=[int64]$sizeModel.proposed_vhdx_max_bytes_unrounded
            proposed_vhdx_max_bytes=$proposedMax; e_total_bytes=$eTotal; e_free_bytes=$eFree
            recommended_30_percent_reserve_bytes=$recommendedReserve
            recommended_allocation_budget_bytes=$recommendedBudget
            recommended_margin_after_proposed_vhdx_max_bytes=$recommendedMarginAfterMax
            recommended_30_percent_admission=$recommendedAdmission
        }
        inventory=[ordered]@{
            inventory_roots=@($inventoryRoots | Sort-Object -Unique); e_vhdx_entries=@($eVhdxInventory)
            wsl_distros=@($distros); wsl_mounts=@($wslMounts); disk_image_states=@($diskImageStates)
            protected_vhdx_paths=@($script:ProtectedVhdxPaths)
        }
        blockers=@($blockers)
        constraints=[ordered]@{
            apply_surface_present=$false; resume_surface_present=$false
            vhdx_create_authorized=$false; vhdx_resize_authorized=$false; vhdx_mount_authorized=$false
            vhdx_detach_authorized=$false; vhdx_compact_authorized=$false; vhdx_move_authorized=$false
            vhdx_delete_authorized=$false; wsl_mutation_authorized=$false; clickhouse_mutation_authorized=$false
            cn_warm_move_authorized=$false; docker_restart_authorized=$false; docker_prune_authorized=$false
            accepted_volume_mutation_authorized=$false; raw_delete_authorized=$false
            cn_replay_authorized=$false; us_bulk_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_cn_warm_ext4_provisioning_preflight.json'
    $receipt | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    Assert-ExactMain 'exit'

    Write-Host '===== PRODUCTION CN WARM EXT4 PROVISIONING PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'provisioning_completed=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_DONE'
    if (-not $ready) { exit 4 }
    exit 0
}
catch {
    Write-Host "PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_FAILED: $($_.Exception.Message)"
    exit 2
}
finally { Pop-Location }
