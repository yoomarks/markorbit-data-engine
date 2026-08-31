[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [ValidateSet('D','E','F')]
    [string[]]$DriveLetters = @('D','E','F'),
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if ($exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $exitCode`: $($rendered -join [Environment]::NewLine)"
    }
    return $rendered
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift detected during $Phase."
    }
    if (git status --porcelain) {
        throw "Working tree must be clean during $Phase."
    }
}

function Get-DriveEvidence([string]$Letter) {
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) {
        return [ordered]@{
            drive = "${Letter}:"
            present = $false
            root = $root
            total_bytes = [int64]0
            free_bytes = [int64]0
            used_bytes = [int64]0
            filesystem = $null
            volume_label = $null
            physical_disks = @()
            physical_mapping_error = $null
        }
    }

    $driveInfo = [System.IO.DriveInfo]::new($root)
    $physicalDisks = @()
    $mappingError = $null
    try {
        $logical = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='${Letter}:'" -ErrorAction Stop
        if ($logical) {
            $partitions = @(Get-CimAssociatedInstance -InputObject $logical -Association Win32_LogicalDiskToPartition -ErrorAction Stop)
            foreach ($partition in $partitions) {
                $diskDrives = @(Get-CimAssociatedInstance -InputObject $partition -Association Win32_DiskDriveToDiskPartition -ErrorAction Stop)
                foreach ($diskDrive in $diskDrives) {
                    $storageDisk = $null
                    $physicalDisk = $null
                    try { $storageDisk = Get-Disk -Number ([int]$diskDrive.Index) -ErrorAction Stop }
                    catch {}
                    try {
                        $physicalDisk = Get-PhysicalDisk -ErrorAction Stop |
                            Where-Object { [string]$_.DeviceId -eq [string]$diskDrive.Index } |
                            Select-Object -First 1
                    }
                    catch {}

                    $physicalDisks += [ordered]@{
                        disk_index = [int]$diskDrive.Index
                        device_id = [string]$diskDrive.DeviceID
                        model = [string]$diskDrive.Model
                        serial_number = [string]$diskDrive.SerialNumber
                        wmi_interface_type = [string]$diskDrive.InterfaceType
                        wmi_media_type = [string]$diskDrive.MediaType
                        size_bytes = [int64]$diskDrive.Size
                        friendly_name = if ($storageDisk) { [string]$storageDisk.FriendlyName } else { $null }
                        bus_type = if ($storageDisk) { [string]$storageDisk.BusType } else { $null }
                        partition_style = if ($storageDisk) { [string]$storageDisk.PartitionStyle } else { $null }
                        physical_media_type = if ($physicalDisk) { [string]$physicalDisk.MediaType } else { $null }
                    }
                }
            }
        }
    }
    catch {
        $mappingError = $_.Exception.Message
    }

    return [ordered]@{
        drive = "${Letter}:"
        present = $true
        root = $root
        total_bytes = [int64]$driveInfo.TotalSize
        free_bytes = [int64]$driveInfo.AvailableFreeSpace
        used_bytes = [int64]($driveInfo.TotalSize - $driveInfo.AvailableFreeSpace)
        filesystem = [string]$driveInfo.DriveFormat
        volume_label = [string]$driveInfo.VolumeLabel
        physical_disks = @($physicalDisks)
        physical_mapping_error = $mappingError
    }
}

function Add-VhdxCandidates([System.Collections.ArrayList]$List, [string]$Root) {
    if (-not $Root -or -not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -Recurse -Filter '*.vhdx' -File -ErrorAction SilentlyContinue)) {
        if (-not $List.Contains($item.FullName)) { [void]$List.Add($item.FullName) }
    }
}

function Get-VhdxEvidence {
    $paths = New-Object System.Collections.ArrayList
    foreach ($root in @(
        'D:\DockerData',
        'E:\DockerData',
        'F:\DockerData',
        'D:\MarkOrbitData',
        'E:\MarkOrbitData',
        'F:\MarkOrbitData',
        (Join-Path $env:LOCALAPPDATA 'Docker\wsl')
    )) {
        Add-VhdxCandidates $paths $root
    }

    $entries = @()
    $backingRoots = @()
    foreach ($path in @($paths)) {
        $item = Get-Item -LiteralPath $path
        $driveRoot = [System.IO.Path]::GetPathRoot($item.FullName)
        $driveInfo = [System.IO.DriveInfo]::new($driveRoot)
        $entries += [ordered]@{
            path = $item.FullName
            file_bytes = [int64]$item.Length
            backing_drive = $driveRoot.TrimEnd('\')
            backing_drive_total_bytes = [int64]$driveInfo.TotalSize
            backing_drive_free_bytes = [int64]$driveInfo.AvailableFreeSpace
        }
        if ($backingRoots -notcontains $driveRoot) { $backingRoots += $driveRoot }
    }

    return [ordered]@{
        candidate_count = $entries.Count
        candidates = @($entries)
        backing_drive_roots = @($backingRoots)
        backing_drive_unambiguous = [bool]($backingRoots.Count -eq 1)
    }
}

try {
    Write-Host '===== GLOBAL MULTI-DISK HOST INVENTORY ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Host inventory must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "global_multi_disk_host_inventory_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'inventory_stage=host_drives'
    $driveEvidence = @()
    foreach ($letter in $DriveLetters) {
        $driveEvidence += Get-DriveEvidence $letter
    }

    Write-Host 'inventory_stage=vhdx'
    $vhdxEvidence = Get-VhdxEvidence

    Write-Host 'inventory_stage=current_clickhouse_volume'
    $volumeJson = (Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume)) -join "`n"
    $volumeInspect = @($volumeJson | ConvertFrom-Json)
    if ($volumeInspect.Count -ne 1) {
        throw "Expected exactly one Docker volume inspect result for $AcceptedVolume."
    }

    $missingDrives = @($driveEvidence | Where-Object { -not [bool]$_.present } | ForEach-Object { $_.drive })
    $blockers = @()
    foreach ($missing in $missingDrives) {
        $blockers += "TARGET_DRIVE_MISSING_$($missing.TrimEnd(':'))"
    }

    $report = [ordered]@{
        inventory_version = 'GLOBAL_MULTI_DISK_HOST_INVENTORY_V1'
        read_only = $true
        status = if ($blockers.Count -eq 0) { 'PASS' } else { 'BLOCKED' }
        engine_sha = $ExpectedMainSha.Trim().ToLowerInvariant()
        drives = @($driveEvidence)
        vhdx = $vhdxEvidence
        current_clickhouse_volume = [ordered]@{
            name = $AcceptedVolume
            driver = [string]$volumeInspect[0].Driver
            mountpoint = [string]$volumeInspect[0].Mountpoint
        }
        proposed_layout = [ordered]@{
            hot = @(
                [ordered]@{ name = 'hot_cn'; host_drive = 'D:'; filesystem = 'ext4'; capacity_bytes = $null; sizing_dependency = '#262'; state = 'PROVISIONAL' },
                [ordered]@{ name = 'hot_us'; host_drive = 'D:'; filesystem = 'ext4'; capacity_bytes = $null; sizing_dependency = '#340'; state = 'PROVISIONAL' },
                [ordered]@{ name = 'hot_global'; host_drive = 'D:'; filesystem = 'ext4'; capacity_bytes = $null; sizing_dependency = 'future jurisdiction evidence'; state = 'PROVISIONAL' }
            )
            warm = @(
                [ordered]@{ name = 'warm'; host_drive = 'E:'; filesystem = 'ext4'; capacity_bytes = $null; sizing_dependency = '#262/#340'; state = 'PROVISIONAL' }
            )
            raw_cold = [ordered]@{
                host_drive = 'F:'
                filesystem = 'native_windows'
                role = 'raw/source/archive/replay/snapshots'
                clickhouse_mergetree_primary_parts_allowed = $false
            }
        }
        blockers = @($blockers)
        destructive_action_performed = $false
        vhdx_create_performed = $false
        vhdx_mount_performed = $false
        vhdx_resize_performed = $false
        vhdx_move_performed = $false
        docker_restart_performed = $false
        docker_prune_performed = $false
        clickhouse_mutation_performed = $false
        corpus_replay_performed = $false
        next_action = if ($blockers.Count -eq 0) { 'FREEZE_NON_PRODUCTION_EXT4_SPIKE_PLAN' } else { 'RESOLVE_HOST_INVENTORY_BLOCKERS' }
    }

    $reportPath = Join-Path $evidenceDir 'global_multi_disk_host_inventory.json'
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    $gib = [math]::Pow(1024, 3)
    Write-Host '===== GLOBAL MULTI-DISK HOST INVENTORY RESULT ====='
    foreach ($drive in $driveEvidence) {
        if ([bool]$drive.present) {
            Write-Host ("drive_{0}_total_gib={1:N2}" -f $drive.drive.TrimEnd(':'), ($drive.total_bytes / $gib))
            Write-Host ("drive_{0}_free_gib={1:N2}" -f $drive.drive.TrimEnd(':'), ($drive.free_bytes / $gib))
            Write-Host ("drive_{0}_filesystem={1}" -f $drive.drive.TrimEnd(':'), $drive.filesystem)
            $media = @($drive.physical_disks | ForEach-Object { if ($_.physical_media_type) { $_.physical_media_type } elseif ($_.wmi_media_type) { $_.wmi_media_type } else { 'Unknown' } }) -join ','
            Write-Host ("drive_{0}_media={1}" -f $drive.drive.TrimEnd(':'), $media)
        }
        else {
            Write-Host ("drive_{0}_present=False" -f $drive.drive.TrimEnd(':'))
        }
    }
    Write-Host "vhdx_candidate_count=$($vhdxEvidence.candidate_count)"
    Write-Host "vhdx_backing_drive_unambiguous=$($vhdxEvidence.backing_drive_unambiguous)"
    foreach ($candidate in $vhdxEvidence.candidates) {
        Write-Host "vhdx=$($candidate.path)"
    }
    Write-Host "current_clickhouse_volume=$AcceptedVolume"
    Write-Host "destructive_action_performed=False"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'GLOBAL_MULTI_DISK_HOST_INVENTORY_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
