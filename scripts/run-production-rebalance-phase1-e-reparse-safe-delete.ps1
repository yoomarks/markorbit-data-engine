[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [int]$ExpectedReparsePointCount = 63,
    [string]$EvidenceRoot = 'reports',
    [switch]$AcknowledgeLegacyEDuplicateDelete,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
$script:LxSymlinkReparseTag = [Convert]::ToUInt32('A000001D', 16)

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
    $compose = @($ComposeBinds | Where-Object { $_.normalized_source -and (Test-PathsOverlap $CandidatePath $_.normalized_source) })
    return [ordered]@{ all_container_reference_count=[int64]$allContainer.Count; compose_reference_count=[int64]$compose.Count }
}

function Get-DriveSnapshot([string]$Letter) {
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { throw "Required drive missing: $root" }
    $drive = New-Object System.IO.DriveInfo($root)
    return [ordered]@{ drive="${Letter}:"; total_bytes=[int64]$drive.TotalSize; free_bytes=[int64]$drive.AvailableFreeSpace; filesystem=[string]$drive.DriveFormat }
}

function Convert-RelativePathToBase64([string]$RelativePath) {
    return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($RelativePath))
}

function Convert-Base64ToRelativePath([string]$Base64) {
    return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Base64))
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
        root=$normalized; exists=$false; root_is_directory=$false; root_is_reparse_point=$false
        enumeration_complete=$true; enumeration_error=$null
        regular_file_count=[int64]0; regular_file_bytes=[int64]0; regular_directory_count=[int64]0
        reparse_point_count=[int64]0; manifest_entry_count=[int64]0; max_relative_depth=[int64]0
        reparse_paths=@(); manifest_path=$ManifestPath; manifest_sha256=$null; non_traversing=$true
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
    finally { $writer.Dispose() }
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) {
        $result.manifest_sha256 = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $result
}

function Read-DeletionManifest([string]$ManifestPath, [string]$Root) {
    $normalizedRoot = Normalize-HostPath $Root
    if (-not $normalizedRoot) { throw 'Unable to normalize manifest root.' }
    $entries = @()
    foreach ($line in @(Get-Content -LiteralPath $ManifestPath -Encoding UTF8)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $parts = @($line -split "`t", 4)
        if ($parts.Count -ne 4) { throw "Invalid deletion manifest line: $line" }
        $kind = [string]$parts[0]
        if ($kind -notin @('F','D','R')) { throw "Unsupported manifest kind: $kind" }
        $relative = Convert-Base64ToRelativePath ([string]$parts[3])
        if ([string]::IsNullOrWhiteSpace($relative)) { throw 'Manifest contains an empty relative path.' }
        $fullPath = [System.IO.Path]::GetFullPath((Join-Path $normalizedRoot $relative))
        if (-not (Test-PathContains $normalizedRoot $fullPath) -or $fullPath.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Manifest path escaped approved root: $relative"
        }
        $entries += [pscustomobject]@{
            kind=$kind
            length=[int64]$parts[1]
            attributes=[int64]$parts[2]
            relative_path=$relative
            full_path=$fullPath
            depth=[int64](Get-RelativeDepth $relative)
        }
    }
    return @($entries)
}

