[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$RuntimeDistro = 'MarkOrbit-ClickHouse-Spike',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [int]$RuntimeTimeoutSeconds = 15,
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$expectedSpikeVirtualBytes = 1073741824
$spikeVhdx = @(
    [ordered]@{ key='hot_cn'; path='D:\MarkOrbitData\spike\hot_cn_spike.vhdx' },
    [ordered]@{ key='hot_us'; path='D:\MarkOrbitData\spike\hot_us_spike.vhdx' },
    [ordered]@{ key='hot_global'; path='D:\MarkOrbitData\spike\hot_global_spike.vhdx' },
    [ordered]@{ key='warm'; path='E:\MarkOrbitData\spike\warm_spike.vhdx' }
)

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $exitCode`: $($rendered -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($rendered) }
}

function Invoke-RuntimeText {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = $RuntimeTimeoutSeconds,
        [switch]$AllowFailure
    )
    $wrapped = @('timeout','--signal=TERM','--kill-after=3s',"${TimeoutSeconds}s") + $Arguments
    $result = Invoke-NativeText 'wsl.exe' (@('-d',$RuntimeDistro,'-u','root','--') + $wrapped) -AllowFailure
    $result['timed_out'] = [bool]($result['exit_code'] -eq 124)
    if (-not $AllowFailure -and $result['exit_code'] -ne 0) {
        throw "Runtime command failed with exit code $($result['exit_code']): $(@($result['lines']) -join [Environment]::NewLine)"
    }
    return $result
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $originMain -ne $expected) { throw "Exact main drift detected during $Phase." }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) { return [ordered]@{ ready=$false; version=$null } }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $versionProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $ready = [bool]($health -eq 'healthy' -and $sqlProbe['exit_code'] -eq 0 -and ((@($sqlProbe['lines']) -join '').Trim() -eq '1'))
    $version = if ($versionProbe['exit_code'] -eq 0) { (@($versionProbe['lines']) -join '').Trim() } else { $null }
    return [ordered]@{ ready=$ready; version=$version }
}

function Get-WorkerContainerCount {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-a','-q','worker') -AllowFailure
    if ($probe['exit_code'] -ne 0) { return -1 }
    return @($probe['lines'] | Where-Object { $_.Trim() }).Count
}

function Test-AcceptedVolumePresent {
    $probe = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

function Parse-LsblkPairs([string[]]$Lines) {
    $rows = @()
    foreach ($line in $Lines) {
        $row = [ordered]@{}
        foreach ($match in [regex]::Matches([string]$line,'([A-Z0-9_]+)="([^"]*)"')) {
            $row[$match.Groups[1].Value] = $match.Groups[2].Value
        }
        if ($row.Count -gt 0) { $rows += $row }
    }
    return @($rows)
}

try {
    Write-Host '===== WSL EXTERNAL DISK STATE READ-ONLY PROFILE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'WSL external disk profile must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'WSL external disk profile requires elevated Administrator PowerShell.' }

    $workerBefore = Get-WorkerContainerCount
    $productionBefore = Get-ProductionClickHouseHealth
    $acceptedBefore = Test-AcceptedVolumePresent
    if ($workerBefore -ne 0) { throw "Worker containers must be zero before read-only profile; observed $workerBefore." }
    if (-not $productionBefore['ready']) { throw 'Production ClickHouse must be healthy before read-only profile.' }
    if (-not $acceptedBefore) { throw 'Accepted ClickHouse volume must exist before read-only profile.' }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "wsl_external_disk_state_profile_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    Write-Host 'profile_step=wsl_version'
    $wslVersion = Invoke-NativeText 'wsl.exe' @('--version') -AllowFailure
    foreach ($line in @($wslVersion['lines'])) { Write-Host "wsl_version_evidence=$line" }

    Write-Host 'profile_step=wsl_list_verbose'
    $wslList = Invoke-NativeText 'wsl.exe' @('--list','--verbose') -AllowFailure
    foreach ($line in @($wslList['lines'])) { Write-Host "wsl_distro_evidence=$line" }

    Write-Host 'profile_step=runtime_lsblk'
    $lsblk = Invoke-RuntimeText @('lsblk','-b','-P','-o','NAME,PATH,TYPE,SIZE,FSTYPE,UUID,MOUNTPOINTS,RO') -AllowFailure
    if ($lsblk['timed_out'] -or $lsblk['exit_code'] -ne 0) { throw 'Unable to collect runtime lsblk inventory.' }
    foreach ($line in @($lsblk['lines'])) { Write-Host "lsblk_evidence=$line" }
    $lsblkRows = Parse-LsblkPairs @($lsblk['lines'])
    $orphanCandidates = @($lsblkRows | Where-Object {
        $_['TYPE'] -eq 'disk' -and
        $_['FSTYPE'] -eq 'ext4' -and
        [int64]$_['SIZE'] -eq $expectedSpikeVirtualBytes -and
        [string]::IsNullOrWhiteSpace([string]$_['MOUNTPOINTS'])
    })

    Write-Host 'profile_step=runtime_findmnt'
    $findmnt = Invoke-RuntimeText @('findmnt','-rn','-o','SOURCE,FSTYPE,TARGET') -AllowFailure
    if ($findmnt['timed_out'] -or $findmnt['exit_code'] -ne 0) { throw 'Unable to collect runtime findmnt inventory.' }
    $mntWslLines = @($findmnt['lines'] | Where-Object { [string]$_ -match '\s/mnt/wsl(?:/|$)' })
    $mntWslRootLines = @($mntWslLines | Where-Object { [string]$_ -match '\s/mnt/wsl$' })
    $dockerManagedMntWslLines = @($mntWslLines | Where-Object { [string]$_ -match '\s/mnt/wsl/docker-desktop(?:/|$)' })
    $foreignMntWslLines = @($mntWslLines | Where-Object {
        [string]$_ -match '\s/mnt/wsl/' -and
        [string]$_ -notmatch '\s/mnt/wsl/docker-desktop(?:/|$)'
    })
    foreach ($line in @($mntWslLines)) { Write-Host "mnt_wsl_evidence=$line" }
    foreach ($line in @($dockerManagedMntWslLines)) { Write-Host "docker_managed_mnt_wsl_evidence=$line" }
    foreach ($line in @($foreignMntWslLines)) { Write-Host "foreign_mnt_wsl_evidence=$line" }

    Write-Host 'profile_step=runtime_blkid'
    $blkid = Invoke-RuntimeText @('blkid') -AllowFailure
    foreach ($line in @($blkid['lines'])) { Write-Host "blkid_evidence=$line" }

    Write-Host 'profile_step=runtime_dmesg_warnings'
    $dmesg = Invoke-RuntimeText @('dmesg','--color=never','--level=err,warn') -TimeoutSeconds 10 -AllowFailure
    $dmesgTail = @($dmesg['lines'] | Select-Object -Last 120)
    foreach ($line in $dmesgTail) { Write-Host "dmesg_evidence=$line" }

    Write-Host 'profile_step=windows_get_disk'
    $windowsDisks = @()
    try {
        $windowsDisks = @(Get-Disk | Select-Object Number,FriendlyName,SerialNumber,BusType,OperationalStatus,PartitionStyle,Size,IsOffline,IsReadOnly)
        foreach ($disk in $windowsDisks) {
            Write-Host ("windows_disk=number:{0}|name:{1}|bus:{2}|status:{3}|style:{4}|size:{5}|offline:{6}|readonly:{7}" -f $disk.Number,$disk.FriendlyName,$disk.BusType,($disk.OperationalStatus -join ','),$disk.PartitionStyle,$disk.Size,$disk.IsOffline,$disk.IsReadOnly)
        }
    }
    catch { Write-Host "windows_disk_probe_error=$($_.Exception.Message)" }

    Write-Host 'profile_step=windows_get_vhd'
    $getVhdAvailable = [bool](Get-Command Get-VHD -ErrorAction SilentlyContinue)
    $vhdEvidence = @()
    foreach ($spec in $spikeVhdx) {
        $entry = [ordered]@{ key=$spec['key']; path=$spec['path']; exists=[bool](Test-Path -LiteralPath $spec['path']); get_vhd_available=$getVhdAvailable; query_ok=$false; attached=$null; size=$null; file_size=$null; error=$null }
        if ($entry['exists'] -and $getVhdAvailable) {
            try {
                $vhd = Get-VHD -Path ([string]$spec['path']) -ErrorAction Stop
                $entry['query_ok'] = $true
                $entry['attached'] = [bool]$vhd.Attached
                $entry['size'] = [int64]$vhd.Size
                $entry['file_size'] = [int64]$vhd.FileSize
            }
            catch { $entry['error'] = $_.Exception.Message }
        }
        $vhdEvidence += $entry
        Write-Host "vhd_evidence=$($entry['key'])|exists=$($entry['exists'])|get_vhd_available=$($entry['get_vhd_available'])|query_ok=$($entry['query_ok'])|attached=$($entry['attached'])|size=$($entry['size'])|file_size=$($entry['file_size'])|error=$($entry['error'])"
    }

    $workerAfter = Get-WorkerContainerCount
    $productionAfter = Get-ProductionClickHouseHealth
    $acceptedAfter = Test-AcceptedVolumePresent
    if ($workerAfter -ne 0) { throw "Worker containers changed during read-only profile; observed $workerAfter." }
    if (-not $productionAfter['ready']) { throw 'Production ClickHouse became unhealthy during read-only profile.' }
    if (-not $acceptedAfter) { throw 'Accepted ClickHouse volume disappeared during read-only profile.' }

    $receipt = [ordered]@{
        decision='WSL_EXTERNAL_DISK_STATE_PROFILE_DONE'
        expected_spike_virtual_bytes=$expectedSpikeVirtualBytes
        orphan_ext4_1g_candidate_count=$orphanCandidates.Count
        orphan_ext4_1g_candidates=@($orphanCandidates)
        mnt_wsl_mount_count=$mntWslLines.Count
        mnt_wsl_root_mount_count=$mntWslRootLines.Count
        docker_managed_mnt_wsl_mount_count=$dockerManagedMntWslLines.Count
        foreign_mnt_wsl_mount_count=$foreignMntWslLines.Count
        foreign_mnt_wsl_mounts=@($foreignMntWslLines)
        mnt_wsl_safety_authority='foreign_children_excluding_docker_desktop_namespace'
        vhd_evidence=@($vhdEvidence)
        wsl_version_exit=$wslVersion['exit_code']
        wsl_list_exit=$wslList['exit_code']
        worker_container_count_before=$workerBefore
        worker_container_count_after=$workerAfter
        production_clickhouse_before_ready=$productionBefore['ready']
        production_clickhouse_after_ready=$productionAfter['ready']
        accepted_volume_before_present=$acceptedBefore
        accepted_volume_after_present=$acceptedAfter
        no_arg_unmount_authorized=$false
        wsl_mount_performed=$false
        wsl_unmount_performed=$false
        wsl_shutdown_performed=$false
        production_clickhouse_restart_performed=$false
        production_clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        corpus_replay_performed=$false
    }
    $receiptPath = Join-Path $evidenceDir 'receipt.json'
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== WSL EXTERNAL DISK STATE READ-ONLY PROFILE RESULT ====='
    Write-Host 'decision=WSL_EXTERNAL_DISK_STATE_PROFILE_DONE'
    Write-Host "orphan_ext4_1g_candidate_count=$($orphanCandidates.Count)"
    Write-Host "mnt_wsl_mount_count=$($mntWslLines.Count)"
    Write-Host "mnt_wsl_root_mount_count=$($mntWslRootLines.Count)"
    Write-Host "docker_managed_mnt_wsl_mount_count=$($dockerManagedMntWslLines.Count)"
    Write-Host "foreign_mnt_wsl_mount_count=$($foreignMntWslLines.Count)"
    Write-Host 'mnt_wsl_safety_authority=foreign_children_excluding_docker_desktop_namespace'
    Write-Host "get_vhd_available=$getVhdAvailable"
    Write-Host "worker_container_count_before=$workerBefore"
    Write-Host "worker_container_count_after=$workerAfter"
    Write-Host "production_clickhouse_before_ready=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_after_ready=$($productionAfter['ready'])"
    Write-Host "accepted_volume_before_present=$acceptedBefore"
    Write-Host "accepted_volume_after_present=$acceptedAfter"
    Write-Host 'no_arg_unmount_authorized=False'
    Write-Host 'wsl_mount_performed=False'
    Write-Host 'wsl_unmount_performed=False'
    Write-Host 'wsl_shutdown_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'WSL_EXTERNAL_DISK_STATE_READ_ONLY_PROFILE_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
