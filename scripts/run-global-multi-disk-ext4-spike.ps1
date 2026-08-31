[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$ToolingDistro = 'Ubuntu-24.04',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply,
    [switch]$CleanupMounts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$SpikeContainerName = 'markorbit-ext4-spike-clickhouse'
$ClickHouseImage = 'clickhouse/clickhouse-server:24.8'
$SpikeMaximumMiB = 1024
$InsertBatchCount = 24
$RowsPerBatch = 100

$diskSpecs = @(
    [ordered]@{ key='hot_cn'; path='D:\MarkOrbitData\spike\hot_cn_spike.vhdx'; mount='markorbit_hot_cn_spike'; label='mo_hot_cn_spike'; disk='hot_cn'; policy='spike_hot_cn'; table='spike_hot_cn_mt' },
    [ordered]@{ key='hot_us'; path='D:\MarkOrbitData\spike\hot_us_spike.vhdx'; mount='markorbit_hot_us_spike'; label='mo_hot_us_spike'; disk='hot_us'; policy='spike_hot_us'; table='spike_hot_us_mt' },
    [ordered]@{ key='hot_global'; path='D:\MarkOrbitData\spike\hot_global_spike.vhdx'; mount='markorbit_hot_global_spike'; label='mo_hot_global_spike'; disk='hot_global'; policy='spike_hot_global'; table='spike_hot_global_mt' },
    [ordered]@{ key='warm'; path='E:\MarkOrbitData\spike\warm_spike.vhdx'; mount='markorbit_warm_spike'; label='mo_warm_spike'; disk='warm'; policy='spike_warm'; table='spike_warm_mt' }
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
    finally {
        $ErrorActionPreference = $previous
    }
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $exitCode`: $($rendered -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code = $exitCode; lines = @($rendered) }
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

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) { return [ordered]@{ container_id=$null; health='missing'; ready=$false } }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $ready = [bool]($health -eq 'healthy' -and $sqlProbe['exit_code'] -eq 0 -and ((@($sqlProbe['lines']) -join '').Trim() -eq '1'))
    return [ordered]@{ container_id=$containerId; health=$health; ready=$ready }
}

function Test-ToolingDistro {
    $command = 'for c in mkfs.ext4 lsblk blkid findmnt; do command -v "$c" >/dev/null 2>&1 || exit 10; done'
    $command = $command.Replace('\"','"')
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','sh','-lc',$command) -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

function Get-WslBlockDisks {
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','lsblk','-dn','-o','NAME,TYPE') -AllowFailure
    if ($probe['exit_code'] -ne 0) {
        throw "Unable to list WSL block disks: $(@($probe['lines']) -join [Environment]::NewLine)"
    }
    $names = @()
    foreach ($line in @($probe['lines'])) {
        $fields = @($line.Trim() -split '\s+')
        if ($fields.Count -ge 2 -and $fields[1] -eq 'disk') { $names += $fields[0] }
    }
    return @($names)
}

function Get-MountProbe([string]$MountName) {
    $mountPath = "/mnt/wsl/$MountName"
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','sh','-lc',"findmnt -n -o FSTYPE,SOURCE,TARGET '$mountPath'") -AllowFailure
    $text = (@($probe['lines']) -join ' ').Trim()
    return [ordered]@{
        ready = [bool]($probe['exit_code'] -eq 0 -and $text -match '^ext4\s')
        exit_code = $probe['exit_code']
        text = $text
        mount_path = $mountPath
    }
}

function Invoke-SpikeSql {
    param(
        [Parameter(Mandatory = $true)][string]$Query,
        [switch]$MultiQuery,
        [switch]$AllowFailure
    )
    $args = @('exec',$SpikeContainerName,'clickhouse-client')
    if ($MultiQuery) { $args += '--multiquery' }
    $args += @('--query',$Query)
    return Invoke-NativeText 'docker' $args -AllowFailure:$AllowFailure
}

function Remove-SpikeContainer {
    $probe = Invoke-NativeText 'docker' @('ps','-aq','--filter',"name=^/$SpikeContainerName$") -AllowFailure
    $containerId = (@($probe['lines']) -join '').Trim()
    if (-not $containerId) { return $false }
    $remove = Invoke-NativeText 'docker' @('rm','-f',$SpikeContainerName) -AllowFailure
    return [bool]($remove['exit_code'] -eq 0)
}

function Unmount-SpikePath([string]$VhdxPath) {
    $probe = Invoke-NativeText 'wsl.exe' @('--unmount',$VhdxPath) -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

try {
    Write-Host '===== GLOBAL MULTI-DISK EXT4 BOUNDED SPIKE ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Bounded ext4 spike must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "global_multi_disk_ext4_spike_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'spike_stage=preflight'
    $workerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerIds = @($workerProbe['lines'] | Where-Object { $_.Trim() })
    $workerCount = $workerIds.Count
    $volumeProbe = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $acceptedVolumePresent = [bool]($volumeProbe['exit_code'] -eq 0)
    $productionBefore = Get-ProductionClickHouseHealth
    $toolingReady = Test-ToolingDistro
    $diskpartReady = [bool](Get-Command diskpart.exe -ErrorAction SilentlyContinue)
    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdministrator = [bool]$adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    $dDrive = Get-PSDrive -Name D -PSProvider FileSystem -ErrorAction SilentlyContinue
    $eDrive = Get-PSDrive -Name E -PSProvider FileSystem -ErrorAction SilentlyContinue
    $minimumDBytes = [int64](8GB)
    $minimumEBytes = [int64](4GB)

    $existingSpikeFiles = @($diskSpecs | Where-Object { Test-Path -LiteralPath $_['path'] } | ForEach-Object { $_['path'] })
    $existingSpikeMounts = @()
    foreach ($spec in $diskSpecs) {
        $probe = Get-MountProbe $spec['mount']
        if ($probe['exit_code'] -eq 0) { $existingSpikeMounts += $spec['mount'] }
    }
    $existingTempContainerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter',"name=^/$SpikeContainerName$") -AllowFailure
    $existingTempContainer = (@($existingTempContainerProbe['lines']) -join '').Trim()

    $blockers = @()
    if ($workerCount -ne 0) { $blockers += 'WORKER_CONTAINER_PRESENT' }
    if (-not $acceptedVolumePresent) { $blockers += 'ACCEPTED_CLICKHOUSE_VOLUME_MISSING' }
    if (-not $productionBefore['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_READY' }
    if (-not $toolingReady) { $blockers += 'WSL_EXT4_TOOLING_NOT_READY' }
    if (-not $diskpartReady) { $blockers += 'DISKPART_NOT_AVAILABLE' }
    if (-not $dDrive -or [int64]$dDrive.Free -lt $minimumDBytes) { $blockers += 'D_DRIVE_FREE_SPACE_BELOW_8GIB' }
    if (-not $eDrive -or [int64]$eDrive.Free -lt $minimumEBytes) { $blockers += 'E_DRIVE_FREE_SPACE_BELOW_4GIB' }
    if ($existingSpikeFiles.Count -gt 0) { $blockers += 'SPIKE_VHDX_ALREADY_EXISTS' }
    if ($existingSpikeMounts.Count -gt 0) { $blockers += 'SPIKE_MOUNT_ALREADY_EXISTS' }
    if ($existingTempContainer) { $blockers += 'SPIKE_CLICKHOUSE_CONTAINER_ALREADY_EXISTS' }

    $preflightReady = [bool]($blockers.Count -eq 0)
    $decision = if ($preflightReady) { 'READY_FOR_BOUNDED_EXT4_SPIKE_APPLY' } else { 'SPIKE_BLOCKED' }
    $runtimeError = $null
    $runtimeStage = $null
    $vhdxCreatePerformed = $false
    $filesystemFormatPerformed = $false
    $dockerBindProofPerformed = $false
    $tempClickHouseStarted = $false
    $tempClickHouseRemoved = $false
    $spikeUnmountPerformed = $false
    $spikeVhdxDeletePerformed = $false
    $mountedFinalPaths = @()
    $bareMountedPaths = @()
    $diskRuntime = @()
    $dockerProofs = @()
    $mergeTreeProofs = @()
    $storageDisksEvidence = @()
    $storagePoliciesEvidence = @()
    $clickhouseLogErrorMatches = @()

    if ($Apply) {
        Write-Host 'spike_stage=apply'
        if (-not $preflightReady) {
            throw "Bounded ext4 spike apply blocked: $($blockers -join ', ')"
        }
        if (-not $isAdministrator) {
            throw 'Bounded ext4 spike apply requires an elevated Administrator PowerShell session.'
        }

        try {
            $runtimeStage = 'create_format_mount'
            foreach ($spec in $diskSpecs) {
                $vhdxPath = [string]$spec['path']
                $parent = Split-Path -Parent $vhdxPath
                New-Item -ItemType Directory -Force -Path $parent | Out-Null

                $diskpartScript = Join-Path $evidenceDir "diskpart_create_$($spec['key']).txt"
                @(
                    "create vdisk file=`"$vhdxPath`" maximum=$SpikeMaximumMiB type=expandable",
                    'exit'
                ) | Set-Content -LiteralPath $diskpartScript -Encoding ASCII
                $create = Invoke-NativeText 'diskpart.exe' @('/s',$diskpartScript) -AllowFailure
                if ($create['exit_code'] -ne 0 -or -not (Test-Path -LiteralPath $vhdxPath)) {
                    throw "Failed to create bounded spike VHDX $vhdxPath`: $(@($create['lines']) -join [Environment]::NewLine)"
                }
                $vhdxCreatePerformed = $true

                $beforeDisks = @(Get-WslBlockDisks)
                $bareMount = Invoke-NativeText 'wsl.exe' @('--mount','--vhd',$vhdxPath,'--bare') -AllowFailure
                if ($bareMount['exit_code'] -ne 0) {
                    throw "Unable to attach new spike VHDX bare: $vhdxPath`: $(@($bareMount['lines']) -join [Environment]::NewLine)"
                }
                $bareMountedPaths += $vhdxPath
                Start-Sleep -Seconds 1
                $afterDisks = @(Get-WslBlockDisks)
                $newDisks = @($afterDisks | Where-Object { $_ -notin $beforeDisks })
                if ($newDisks.Count -ne 1) {
                    throw "Expected exactly one new WSL block disk for $vhdxPath; observed: $($newDisks -join ',')"
                }
                $device = "/dev/$($newDisks[0])"

                $mkfs = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','mkfs.ext4','-F','-L',[string]$spec['label'],$device) -AllowFailure
                if ($mkfs['exit_code'] -ne 0) {
                    throw "mkfs.ext4 failed for $vhdxPath on $device`: $(@($mkfs['lines']) -join [Environment]::NewLine)"
                }
                $filesystemFormatPerformed = $true

                if (-not (Unmount-SpikePath $vhdxPath)) {
                    throw "Unable to detach bare spike VHDX after format: $vhdxPath"
                }
                $bareMountedPaths = @($bareMountedPaths | Where-Object { $_ -ne $vhdxPath })

                $namedMount = Invoke-NativeText 'wsl.exe' @('--mount','--vhd',$vhdxPath,'--name',[string]$spec['mount']) -AllowFailure
                if ($namedMount['exit_code'] -ne 0) {
                    throw "Unable to mount formatted spike VHDX $vhdxPath`: $(@($namedMount['lines']) -join [Environment]::NewLine)"
                }
                $mountedFinalPaths += $vhdxPath
                Start-Sleep -Seconds 1
                $mountProbe = Get-MountProbe $spec['mount']
                if (-not $mountProbe['ready']) {
                    throw "Mounted spike disk is not confirmed ext4 for $($spec['key']): $($mountProbe['text'])"
                }

                $linuxRoot = "/mnt/wsl/$($spec['mount'])"
                $prepare = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','sh','-lc',"mkdir -p '$linuxRoot/clickhouse' && chmod 0777 '$linuxRoot/clickhouse'") -AllowFailure
                if ($prepare['exit_code'] -ne 0) {
                    throw "Unable to prepare ClickHouse spike directory on $linuxRoot`: $(@($prepare['lines']) -join [Environment]::NewLine)"
                }

                $diskRuntime += [ordered]@{
                    key = $spec['key']
                    vhdx_path = $vhdxPath
                    maximum_mib = $SpikeMaximumMiB
                    device = $device
                    mount_name = $spec['mount']
                    mount_path = $linuxRoot
                    filesystem = $mountProbe['text']
                    ext4_ready = $mountProbe['ready']
                }
            }

            $runtimeStage = 'docker_bind_visibility'
            $allDockerBindReady = $true
            $probeCommand = 'set -eu; fs=$(stat -f -c %T /probe); touch /probe/docker_bind_probe; sync; rm -f /probe/docker_bind_probe; printf "FS_TYPE=%s\n" "$fs"'
            $probeCommand = $probeCommand.Replace('\"','"')
            foreach ($spec in $diskSpecs) {
                $source = "/mnt/wsl/$($spec['mount'])/clickhouse"
                $dockerProbe = Invoke-NativeText 'docker' @('run','--rm','--entrypoint','sh','--mount',"type=bind,source=$source,target=/probe",$ClickHouseImage,'-lc',$probeCommand) -AllowFailure
                $dockerBindProofPerformed = $true
                $probeText = (@($dockerProbe['lines']) -join "`n").Trim()
                $fsType = $null
                foreach ($line in @($dockerProbe['lines'])) {
                    if ($line -match '^FS_TYPE=(.+)$') { $fsType = $Matches[1].Trim() }
                }
                $linuxFs = [bool]($dockerProbe['exit_code'] -eq 0 -and $fsType -match '^(ext2/ext3|ext4)$')
                if (-not $linuxFs) { $allDockerBindReady = $false }
                $dockerProofs += [ordered]@{
                    key = $spec['key']
                    source = $source
                    exit_code = $dockerProbe['exit_code']
                    fs_type = $fsType
                    linux_ext_filesystem = $linuxFs
                    output = $probeText
                }
            }

            if (-not $allDockerBindReady) {
                $decision = 'DEDICATED_WSL_CLICKHOUSE_REQUIRED'
            }
            else {
                $runtimeStage = 'isolated_clickhouse'
                $storageConfigPath = Join-Path $evidenceDir 'spike-storage.xml'
                @'
<clickhouse>
  <storage_configuration>
    <disks>
      <hot_cn><type>local</type><path>/var/lib/clickhouse/disks/hot_cn/</path></hot_cn>
      <hot_us><type>local</type><path>/var/lib/clickhouse/disks/hot_us/</path></hot_us>
      <hot_global><type>local</type><path>/var/lib/clickhouse/disks/hot_global/</path></hot_global>
      <warm><type>local</type><path>/var/lib/clickhouse/disks/warm/</path></warm>
    </disks>
    <policies>
      <spike_hot_cn><volumes><main><disk>hot_cn</disk></main></volumes></spike_hot_cn>
      <spike_hot_us><volumes><main><disk>hot_us</disk></main></volumes></spike_hot_us>
      <spike_hot_global><volumes><main><disk>hot_global</disk></main></volumes></spike_hot_global>
      <spike_warm><volumes><main><disk>warm</disk></main></volumes></spike_warm>
    </policies>
  </storage_configuration>
</clickhouse>
'@ | Set-Content -LiteralPath $storageConfigPath -Encoding UTF8
                $storageConfigPath = (Resolve-Path -LiteralPath $storageConfigPath).Path

                $runArgs = @('run','-d','--name',$SpikeContainerName,
                    '--mount',"type=bind,source=$storageConfigPath,target=/etc/clickhouse-server/config.d/markorbit-spike-storage.xml,readonly")
                foreach ($spec in $diskSpecs) {
                    $source = "/mnt/wsl/$($spec['mount'])/clickhouse"
                    $target = "/var/lib/clickhouse/disks/$($spec['disk'])"
                    $runArgs += @('--mount',"type=bind,source=$source,target=$target")
                }
                $runArgs += $ClickHouseImage
                $start = Invoke-NativeText 'docker' $runArgs -AllowFailure
                if ($start['exit_code'] -ne 0) {
                    throw "Unable to start isolated ClickHouse spike container: $(@($start['lines']) -join [Environment]::NewLine)"
                }
                $tempClickHouseStarted = $true

                $spikeReady = $false
                for ($attempt = 0; $attempt -lt 30; $attempt++) {
                    $readyProbe = Invoke-SpikeSql -Query 'SELECT 1' -AllowFailure
                    if ($readyProbe['exit_code'] -eq 0 -and ((@($readyProbe['lines']) -join '').Trim() -eq '1')) {
                        $spikeReady = $true
                        break
                    }
                    Start-Sleep -Seconds 2
                }
                if (-not $spikeReady) {
                    $logs = Invoke-NativeText 'docker' @('logs',$SpikeContainerName) -AllowFailure
                    throw "Isolated ClickHouse spike did not become ready: $(@($logs['lines']) -join [Environment]::NewLine)"
                }

                $diskEvidenceProbe = Invoke-SpikeSql -Query "SELECT name, path FROM system.disks WHERE name IN ('hot_cn','hot_us','hot_global','warm') ORDER BY name FORMAT TSV"
                $storageDisksEvidence = @($diskEvidenceProbe['lines'])
                if ($storageDisksEvidence.Count -ne 4) {
                    throw "Expected four external spike disks in system.disks; got $($storageDisksEvidence.Count)."
                }
                $policyEvidenceProbe = Invoke-SpikeSql -Query "SELECT policy_name, volume_name, arrayStringConcat(disks, ',') FROM system.storage_policies WHERE policy_name IN ('spike_hot_cn','spike_hot_us','spike_hot_global','spike_warm') ORDER BY policy_name FORMAT TSV"
                $storagePoliciesEvidence = @($policyEvidenceProbe['lines'])
                if ($storagePoliciesEvidence.Count -ne 4) {
                    throw "Expected four spike policies in system.storage_policies; got $($storagePoliciesEvidence.Count)."
                }

                $runtimeStage = 'mergetree_acceptance'
                $allMergeTreeReady = $true
                foreach ($spec in $diskSpecs) {
                    $table = [string]$spec['table']
                    $policy = [string]$spec['policy']
                    $disk = [string]$spec['disk']
                    [void](Invoke-SpikeSql -Query "DROP TABLE IF EXISTS default.$table")
                    [void](Invoke-SpikeSql -Query "CREATE TABLE default.$table (id UInt64, payload String) ENGINE=MergeTree ORDER BY id SETTINGS storage_policy='$policy'")

                    $statements = @()
                    for ($batch = 0; $batch -lt $InsertBatchCount; $batch++) {
                        $base = $batch * 1000
                        $statements += "INSERT INTO default.$table SELECT number + $base, repeat(toString(number), 4) FROM numbers($RowsPerBatch);"
                    }
                    $insert = Invoke-SpikeSql -Query ($statements -join [Environment]::NewLine) -MultiQuery -AllowFailure
                    if ($insert['exit_code'] -ne 0) {
                        throw "MergeTree insert failed for $table`: $(@($insert['lines']) -join [Environment]::NewLine)"
                    }

                    $mergeObserved = $false
                    $partsText = $null
                    for ($attempt = 0; $attempt -lt 30; $attempt++) {
                        $parts = Invoke-SpikeSql -Query "SELECT count(), sum(rows), max(level), uniqExact(disk_name), any(disk_name) FROM system.parts WHERE database='default' AND table='$table' AND active FORMAT TSV" -AllowFailure
                        if ($parts['exit_code'] -eq 0 -and $parts['lines'].Count -eq 1) {
                            $partsText = [string]$parts['lines'][0]
                            $fields = $partsText -split "`t"
                            if ($fields.Count -ge 5 -and [int]$fields[2] -gt 0) {
                                $mergeObserved = $true
                                break
                            }
                        }
                        Start-Sleep -Seconds 2
                    }

                    $expectedRows = $InsertBatchCount * $RowsPerBatch
                    $expectedSum = [int64]0
                    for ($batch = 0; $batch -lt $InsertBatchCount; $batch++) {
                        $base = $batch * 1000
                        $expectedSum += ([int64]$RowsPerBatch * [int64]$base) + ([int64]($RowsPerBatch - 1) * [int64]$RowsPerBatch / 2)
                    }
                    $select = Invoke-SpikeSql -Query "SELECT count(), sum(id) FROM default.$table FORMAT TSV" -AllowFailure
                    $selectText = if ($select['lines'].Count -eq 1) { [string]$select['lines'][0] } else { '' }
                    $selectFields = $selectText -split "`t"
                    $selectReady = [bool]($select['exit_code'] -eq 0 -and $selectFields.Count -ge 2 -and [int64]$selectFields[0] -eq $expectedRows -and [int64]$selectFields[1] -eq $expectedSum)

                    $diskNameReady = $false
                    if ($partsText) {
                        $partsFields = $partsText -split "`t"
                        $diskNameReady = [bool]($partsFields.Count -ge 5 -and [int]$partsFields[3] -eq 1 -and $partsFields[4] -eq $disk)
                    }
                    $tmpProbe = Invoke-NativeText 'docker' @('exec',$SpikeContainerName,'sh','-lc',"find '/var/lib/clickhouse/disks/$disk' -name 'tmp_insert_*' -print -quit") -AllowFailure
                    $tmpInsertCount = @($tmpProbe['lines'] | Where-Object { $_.Trim() }).Count
                    $proofReady = [bool]($mergeObserved -and $selectReady -and $diskNameReady -and $tmpInsertCount -eq 0)
                    if (-not $proofReady) { $allMergeTreeReady = $false }

                    $mergeTreeProofs += [ordered]@{
                        key = $spec['key']
                        table = $table
                        policy = $policy
                        disk = $disk
                        expected_rows = $expectedRows
                        expected_sum_id = $expectedSum
                        parts_evidence = $partsText
                        background_merge_observed = $mergeObserved
                        select_evidence = $selectText
                        select_verified = $selectReady
                        disk_name_verified = $diskNameReady
                        tmp_insert_count = $tmpInsertCount
                        ready = $proofReady
                    }
                    [void](Invoke-SpikeSql -Query "DROP TABLE IF EXISTS default.$table")
                }

                $logs = Invoke-NativeText 'docker' @('logs',$SpikeContainerName) -AllowFailure
                $logPath = Join-Path $evidenceDir 'isolated_clickhouse.log'
                @($logs['lines']) | Set-Content -LiteralPath $logPath -Encoding UTF8
                $clickhouseLogErrorMatches = @($logs['lines'] | Where-Object { $_ -match '(?i)(permission denied|operation not permitted|cannot rename|failed to rename|tmp_insert_.*rename)' })
                if (-not $allMergeTreeReady -or $clickhouseLogErrorMatches.Count -gt 0) {
                    $decision = 'DEDICATED_WSL_CLICKHOUSE_REQUIRED'
                }
                else {
                    $decision = 'DOCKER_DESKTOP_EXTERNAL_EXT4_GO'
                }
            }
        }
        catch {
            $runtimeError = $_.Exception.Message
            if ($diskRuntime.Count -eq $diskSpecs.Count -and @($diskRuntime | Where-Object { -not $_['ext4_ready'] }).Count -eq 0) {
                $decision = 'DEDICATED_WSL_CLICKHOUSE_REQUIRED'
            }
            else {
                $decision = 'SPIKE_BLOCKED'
            }
        }
        finally {
            $tempClickHouseRemoved = Remove-SpikeContainer
            foreach ($barePath in @($bareMountedPaths)) {
                [void](Unmount-SpikePath $barePath)
            }
        }
    }

    Write-Host 'spike_stage=acceptance'
    $productionAfter = Get-ProductionClickHouseHealth
    $volumeAfter = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $acceptedVolumeAfterPresent = [bool]($volumeAfter['exit_code'] -eq 0)
    $workerAfterProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerAfterCount = @($workerAfterProbe['lines'] | Where-Object { $_.Trim() }).Count

    if ($Apply -and (-not $productionAfter['ready'] -or -not $acceptedVolumeAfterPresent -or $workerAfterCount -ne 0)) {
        $decision = 'SPIKE_BLOCKED'
        if (-not $runtimeError) { $runtimeError = 'Production safety invariant changed during bounded spike acceptance.' }
    }

    if ($Apply -and $CleanupMounts) {
        Write-Host 'spike_stage=cleanup_mounts'
        $cleanupFailures = @()
        foreach ($spec in $diskSpecs) {
            if (Test-Path -LiteralPath $spec['path']) {
                if (-not (Unmount-SpikePath $spec['path'])) { $cleanupFailures += $spec['path'] }
            }
        }
        if ($cleanupFailures.Count -gt 0) {
            $decision = 'SPIKE_BLOCKED'
            $runtimeError = "Spike mount cleanup failed for: $($cleanupFailures -join ', ')"
        }
        else {
            $spikeUnmountPerformed = $true
        }
    }

    $report = [ordered]@{
        receipt_version = 'GLOBAL_MULTI_DISK_EXT4_SPIKE_V1'
        decision = $decision
        engine_sha = $ExpectedMainSha.Trim().ToLowerInvariant()
        apply_requested = [bool]$Apply
        cleanup_mounts_requested = [bool]$CleanupMounts
        windows_is_administrator = $isAdministrator
        worker_container_count_before = $workerCount
        worker_container_count_after = $workerAfterCount
        accepted_clickhouse_volume = $AcceptedVolume
        accepted_volume_before_present = $acceptedVolumePresent
        accepted_volume_after_present = $acceptedVolumeAfterPresent
        production_clickhouse_before = $productionBefore
        production_clickhouse_after = $productionAfter
        tooling_distro = $ToolingDistro
        tooling_ready = $toolingReady
        spike_maximum_mib_each = $SpikeMaximumMiB
        blockers = @($blockers)
        runtime_stage = $runtimeStage
        runtime_error = $runtimeError
        disks = @($diskRuntime)
        docker_bind_proofs = @($dockerProofs)
        system_disks = @($storageDisksEvidence)
        system_storage_policies = @($storagePoliciesEvidence)
        mergetree_proofs = @($mergeTreeProofs)
        clickhouse_log_error_matches = @($clickhouseLogErrorMatches)
        vhdx_create_performed = $vhdxCreatePerformed
        filesystem_format_performed = $filesystemFormatPerformed
        docker_bind_proof_performed = $dockerBindProofPerformed
        temporary_clickhouse_started = $tempClickHouseStarted
        temporary_clickhouse_removed = $tempClickHouseRemoved
        spike_unmount_performed = $spikeUnmountPerformed
        spike_vhdx_delete_performed = $spikeVhdxDeletePerformed
        production_clickhouse_restart_performed = $false
        production_clickhouse_mutation_performed = $false
        accepted_volume_mutation_performed = $false
        corpus_replay_performed = $false
    }
    $reportPath = Join-Path $evidenceDir 'global_multi_disk_ext4_spike.json'
    $report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== GLOBAL MULTI-DISK EXT4 BOUNDED SPIKE RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "cleanup_mounts_requested=$([bool]$CleanupMounts)"
    Write-Host "windows_is_administrator=$isAdministrator"
    Write-Host "worker_container_count_before=$workerCount"
    Write-Host "worker_container_count_after=$workerAfterCount"
    Write-Host "tooling_ready=$toolingReady"
    Write-Host "production_clickhouse_before_ready=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_after_ready=$($productionAfter['ready'])"
    Write-Host "accepted_volume_before_present=$acceptedVolumePresent"
    Write-Host "accepted_volume_after_present=$acceptedVolumeAfterPresent"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    if ($runtimeStage) { Write-Host "runtime_stage=$runtimeStage" }
    if ($runtimeError) { Write-Host "runtime_error=$runtimeError" }
    foreach ($disk in $diskRuntime) { Write-Host "ext4_disk=$($disk['key'])|path=$($disk['vhdx_path'])|mount=$($disk['mount_path'])|fs=$($disk['filesystem'])" }
    foreach ($proof in $dockerProofs) { Write-Host "docker_bind=$($proof['key'])|ready=$($proof['linux_ext_filesystem'])|fs=$($proof['fs_type'])|exit=$($proof['exit_code'])" }
    foreach ($proof in $mergeTreeProofs) { Write-Host "mergetree=$($proof['key'])|ready=$($proof['ready'])|merge=$($proof['background_merge_observed'])|select=$($proof['select_verified'])|disk=$($proof['disk_name_verified'])|tmp_insert=$($proof['tmp_insert_count'])" }
    Write-Host "vhdx_create_performed=$vhdxCreatePerformed"
    Write-Host "filesystem_format_performed=$filesystemFormatPerformed"
    Write-Host "temporary_clickhouse_started=$tempClickHouseStarted"
    Write-Host "temporary_clickhouse_removed=$tempClickHouseRemoved"
    Write-Host "spike_unmount_performed=$spikeUnmountPerformed"
    Write-Host 'spike_vhdx_delete_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'GLOBAL_MULTI_DISK_EXT4_SPIKE_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