function Ensure-NativeSafeDeleteType {
    if ('MarkOrbit.NativeSafeDelete' -as [type]) { return }
    $source = @'
using System;
using System.IO;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace MarkOrbit {
    public sealed class ReparseIdentity {
        public uint Tag;
        public uint LxVersion;
        public bool IsDirectory;
        public int Win32Error;
        public string Error;
    }

    public static class NativeSafeDelete {
        const uint FSCTL_GET_REPARSE_POINT = 0x000900A8;
        const uint OPEN_EXISTING = 3;
        const uint FILE_SHARE_READ = 1;
        const uint FILE_SHARE_WRITE = 2;
        const uint FILE_SHARE_DELETE = 4;
        const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
        const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        const uint FILE_ATTRIBUTE_DIRECTORY = 0x10;
        const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x400;
        const uint INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF;

        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern SafeFileHandle CreateFileW(string fileName, uint desiredAccess, uint shareMode,
            IntPtr securityAttributes, uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool DeviceIoControl(SafeFileHandle hDevice, uint controlCode,
            IntPtr inBuffer, int inBufferSize, [Out] byte[] outBuffer, int outBufferSize,
            out int bytesReturned, IntPtr overlapped);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern uint GetFileAttributesW(string lpFileName);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern bool DeleteFileW(string lpFileName);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern bool RemoveDirectoryW(string lpPathName);

        public static bool ExistsNoFollow(string path) {
            return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES;
        }

        public static ReparseIdentity Query(string path) {
            uint attrs = GetFileAttributesW(path);
            if (attrs == INVALID_FILE_ATTRIBUTES) return new ReparseIdentity { Win32Error = Marshal.GetLastWin32Error(), Error = "GET_ATTRIBUTES_FAILED" };
            bool isDirectory = (attrs & FILE_ATTRIBUTE_DIRECTORY) != 0;
            if ((attrs & FILE_ATTRIBUTE_REPARSE_POINT) == 0) return new ReparseIdentity { IsDirectory=isDirectory, Error="NOT_REPARSE_POINT" };
            using (SafeFileHandle handle = CreateFileW(path, 0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, IntPtr.Zero,
                OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero)) {
                if (handle.IsInvalid) return new ReparseIdentity { IsDirectory=isDirectory, Win32Error=Marshal.GetLastWin32Error(), Error="CREATE_FILE_FAILED" };
                byte[] buffer = new byte[16384];
                int bytesReturned;
                if (!DeviceIoControl(handle, FSCTL_GET_REPARSE_POINT, IntPtr.Zero, 0, buffer, buffer.Length, out bytesReturned, IntPtr.Zero)) {
                    return new ReparseIdentity { IsDirectory=isDirectory, Win32Error=Marshal.GetLastWin32Error(), Error="FSCTL_GET_REPARSE_POINT_FAILED" };
                }
                if (bytesReturned < 8) return new ReparseIdentity { IsDirectory=isDirectory, Error="REPARSE_BUFFER_TOO_SHORT" };
                uint tag = BitConverter.ToUInt32(buffer, 0);
                uint version = 0;
                if (tag == 0xA000001D && bytesReturned >= 12) version = BitConverter.ToUInt32(buffer, 8);
                return new ReparseIdentity { Tag=tag, LxVersion=version, IsDirectory=isDirectory };
            }
        }

        public static void UnlinkChecked(string path, uint expectedTag, uint expectedLxVersion, bool requireLxVersion) {
            ReparseIdentity identity = Query(path);
            if (!String.IsNullOrEmpty(identity.Error)) throw new InvalidOperationException("Reparse query failed for " + path + ": " + identity.Error + " win32=" + identity.Win32Error);
            if (identity.Tag != expectedTag) throw new InvalidOperationException("Reparse tag changed for " + path + ": 0x" + identity.Tag.ToString("X8"));
            if (requireLxVersion && identity.LxVersion != expectedLxVersion) throw new InvalidOperationException("LX version changed for " + path + ": " + identity.LxVersion);
            bool ok = identity.IsDirectory ? RemoveDirectoryW(path) : DeleteFileW(path);
            if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error(), "Native no-follow unlink failed: " + path);
            if (ExistsNoFollow(path)) throw new InvalidOperationException("Reparse object still exists after unlink: " + path);
        }

        public static void DeleteNormalFileChecked(string path, long expectedLength) {
            uint attrs = GetFileAttributesW(path);
            if (attrs == INVALID_FILE_ATTRIBUTES) throw new Win32Exception(Marshal.GetLastWin32Error(), "File disappeared before delete: " + path);
            if ((attrs & FILE_ATTRIBUTE_REPARSE_POINT) != 0 || (attrs & FILE_ATTRIBUTE_DIRECTORY) != 0) throw new InvalidOperationException("Expected normal file changed type: " + path);
            long length = new FileInfo(path).Length;
            if (length != expectedLength) throw new InvalidOperationException("File length changed before delete: " + path);
            if (!DeleteFileW(path)) throw new Win32Exception(Marshal.GetLastWin32Error(), "Native file delete failed: " + path);
        }

        public static void DeleteNormalDirectoryChecked(string path) {
            uint attrs = GetFileAttributesW(path);
            if (attrs == INVALID_FILE_ATTRIBUTES) throw new Win32Exception(Marshal.GetLastWin32Error(), "Directory disappeared before delete: " + path);
            if ((attrs & FILE_ATTRIBUTE_REPARSE_POINT) != 0 || (attrs & FILE_ATTRIBUTE_DIRECTORY) == 0) throw new InvalidOperationException("Expected normal directory changed type: " + path);
            if (!RemoveDirectoryW(path)) throw new Win32Exception(Marshal.GetLastWin32Error(), "Native directory delete failed: " + path);
        }
    }
}
'@
    Add-Type -TypeDefinition $source -Language CSharp
}

