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
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments,
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

function Resolve-ReparseTargetLexically([string]$LinkPath, [string]$RawTarget) {
    if ([string]::IsNullOrWhiteSpace($RawTarget)) { return '' }
    $target = $RawTarget.Trim()
    if ($target.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) { $target = $target.Substring(4) }
    if ($target.StartsWith('\??\', [System.StringComparison]::OrdinalIgnoreCase)) { $target = $target.Substring(4) }
    if ($target -match '^[A-Za-z]:[\\/]') { return Normalize-HostPath $target }
    if ([System.IO.Path]::IsPathRooted($target)) { return '' }
    $linkNormalized = Normalize-HostPath $LinkPath
    if (-not $linkNormalized) { return '' }
    $parent = [System.IO.Path]::GetDirectoryName($linkNormalized)
    if (-not $parent) { return '' }
    try { return Normalize-HostPath ([System.IO.Path]::GetFullPath([System.IO.Path]::Combine($parent, $target))) }
    catch { return '' }
}

function Ensure-NativeReparseType {
    if ('MarkOrbit.NativeReparsePoint' -as [type]) { return }
    $source = @'
using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace MarkOrbit {
    public sealed class ReparseInfo {
        public uint Tag;
        public string TagHex;
        public string Kind;
        public string Target;
        public string SubstituteName;
        public string PrintName;
        public uint LxVersion;
        public int Win32Error;
        public string Error;
        public int BytesReturned;
    }

    public static class NativeReparsePoint {
        const uint FSCTL_GET_REPARSE_POINT = 0x000900A8;
        const uint OPEN_EXISTING = 3;
        const uint FILE_SHARE_READ = 1;
        const uint FILE_SHARE_WRITE = 2;
        const uint FILE_SHARE_DELETE = 4;
        const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
        const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        const uint IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003;
        const uint IO_REPARSE_TAG_SYMLINK = 0xA000000C;
        const uint IO_REPARSE_TAG_LX_SYMLINK = 0xA000001D;

        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern SafeFileHandle CreateFileW(string fileName, uint desiredAccess, uint shareMode,
            IntPtr securityAttributes, uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool DeviceIoControl(SafeFileHandle hDevice, uint controlCode,
            IntPtr inBuffer, int inBufferSize, [Out] byte[] outBuffer, int outBufferSize,
            out int bytesReturned, IntPtr overlapped);

        static string UnicodeSlice(byte[] buffer, int start, int length, int bytesReturned) {
            if (length < 0 || start < 0 || start + length > bytesReturned || (length % 2) != 0) return null;
            return Encoding.Unicode.GetString(buffer, start, length).TrimEnd('\0');
        }

        public static ReparseInfo ParseBuffer(byte[] buffer, int bytesReturned) {
            var result = new ReparseInfo();
            result.BytesReturned = bytesReturned;
            if (buffer == null || bytesReturned < 8) { result.Error = "REPARSE_BUFFER_TOO_SHORT"; return result; }
            uint tag = BitConverter.ToUInt32(buffer, 0);
            int dataLength = BitConverter.ToUInt16(buffer, 4);
            result.Tag = tag;
            result.TagHex = "0x" + tag.ToString("X8");
            if (8 + dataLength > bytesReturned) { result.Error = "REPARSE_DATA_LENGTH_INVALID"; return result; }

            if (tag == IO_REPARSE_TAG_SYMLINK) {
                result.Kind = "SYMLINK";
                if (dataLength < 12 || bytesReturned < 20) { result.Error = "SYMLINK_BUFFER_TOO_SHORT"; return result; }
                int subOffset = BitConverter.ToUInt16(buffer, 8);
                int subLength = BitConverter.ToUInt16(buffer, 10);
                int printOffset = BitConverter.ToUInt16(buffer, 12);
                int printLength = BitConverter.ToUInt16(buffer, 14);
                int pathBase = 20;
                result.SubstituteName = UnicodeSlice(buffer, pathBase + subOffset, subLength, bytesReturned);
                result.PrintName = UnicodeSlice(buffer, pathBase + printOffset, printLength, bytesReturned);
                result.Target = !String.IsNullOrWhiteSpace(result.PrintName) ? result.PrintName : result.SubstituteName;
                if (String.IsNullOrWhiteSpace(result.Target)) result.Error = "SYMLINK_TARGET_EMPTY";
                return result;
            }

            if (tag == IO_REPARSE_TAG_MOUNT_POINT) {
                result.Kind = "MOUNT_POINT";
                if (dataLength < 8 || bytesReturned < 16) { result.Error = "MOUNT_POINT_BUFFER_TOO_SHORT"; return result; }
                int subOffset = BitConverter.ToUInt16(buffer, 8);
                int subLength = BitConverter.ToUInt16(buffer, 10);
                int printOffset = BitConverter.ToUInt16(buffer, 12);
                int printLength = BitConverter.ToUInt16(buffer, 14);
                int pathBase = 16;
                result.SubstituteName = UnicodeSlice(buffer, pathBase + subOffset, subLength, bytesReturned);
                result.PrintName = UnicodeSlice(buffer, pathBase + printOffset, printLength, bytesReturned);
                result.Target = !String.IsNullOrWhiteSpace(result.PrintName) ? result.PrintName : result.SubstituteName;
                if (String.IsNullOrWhiteSpace(result.Target)) result.Error = "MOUNT_POINT_TARGET_EMPTY";
                return result;
            }

            if (tag == IO_REPARSE_TAG_LX_SYMLINK) {
                result.Kind = "LX_SYMLINK";
                if (dataLength < 5 || bytesReturned < 13) { result.Error = "LX_SYMLINK_BUFFER_TOO_SHORT"; return result; }
                result.LxVersion = BitConverter.ToUInt32(buffer, 8);
                int targetLength = dataLength - 4;
                string target = Encoding.UTF8.GetString(buffer, 12, targetLength).TrimEnd('\0');
                result.Target = target;
                if (String.IsNullOrWhiteSpace(result.Target)) result.Error = "LX_SYMLINK_TARGET_EMPTY";
                return result;
            }

            result.Kind = "UNKNOWN";
            result.Error = "UNSUPPORTED_REPARSE_TAG";
            return result;
        }

        public static ReparseInfo Query(string path) {
            using (SafeFileHandle handle = CreateFileW(path, 0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, IntPtr.Zero,
                OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero)) {
                if (handle.IsInvalid) return new ReparseInfo { Win32Error = Marshal.GetLastWin32Error(), Error = "CREATE_FILE_FAILED" };
                byte[] buffer = new byte[16384];
                int bytesReturned;
                if (!DeviceIoControl(handle, FSCTL_GET_REPARSE_POINT, IntPtr.Zero, 0,
                    buffer, buffer.Length, out bytesReturned, IntPtr.Zero)) {
                    return new ReparseInfo { Win32Error = Marshal.GetLastWin32Error(), Error = "FSCTL_GET_REPARSE_POINT_FAILED" };
                }
                return ParseBuffer(buffer, bytesReturned);
            }
        }
    }
}
'@
    Add-Type -TypeDefinition $source -Language CSharp
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

function Get-NativeReparseEntry([string]$CandidateRoot, [string]$EntryPath) {
    $candidate = Normalize-HostPath $CandidateRoot
    $entry = Normalize-HostPath $EntryPath
    if (-not $candidate -or -not $entry) { throw 'Unable to normalize native reparse path.' }
    $native = [MarkOrbit.NativeReparsePoint]::Query($entry)
    $rawTarget = if ([string]::IsNullOrWhiteSpace($native.Target)) { '' } else { [string]$native.Target }
    $lexical = if ($rawTarget) { Resolve-ReparseTargetLexically $entry $rawTarget } else { '' }
    $exists = [bool]($lexical -and (Test-Path -LiteralPath $lexical))
    return [ordered]@{
        candidate_root=$candidate
        path=$entry
        native_tag=[string]$native.TagHex
        native_kind=[string]$native.Kind
        native_error=[string]$native.Error
        native_win32_error=[int]$native.Win32Error
        native_bytes_returned=[int]$native.BytesReturned
        lx_version=[uint32]$native.LxVersion
        raw_target=$rawTarget
        substitute_name=[string]$native.SubstituteName
        print_name=[string]$native.PrintName
        lexical_target=$lexical
        target_exists=$exists
        lexical_target_inside_candidate_root=[bool]($lexical -and (Test-PathContains $candidate $lexical))
        dangling=[bool]($lexical -and -not $exists)
        target_unresolved=[bool](-not $lexical -or -not [string]::IsNullOrWhiteSpace($native.Error))
    }
}

function Get-NativeReparseInventory([string]$Root) {
    $normalized = Normalize-HostPath $Root
    $result = [ordered]@{ root=$normalized; exists=$false; reparse_points=@(); reparse_point_count=0; enumeration_complete=$true; enumeration_error=$null }
    if (-not $normalized -or -not (Test-Path -LiteralPath $normalized)) { return $result }
    $result.exists = $true
    try {
        $found = @()
        $rootAttr = [System.IO.File]::GetAttributes($normalized)
        if (($rootAttr -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            $found += Get-NativeReparseEntry $normalized $normalized
        } elseif (($rootAttr -band [System.IO.FileAttributes]::Directory) -ne 0) {
            $stack = New-Object 'System.Collections.Generic.Stack[string]'
            $stack.Push($normalized)
            while ($stack.Count -gt 0) {
                $directory = $stack.Pop()
                foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($directory)) {
                    $attributes = [System.IO.File]::GetAttributes($entry)
                    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                        $found += Get-NativeReparseEntry $normalized $entry
                        continue
                    }
                    if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { $stack.Push($entry) }
                }
            }
        }
        $result.reparse_points = @($found)
        $result.reparse_point_count = @($found).Count
    }
    catch { $result.enumeration_complete=$false; $result.enumeration_error=$_.Exception.Message }
    return $result
}

function Get-NativeClassification([object[]]$Inventories) {
    $points = @()
    foreach ($inventory in $Inventories) {
        if (-not [bool]$inventory.enumeration_complete) { return 'REBALANCE_E_NATIVE_REPARSE_ENUMERATION_INCOMPLETE' }
        $points += @($inventory.reparse_points)
    }
    if ($points.Count -eq 0) { return 'REBALANCE_E_NATIVE_REPARSE_NONE' }
    if (@($points | Where-Object { [bool]$_.target_unresolved }).Count -gt 0) { return 'REBALANCE_E_NATIVE_REPARSE_UNRESOLVED' }
    if (@($points | Where-Object { [bool]$_.dangling }).Count -gt 0) { return 'REBALANCE_E_NATIVE_REPARSE_DANGLING' }
    if (@($points | Where-Object { -not [bool]$_.lexical_target_inside_candidate_root }).Count -gt 0) { return 'REBALANCE_E_NATIVE_REPARSE_ESCAPES_DELETION_ROOT' }
    return 'REBALANCE_E_NATIVE_REPARSE_INTERNAL_LEXICAL_TARGETS'
}

try {
    Write-Host '===== PRODUCTION REBALANCE E NATIVE REPARSE PROVENANCE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Native E reparse provenance must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Native E reparse provenance requires elevated Administrator PowerShell.' }

    $hot = Normalize-HostPath $LegacyEHotRoot
    $logs = Normalize-HostPath $LegacyEHotLogsRoot
    if (-not $hot.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot must remain exact.' }
    if (-not $logs.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot must remain exact.' }

    Ensure-NativeReparseType
    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    Assert-RawConsumersStopped
    $before = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$before.ready)"
    Write-Host "production_clickhouse_health_before=$($before.health)"
    if (-not [bool]$before.ready) { throw 'Production ClickHouse must be healthy before native reparse provenance.' }
    Assert-AcceptedProductionMount $before.container_id

    Write-Host 'native_reparse_stage=non_traversing_inventory'
    $hotInventory = Get-NativeReparseInventory $hot
    $logsInventory = Get-NativeReparseInventory $logs
    foreach ($inventory in @($hotInventory,$logsInventory)) {
        Write-Host "reparse_root=$($inventory.root)"
        Write-Host "reparse_root_exists=$([bool]$inventory.exists)"
        Write-Host "reparse_point_count=$([int64]$inventory.reparse_point_count)"
        foreach ($point in @($inventory.reparse_points)) {
            Write-Host "reparse_path=$($point.path)"
            Write-Host "reparse_native_tag=$($point.native_tag)"
            Write-Host "reparse_native_kind=$($point.native_kind)"
            Write-Host "reparse_native_error=$($point.native_error)"
            Write-Host "reparse_native_win32_error=$([int]$point.native_win32_error)"
            Write-Host "reparse_raw_target=$($point.raw_target)"
            Write-Host "reparse_lexical_target=$($point.lexical_target)"
            Write-Host "reparse_target_exists=$([bool]$point.target_exists)"
            Write-Host "reparse_target_inside_candidate_root=$([bool]$point.lexical_target_inside_candidate_root)"
            Write-Host "reparse_dangling=$([bool]$point.dangling)"
            Write-Host "reparse_target_unresolved=$([bool]$point.target_unresolved)"
        }
    }

    $decision = Get-NativeClassification @($hotInventory,$logsInventory)
    $nextGate = if ($decision -eq 'REBALANCE_E_NATIVE_REPARSE_INTERNAL_LEXICAL_TARGETS') { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_DESIGN' }
        elseif ($decision -eq 'REBALANCE_E_NATIVE_REPARSE_NONE') { 'PRODUCTION_REBALANCE_PHASE1_E_DRY_RUN_RETRY' }
        else { 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_REVIEW_REQUIRED' }

    $after = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_after=$([bool]$after.ready)"
    Write-Host "production_clickhouse_health_after=$($after.health)"
    if (-not [bool]$after.ready) { throw 'Production ClickHouse must remain healthy after native reparse provenance.' }
    Assert-AcceptedProductionMount $after.container_id
    Assert-RawConsumersStopped
    $envHashAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during native read-only provenance.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_e_native_reparse_provenance_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version='PRODUCTION_REBALANCE_E_NATIVE_REPARSE_PROVENANCE_V2'
        decision=$decision
        next_gate=$nextGate
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        read_only=$true
        parser_locale_independent=$true
        non_traversing_inventory=$true
        legacy_e_hot=$hotInventory
        legacy_e_logs=$logsInventory
        production=[ordered]@{ clickhouse_ready_before=[bool]$before.ready; clickhouse_ready_after=[bool]$after.ready; accepted_volume=$AcceptedVolume; running_raw_consumer_count=0 }
        constraints=[ordered]@{ phase1_delete_authorized=$false; reparse_delete_authorized=$false; legacy_e_hot_delete_authorized=$false; accepted_volume_delete_authorized=$false; vhdx_create_authorized=$false; wsl_shutdown_authorized=$false; wsl_unmount_authorized=$false; clickhouse_mutation_authorized=$false; cn_replay_authorized=$false; us_package_2_authorized=$false; us_bulk_authorized=$false; mutation_performed=$false }
        env_unchanged=$envUnchanged
    }
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $evidenceDir 'production_rebalance_e_native_reparse_provenance.json') -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE E NATIVE REPARSE PROVENANCE RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "hot_reparse_point_count=$([int64]$hotInventory.reparse_point_count)"
    Write-Host "logs_reparse_point_count=$([int64]$logsInventory.reparse_point_count)"
    Write-Host 'phase1_delete_authorized=False'
    Write-Host 'reparse_delete_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host "production_invariant_preserved=$([bool]($before.ready -and $after.ready))"
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_E_NATIVE_REPARSE_PROVENANCE_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