function Assert-LxManifestEntries([object[]]$Entries) {
    Ensure-NativeSafeDeleteType
    $reparseEntries = @($Entries | Where-Object { $_.kind -eq 'R' })
    foreach ($entry in $reparseEntries) {
        $identity = [MarkOrbit.NativeSafeDelete]::Query([string]$entry.full_path)
        if (-not [string]::IsNullOrWhiteSpace([string]$identity.Error)) { throw "Native LX identity query failed for $($entry.relative_path): $($identity.Error)" }
        if ([uint32]$identity.Tag -ne $script:LxSymlinkReparseTag) { throw "Non-LX reparse object found at destructive boundary: $($entry.relative_path)" }
        if ([uint32]$identity.LxVersion -ne [uint32]2) { throw "Unsupported LX version at destructive boundary: $($entry.relative_path)" }
    }
    Write-Host "native_lx_reparse_verified_count=$($reparseEntries.Count)"
    return [int64]$reparseEntries.Count
}

function Invoke-FreshSafeDeletePreflight([string]$RunId) {
    $childRelativeRoot = Join-Path (Join-Path 'reports' '_eapply') (Join-Path $RunId 'preflight')
    $childAbsoluteRoot = Join-Path $repoRoot $childRelativeRoot
    New-Item -ItemType Directory -Force -Path $childAbsoluteRoot | Out-Null
    Write-Host 'safe_apply_stage=fresh_same_main_preflight'
    $scriptPath = Join-Path $PSScriptRoot 'preflight-production-rebalance-phase1-e-reparse-safe-delete.ps1'
    $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,'-ExpectedMainSha',$ExpectedMainSha,'-AcceptedVolume',$AcceptedVolume,'-LegacyEHotRoot',$LegacyEHotRoot,'-LegacyEHotLogsRoot',$LegacyEHotLogsRoot,'-ExpectedReparsePointCount',([string]$ExpectedReparsePointCount),'-EvidenceRoot',$childRelativeRoot)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& powershell.exe @childArgs 2>&1)
        $childExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    foreach ($line in @($outputLines | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($childExitCode -ne 0) { throw "Fresh same-main safe-delete preflight exited $childExitCode." }
    $dirs = @(Get-ChildItem -LiteralPath $childAbsoluteRoot -Directory -Filter 'production_rebalance_phase1_e_reparse_safe_preflight_*' | Sort-Object LastWriteTime -Descending)
    if ($dirs.Count -ne 1) { throw "Expected exactly one fresh safe-delete preflight receipt directory; observed $($dirs.Count)." }
    $receiptPath = Join-Path $dirs[0].FullName 'production_rebalance_phase1_e_reparse_safe_delete_preflight.json'
    $hotManifest = Join-Path $dirs[0].FullName 'phase1_e_hot_non_traversing_manifest.tsv'
    $logsManifest = Join-Path $dirs[0].FullName 'phase1_e_logs_non_traversing_manifest.tsv'
    foreach ($required in @($receiptPath,$hotManifest,$logsManifest)) { if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Fresh preflight evidence missing: $required" } }
    try { $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Fresh safe-delete preflight receipt is invalid JSON: $($_.Exception.Message)" }
    return [ordered]@{ receipt=$receipt; receipt_path=$receiptPath; hot_manifest=$hotManifest; logs_manifest=$logsManifest; evidence_dir=$dirs[0].FullName }
}

function Invoke-FreshCapacityInventory([string]$RunId) {
    $childRelativeRoot = Join-Path (Join-Path 'reports' '_eapply') (Join-Path $RunId 'capacity')
    $childAbsoluteRoot = Join-Path $repoRoot $childRelativeRoot
    New-Item -ItemType Directory -Force -Path $childAbsoluteRoot | Out-Null
    Write-Host 'safe_apply_stage=fresh_capacity_inventory'
    $scriptPath = Join-Path $PSScriptRoot 'profile-production-storage-rebalance-candidates.ps1'
    $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,'-ExpectedMainSha',$ExpectedMainSha,'-AcceptedVolume',$AcceptedVolume,'-LegacyEHotRoot',$LegacyEHotRoot,'-LegacyEHotLogsRoot',$LegacyEHotLogsRoot,'-EvidenceRoot',$childRelativeRoot)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputLines = @(& powershell.exe @childArgs 2>&1)
        $childExitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    foreach ($line in @($outputLines | ForEach-Object { $_.ToString() })) { Write-Host $line }
    if ($childExitCode -ne 0) { throw "Fresh capacity inventory exited $childExitCode." }
    $dirs = @(Get-ChildItem -LiteralPath $childAbsoluteRoot -Directory -Filter 'production_storage_rebalance_inventory_*' | Sort-Object LastWriteTime -Descending)
    if ($dirs.Count -ne 1) { throw "Expected exactly one fresh capacity inventory directory; observed $($dirs.Count)." }
    $receiptPath = Join-Path $dirs[0].FullName 'production_storage_rebalance_candidate_inventory.json'
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'Fresh capacity inventory receipt is missing.' }
    try { $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Fresh capacity inventory receipt is invalid JSON: $($_.Exception.Message)" }
    return [ordered]@{ receipt=$receipt; receipt_path=$receiptPath }
}

function Write-Journal([string]$Path, [hashtable]$Journal) {
    $Journal.updated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    $Journal | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Remove-TreeFromManifest {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][object[]]$Entries,
        [Parameter(Mandatory = $true)][string]$JournalPath,
        [Parameter(Mandatory = $true)][hashtable]$Journal,
        [Parameter(Mandatory = $true)][string]$TreeName
    )
    Ensure-NativeSafeDeleteType
    $reparseEntries = @($Entries | Where-Object { $_.kind -eq 'R' } | Sort-Object depth -Descending)
    $fileEntries = @($Entries | Where-Object { $_.kind -eq 'F' } | Sort-Object depth -Descending)
    $directoryEntries = @($Entries | Where-Object { $_.kind -eq 'D' } | Sort-Object depth -Descending)

    $Journal.phase = "${TreeName}_unlink_reparse"
    Write-Journal $JournalPath $Journal
    $count = 0
    foreach ($entry in $reparseEntries) {
        [MarkOrbit.NativeSafeDelete]::UnlinkChecked([string]$entry.full_path, $script:LxSymlinkReparseTag, [uint32]2, $true)
        $count++
        if (($count % 25) -eq 0 -or $count -eq $reparseEntries.Count) { Write-Host "${TreeName}_reparse_unlink_progress=$count/$($reparseEntries.Count)" }
    }
    $Journal["${TreeName}_reparse_unlinked"] = [int64]$count
    Write-Journal $JournalPath $Journal

    $Journal.phase = "${TreeName}_delete_files"
    Write-Journal $JournalPath $Journal
    $count = 0
    foreach ($entry in $fileEntries) {
        [MarkOrbit.NativeSafeDelete]::DeleteNormalFileChecked([string]$entry.full_path, [int64]$entry.length)
        $count++
        if (($count % 10000) -eq 0 -or $count -eq $fileEntries.Count) { Write-Host "${TreeName}_file_delete_progress=$count/$($fileEntries.Count)" }
    }
    $Journal["${TreeName}_files_deleted"] = [int64]$count
    Write-Journal $JournalPath $Journal

    $Journal.phase = "${TreeName}_delete_directories"
    Write-Journal $JournalPath $Journal
    $count = 0
    foreach ($entry in $directoryEntries) {
        [MarkOrbit.NativeSafeDelete]::DeleteNormalDirectoryChecked([string]$entry.full_path)
        $count++
        if (($count % 1000) -eq 0 -or $count -eq $directoryEntries.Count) { Write-Host "${TreeName}_directory_delete_progress=$count/$($directoryEntries.Count)" }
    }
    $Journal["${TreeName}_directories_deleted"] = [int64]$count
    Write-Journal $JournalPath $Journal

    $Journal.phase = "${TreeName}_delete_root"
    Write-Journal $JournalPath $Journal
    [MarkOrbit.NativeSafeDelete]::DeleteNormalDirectoryChecked((Normalize-HostPath $Root))
    if ([MarkOrbit.NativeSafeDelete]::ExistsNoFollow((Normalize-HostPath $Root))) { throw "${TreeName} root remains after native delete." }
    $Journal["${TreeName}_root_deleted"] = $true
    Write-Journal $JournalPath $Journal
}

try {
    Write-Host '===== PRODUCTION REBALANCE PHASE1 E REPARSE SAFE DELETE APPLY ====='
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "legacy_e_duplicate_delete_acknowledged=$([bool]$AcknowledgeLegacyEDuplicateDelete)"
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase1E safe-delete apply must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase1E safe-delete apply requires elevated Administrator PowerShell.' }
    if ($Apply -and -not $AcknowledgeLegacyEDuplicateDelete) { throw '-Apply requires explicit -AcknowledgeLegacyEDuplicateDelete.' }

    $hot = Normalize-HostPath $LegacyEHotRoot
    $logs = Normalize-HostPath $LegacyEHotLogsRoot
    if (-not $hot.Equals('E:\MarkOrbitData\hot\clickhouse', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotRoot must remain the exact approved legacy E ClickHouse root.' }
    if (-not $logs.Equals('E:\MarkOrbitData\hot\clickhouse-logs', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'LegacyEHotLogsRoot must remain the exact approved legacy E ClickHouse log root.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $runId = "${timestamp}_$PID"
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_rebalance_phase1_e_reparse_safe_delete_apply_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $envPath = Join-Path $repoRoot '.env'
    $envHashBefore = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }

    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_before=$([bool]$productionBefore.ready)"
    Write-Host "production_clickhouse_health_before=$($productionBefore.health)"
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before safe-delete apply.' }
    Assert-AcceptedProductionMount $productionBefore.container_id

    $preflightResult = Invoke-FreshSafeDeletePreflight $runId
    $preflight = $preflightResult.receipt
    Write-Host "accepted_same_main_preflight_receipt=$($preflightResult.receipt_path)"
    if ([string]$preflight.receipt_version -ne 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_V1') { throw 'Unexpected safe-delete preflight receipt version.' }
    if ([string]$preflight.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Safe-delete preflight receipt SHA mismatch.' }
    if (-not [bool]$preflight.read_only -or -not [bool]$preflight.ready_for_safe_delete_apply_design) { throw 'Safe-delete preflight did not preserve accepted read-only readiness.' }
    if ([string]$preflight.decision -ne 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_PREFLIGHT_READY') { throw "Safe-delete preflight not ready: $($preflight.decision)" }
    if ([string]$preflight.next_gate -ne 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY_DESIGN') { throw 'Safe-delete preflight next gate mismatch.' }

    $acceptedHotManifestSha = (Get-FileHash -LiteralPath $preflightResult.hot_manifest -Algorithm SHA256).Hash.ToLowerInvariant()
    $acceptedLogsManifestSha = (Get-FileHash -LiteralPath $preflightResult.logs_manifest -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "accepted_hot_manifest_sha256=$acceptedHotManifestSha"
    Write-Host "accepted_logs_manifest_sha256=$acceptedLogsManifestSha"

    $capacityResult = Invoke-FreshCapacityInventory $runId
    $capacity = $capacityResult.receipt
    Write-Host "fresh_capacity_receipt=$($capacityResult.receipt_path)"
    if ([string]$capacity.receipt_version -ne 'PRODUCTION_STORAGE_REBALANCE_CANDIDATE_INVENTORY_V1') { throw 'Unexpected capacity inventory receipt version.' }
    if (-not [bool]$capacity.read_only -or -not [bool]$capacity.production_invariant_preserved -or -not [bool]$capacity.env_unchanged) { throw 'Fresh capacity inventory did not preserve production invariants.' }
    if (-not [bool]$capacity.production.accepted_production_mount_ready -or [int64]$capacity.production.worker_container_count -ne 0) { throw 'Fresh capacity inventory lost the accepted production boundary.' }

    Write-Host 'safe_apply_stage=destructive_boundary_manifest_regeneration'
    $boundaryHotManifest = Join-Path $evidenceDir 'boundary_hot_non_traversing_manifest.tsv'
    $boundaryLogsManifest = Join-Path $evidenceDir 'boundary_logs_non_traversing_manifest.tsv'
    $hotInventory = Get-NonTraversingDeletionInventory -Root $hot -ManifestPath $boundaryHotManifest
    $logsInventory = Get-NonTraversingDeletionInventory -Root $logs -ManifestPath $boundaryLogsManifest
    if (-not [bool]$hotInventory.enumeration_complete -or -not [bool]$logsInventory.enumeration_complete) { throw 'Boundary non-following inventory is incomplete.' }
    if ([bool]$hotInventory.root_is_reparse_point -or [bool]$logsInventory.root_is_reparse_point) { throw 'A legacy E root became a reparse point.' }
    if (-not [bool]$hotInventory.exists -or -not [bool]$logsInventory.exists) { throw 'A required legacy E root is missing before apply.' }
    if ([int64]$hotInventory.reparse_point_count -ne [int64]$ExpectedReparsePointCount -or [int64]$logsInventory.reparse_point_count -ne 0) { throw 'Boundary reparse count changed.' }
    if ([string]$hotInventory.manifest_sha256 -ne $acceptedHotManifestSha -or [string]$logsInventory.manifest_sha256 -ne $acceptedLogsManifestSha) { throw 'Boundary non-following manifest SHA changed after accepted same-main preflight.' }
    Write-Host "boundary_hot_manifest_sha256=$($hotInventory.manifest_sha256)"
    Write-Host "boundary_logs_manifest_sha256=$($logsInventory.manifest_sha256)"
    Write-Host 'boundary_manifest_match=True'

    $hotEntries = @(Read-DeletionManifest $boundaryHotManifest $hot)
    $logsEntries = @(Read-DeletionManifest $boundaryLogsManifest $logs)
    $nativeVerified = Assert-LxManifestEntries $hotEntries
    if ($nativeVerified -ne $ExpectedReparsePointCount) { throw 'Native LX verification count changed.' }
    if (@($logsEntries | Where-Object { $_.kind -eq 'R' }).Count -ne 0) { throw 'Unexpected Logs reparse object found.' }
    $readOnlyFileCount = @($hotEntries + $logsEntries | Where-Object { $_.kind -eq 'F' -and (([int64]$_.attributes -band [int64][System.IO.FileAttributes]::ReadOnly) -ne 0) }).Count
    Write-Host "read_only_regular_file_count=$readOnlyFileCount"
    if ($readOnlyFileCount -ne 0) { throw 'Read-only regular files would make native delete non-deterministic; review required.' }

    Assert-RawConsumersStopped
    $containerMounts = @(Get-AllContainerMounts)
    $composeBinds = @(Get-ComposeBindMounts)
    $hotRefs = Get-PathReferences $hot $containerMounts $composeBinds
    $logsRefs = Get-PathReferences $logs $containerMounts $composeBinds
    Write-Host "boundary_hot_container_reference_count=$($hotRefs.all_container_reference_count)"
    Write-Host "boundary_hot_compose_reference_count=$($hotRefs.compose_reference_count)"
    Write-Host "boundary_logs_container_reference_count=$($logsRefs.all_container_reference_count)"
    Write-Host "boundary_logs_compose_reference_count=$($logsRefs.compose_reference_count)"
    if ($hotRefs.all_container_reference_count -ne 0 -or $hotRefs.compose_reference_count -ne 0 -or $logsRefs.all_container_reference_count -ne 0 -or $logsRefs.compose_reference_count -ne 0) { throw 'Legacy E roots gained Docker/Compose references at destructive boundary.' }
    $productionBoundary = Get-ProductionClickHouseHealth
    if (-not [bool]$productionBoundary.ready) { throw 'Production ClickHouse lost health at destructive boundary.' }
    Assert-AcceptedProductionMount $productionBoundary.container_id
    Assert-ExactMain 'destructive_boundary'

    $driveEBefore = Get-DriveSnapshot 'E'
    $requiredERecommendedFree = [int64]([int64]$capacity.drives.E.free_bytes + [int64]$capacity.deficits.e_additional_free_recommended_bytes)
    $logicalReclaimBytes = [int64]([int64]$hotInventory.regular_file_bytes + [int64]$logsInventory.regular_file_bytes)
    $projectedEFree = [int64]([int64]$driveEBefore.free_bytes + $logicalReclaimBytes)
    Write-Host "e_free_before_bytes=$($driveEBefore.free_bytes)"
    Write-Host "e_required_recommended_free_bytes=$requiredERecommendedFree"
    Write-Host "e_non_following_regular_file_bytes=$logicalReclaimBytes"
    Write-Host "e_projected_free_after_logical_bytes=$projectedEFree"
    if ($projectedEFree -lt $requiredERecommendedFree) { throw 'Non-following logical reclaim no longer covers the E recommended free-space target.' }

    $journalPath = Join-Path $evidenceDir 'phase1_e_safe_delete_journal.json'
    $journal = @{
        journal_version='PRODUCTION_REBALANCE_PHASE1_E_SAFE_DELETE_JOURNAL_V1'
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        state='PREPARED'
        phase='pre_mutation'
        mutation_started=$false
        apply_requested=[bool]$Apply
        hot_manifest_sha256=$acceptedHotManifestSha
        logs_manifest_sha256=$acceptedLogsManifestSha
        hot_reparse_unlinked=[int64]0
        hot_files_deleted=[int64]0
        hot_directories_deleted=[int64]0
        hot_root_deleted=$false
        logs_reparse_unlinked=[int64]0
        logs_files_deleted=[int64]0
        logs_directories_deleted=[int64]0
        logs_root_deleted=$false
        failure=$null
        updated_at_utc=$null
    }
    Write-Journal $journalPath $journal

    if (-not $Apply) {
        $decision='PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_READY_FOR_APPLY'
        $nextGate='PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY'
        $applyAccepted=$false
        $mutationPerformed=$false
        $driveEAfter=$driveEBefore
    }
    else {
        $journal.state='MUTATING'
        $journal.mutation_started=$true
        Write-Journal $journalPath $journal
        try {
            Write-Host 'safe_apply_stage=native_no_follow_delete_hot'
            Remove-TreeFromManifest -Root $hot -Entries $hotEntries -JournalPath $journalPath -Journal $journal -TreeName 'hot'
            Write-Host 'safe_apply_stage=native_no_follow_delete_logs'
            Remove-TreeFromManifest -Root $logs -Entries $logsEntries -JournalPath $journalPath -Journal $journal -TreeName 'logs'
            $journal.state='MUTATION_COMPLETE'
            $journal.phase='post_delete_verification'
            Write-Journal $journalPath $journal
        }
        catch {
            $journal.state='PARTIAL_FAILURE'
            $journal.failure=$_.Exception.Message
            Write-Journal $journalPath $journal
            throw "Phase1E native no-follow delete entered partial-failure state. Journal: ${journalPath}. Error: $($_.Exception.Message)"
        }

        if ([MarkOrbit.NativeSafeDelete]::ExistsNoFollow($hot) -or [MarkOrbit.NativeSafeDelete]::ExistsNoFollow($logs)) { throw 'Legacy E root remains after safe delete.' }
        $driveEAfter = Get-DriveSnapshot 'E'
        Write-Host "e_free_after_bytes=$($driveEAfter.free_bytes)"
        if ([int64]$driveEAfter.free_bytes -lt $requiredERecommendedFree) { throw "E free space is below the required recommended floor after Phase1E: $($driveEAfter.free_bytes) < $requiredERecommendedFree" }
        Assert-RawConsumersStopped
        $productionAfterDelete = Get-ProductionClickHouseHealth
        if (-not [bool]$productionAfterDelete.ready) { throw 'Production ClickHouse lost health after Phase1E safe delete.' }
        Assert-AcceptedProductionMount $productionAfterDelete.container_id
        $decision='PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_GO'
        $nextGate='PRODUCTION_REBALANCE_PHASE2_D_FULL_SHA256_APPLY'
        $applyAccepted=$true
        $mutationPerformed=$true
        $journal.state='GO'
        $journal.phase='complete'
        Write-Journal $journalPath $journal
    }

    $envHashAfter = if (Test-Path -LiteralPath $envPath -PathType Leaf) { (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash } else { $null }
    $envUnchanged = [bool]($envHashBefore -eq $envHashAfter)
    if (-not $envUnchanged) { throw '.env changed during Phase1E safe-delete apply.' }
    $productionFinal = Get-ProductionClickHouseHealth
    if (-not [bool]$productionFinal.ready) { throw 'Production ClickHouse must remain healthy at final verification.' }
    Assert-AcceptedProductionMount $productionFinal.container_id
    Assert-RawConsumersStopped

    $receipt = [ordered]@{
        receipt_version='PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY_V1'
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        apply_requested=[bool]$Apply
        delete_acknowledged=[bool]$AcknowledgeLegacyEDuplicateDelete
        apply_accepted=$applyAccepted
        mutation_performed=$mutationPerformed
        accepted_same_main_preflight_receipt=$preflightResult.receipt_path
        accepted_hot_manifest_sha256=$acceptedHotManifestSha
        accepted_logs_manifest_sha256=$acceptedLogsManifestSha
        boundary_hot_manifest_sha256=[string]$hotInventory.manifest_sha256
        boundary_logs_manifest_sha256=[string]$logsInventory.manifest_sha256
        boundary_manifest_match=$true
        native_lx_required_tag='0xA000001D'
        native_lx_required_version=2
        native_lx_verified_count=[int64]$nativeVerified
        physical=[ordered]@{
            hot_regular_file_count=[int64]$hotInventory.regular_file_count
            hot_regular_file_bytes=[int64]$hotInventory.regular_file_bytes
            hot_regular_directory_count=[int64]$hotInventory.regular_directory_count
            hot_reparse_point_count=[int64]$hotInventory.reparse_point_count
            logs_regular_file_count=[int64]$logsInventory.regular_file_count
            logs_regular_file_bytes=[int64]$logsInventory.regular_file_bytes
            logs_regular_directory_count=[int64]$logsInventory.regular_directory_count
            logs_reparse_point_count=[int64]$logsInventory.reparse_point_count
            logical_reclaim_bytes=$logicalReclaimBytes
        }
        capacity=[ordered]@{
            fresh_capacity_receipt=$capacityResult.receipt_path
            e_total_bytes=[int64]$driveEBefore.total_bytes
            e_free_before_bytes=[int64]$driveEBefore.free_bytes
            e_free_after_bytes=[int64]$driveEAfter.free_bytes
            e_required_recommended_free_bytes=$requiredERecommendedFree
            projected_free_after_logical_bytes=$projectedEFree
            recommended_floor_met=[bool]([int64]$driveEAfter.free_bytes -ge $requiredERecommendedFree -or -not $Apply)
        }
        references=[ordered]@{
            hot_container_reference_count=[int64]$hotRefs.all_container_reference_count
            hot_compose_reference_count=[int64]$hotRefs.compose_reference_count
            logs_container_reference_count=[int64]$logsRefs.all_container_reference_count
            logs_compose_reference_count=[int64]$logsRefs.compose_reference_count
        }
        journal_path=$journalPath
        production=[ordered]@{
            clickhouse_ready_before=[bool]$productionBefore.ready
            clickhouse_ready_final=[bool]$productionFinal.ready
            accepted_volume=$AcceptedVolume
            accepted_production_mount_ready=$true
            running_raw_consumer_count=0
        }
        constraints=[ordered]@{
            native_no_follow_delete_authorized=[bool]($Apply -and $AcknowledgeLegacyEDuplicateDelete)
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
        env_unchanged=$envUnchanged
    }
    $receiptPath = Join-Path $evidenceDir 'production_rebalance_phase1_e_reparse_safe_delete_apply.json'
    $receipt | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION REBALANCE PHASE1 E REPARSE SAFE DELETE APPLY RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "apply_accepted=$applyAccepted"
    Write-Host "mutation_performed=$mutationPerformed"
    Write-Host "accepted_hot_manifest_sha256=$acceptedHotManifestSha"
    Write-Host "accepted_logs_manifest_sha256=$acceptedLogsManifestSha"
    Write-Host "boundary_manifest_match=True"
    Write-Host "native_lx_verified_count=$nativeVerified"
    Write-Host "e_free_before_bytes=$($driveEBefore.free_bytes)"
    Write-Host "e_free_after_bytes=$($driveEAfter.free_bytes)"
    Write-Host "e_required_recommended_free_bytes=$requiredERecommendedFree"
    Write-Host "production_invariant_preserved=True"
    Write-Host "env_unchanged=$envUnchanged"
    Write-Host 'accepted_volume_delete_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'vhdx_create_authorized=False'
    Write-Host 'wsl_shutdown_authorized=False'
    Write-Host 'us_package_2_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_REBALANCE_PHASE1_E_REPARSE_SAFE_DELETE_APPLY_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
