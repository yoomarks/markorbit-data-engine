[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$RuntimeDistro = 'MarkOrbit-ClickHouse-Spike',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [int]$RuntimeTimeoutSeconds = 15,
    [int]$UnmountTimeoutSeconds = 30,
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$expectedSpikeVirtualBytes = 1073741824
$expectedOrphanLabel = 'mo_hot_cn_spike'
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

function Get-RecoveryState {
    Write-Host 'recovery_probe=runtime_lsblk'
    $lsblk = Invoke-RuntimeText @('lsblk','-b','-P','-o','NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,RO') -AllowFailure
    if ($lsblk['timed_out'] -or $lsblk['exit_code'] -ne 0) { throw 'Unable to collect runtime lsblk recovery state.' }
    foreach ($line in @($lsblk['lines'])) { Write-Host "recovery_lsblk=$line" }
    $rows = Parse-LsblkPairs @($lsblk['lines'])
    $spikeShape = @($rows | Where-Object {
        $_['TYPE'] -eq 'disk' -and
        $_['FSTYPE'] -eq 'ext4' -and
        [int64]$_['SIZE'] -eq $expectedSpikeVirtualBytes -and
        [string]::IsNullOrWhiteSpace([string]$_['MOUNTPOINTS'])
    })
    $expectedOrphan = @($spikeShape | Where-Object { [string]$_['LABEL'] -eq $expectedOrphanLabel })
    $foreignSpikeShape = @($spikeShape | Where-Object { [string]$_['LABEL'] -ne $expectedOrphanLabel })

    Write-Host 'recovery_probe=runtime_findmnt'
    $findmnt = Invoke-RuntimeText @('findmnt','-rn','-o','SOURCE,FSTYPE,TARGET') -AllowFailure
    if ($findmnt['timed_out'] -or $findmnt['exit_code'] -ne 0) { throw 'Unable to collect runtime findmnt recovery state.' }
    $externalMounts = @($findmnt['lines'] | Where-Object { [string]$_ -match '\s/mnt/wsl/.+' })
    foreach ($line in @($externalMounts)) { Write-Host "recovery_external_mount=$line" }

    return [ordered]@{
        lsblk_rows=@($rows)
        spike_shape_candidates=@($spikeShape)
        expected_orphan_candidates=@($expectedOrphan)
        foreign_spike_shape_candidates=@($foreignSpikeShape)
        external_mounts=@($externalMounts)
    }
}

function Invoke-NoArgUnmountBounded {
    param([int]$TimeoutSeconds = $UnmountTimeoutSeconds)
    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath 'wsl.exe' -ArgumentList '--unmount' -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $completed = $process.WaitForExit($TimeoutSeconds * 1000)
        $timedOut = -not $completed
        if ($timedOut) {
            try { $process.Kill() } catch { }
            try { $process.WaitForExit() } catch { }
        }
        else {
            $process.WaitForExit()
            $process.Refresh()
        }
        $lines = @()
        if (Test-Path -LiteralPath $stdoutPath) { $lines += @(Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue) }
        if (Test-Path -LiteralPath $stderrPath) { $lines += @(Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue) }
        $exitCode = if ($timedOut) { 124 } else { [int]$process.ExitCode }
        return [ordered]@{ exit_code=$exitCode; timed_out=$timedOut; lines=@($lines) }
    }
    finally {
        [System.IO.File]::Delete($stdoutPath)
        [System.IO.File]::Delete($stderrPath)
    }
}

try {
    Write-Host '===== WSL NO-ARG UNMOUNT RECOVERY ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'WSL no-arg unmount recovery must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $allSpikeVhdxExist = [bool](@($spikeVhdx | Where-Object { -not (Test-Path -LiteralPath ([string]$_['path'])) }).Count -eq 0)
    if (-not $allSpikeVhdxExist) { throw 'All four retained spike VHDX files must exist before recovery.' }

    $workerBefore = Get-WorkerContainerCount
    $productionBefore = Get-ProductionClickHouseHealth
    $acceptedBefore = Test-AcceptedVolumePresent
    $stateBefore = Get-RecoveryState

    $gateWorkers = [bool]($workerBefore -eq 0)
    $gateProduction = [bool]$productionBefore['ready']
    $gateAcceptedVolume = [bool]$acceptedBefore
    $gateSpikeFiles = $allSpikeVhdxExist
    $gateOneSpikeShape = [bool](@($stateBefore['spike_shape_candidates']).Count -eq 1)
    $gateOneExpectedOrphan = [bool](@($stateBefore['expected_orphan_candidates']).Count -eq 1)
    $gateNoForeignSpikeShape = [bool](@($stateBefore['foreign_spike_shape_candidates']).Count -eq 0)
    $gateNoExternalMounts = [bool](@($stateBefore['external_mounts']).Count -eq 0)
    $noArgUnmountAuthorized = [bool](
        $gateWorkers -and
        $gateProduction -and
        $gateAcceptedVolume -and
        $gateSpikeFiles -and
        $gateOneSpikeShape -and
        $gateOneExpectedOrphan -and
        $gateNoForeignSpikeShape -and
        $gateNoExternalMounts
    )

    Write-Host "recovery_gate=workers:$gateWorkers|production:$gateProduction|accepted_volume:$gateAcceptedVolume|spike_files:$gateSpikeFiles|one_spike_shape:$gateOneSpikeShape|one_expected_orphan:$gateOneExpectedOrphan|no_foreign_spike_shape:$gateNoForeignSpikeShape|no_external_mounts:$gateNoExternalMounts|authorized:$noArgUnmountAuthorized"

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "wsl_noarg_unmount_recovery_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    $decision = if ($noArgUnmountAuthorized) { 'READY_FOR_WSL_NOARG_UNMOUNT_RECOVERY' } else { 'WSL_NOARG_UNMOUNT_RECOVERY_BLOCKED' }
    $noArgUnmountPerformed = $false
    $unmountEvidence = [ordered]@{ exit_code=$null; timed_out=$false; lines=@() }
    $stateAfter = $stateBefore

    if ($Apply) {
        $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'WSL no-arg unmount recovery requires elevated Administrator PowerShell.' }
        if (-not $noArgUnmountAuthorized) { throw 'Recovery safety gate is not authorized; refusing no-arg WSL unmount.' }

        Write-Host 'recovery_stage=no_arg_unmount_once'
        $noArgUnmountPerformed = $true
        $unmountEvidence = Invoke-NoArgUnmountBounded -TimeoutSeconds $UnmountTimeoutSeconds
        $unmountOutput = (@($unmountEvidence['lines']) -join ' | ')
        Write-Host "no_arg_unmount_evidence=exit:$($unmountEvidence['exit_code'])|timed_out:$($unmountEvidence['timed_out'])|output:$unmountOutput"
        Start-Sleep -Seconds 2

        Write-Host 'recovery_stage=post_unmount_authoritative_state'
        $stateAfter = Get-RecoveryState
        $workerAfterProbe = Get-WorkerContainerCount
        $productionAfterProbe = Get-ProductionClickHouseHealth
        $acceptedAfterProbe = Test-AcceptedVolumePresent

        $postOrphanCleared = [bool](@($stateAfter['spike_shape_candidates']).Count -eq 0 -and @($stateAfter['expected_orphan_candidates']).Count -eq 0)
        $postNoExternalMounts = [bool](@($stateAfter['external_mounts']).Count -eq 0)
        $postWorkersSafe = [bool]($workerAfterProbe -eq 0)
        $postProductionSafe = [bool]$productionAfterProbe['ready']
        $postAcceptedSafe = [bool]$acceptedAfterProbe
        $postAuthoritativeGo = [bool](
            -not $unmountEvidence['timed_out'] -and
            $postOrphanCleared -and
            $postNoExternalMounts -and
            $postWorkersSafe -and
            $postProductionSafe -and
            $postAcceptedSafe
        )
        Write-Host "post_recovery_gate=orphan_cleared:$postOrphanCleared|no_external_mounts:$postNoExternalMounts|workers:$postWorkersSafe|production:$postProductionSafe|accepted_volume:$postAcceptedSafe|go:$postAuthoritativeGo"
        $decision = if ($postAuthoritativeGo) { 'WSL_NOARG_UNMOUNT_RECOVERY_GO' } else { 'WSL_NOARG_UNMOUNT_RECOVERY_BLOCKED' }
    }

    $workerAfter = Get-WorkerContainerCount
    $productionAfter = Get-ProductionClickHouseHealth
    $acceptedAfter = Test-AcceptedVolumePresent

    $receipt = [ordered]@{
        decision=$decision
        expected_spike_virtual_bytes=$expectedSpikeVirtualBytes
        expected_orphan_label=$expectedOrphanLabel
        retained_spike_vhdx_all_exist=$allSpikeVhdxExist
        recovery_gate_authority='single_expected_labeled_orphan_and_zero_external_mounts'
        recovery_gate_workers=$gateWorkers
        recovery_gate_production=$gateProduction
        recovery_gate_accepted_volume=$gateAcceptedVolume
        recovery_gate_spike_files=$gateSpikeFiles
        recovery_gate_one_spike_shape=$gateOneSpikeShape
        recovery_gate_one_expected_orphan=$gateOneExpectedOrphan
        recovery_gate_no_foreign_spike_shape=$gateNoForeignSpikeShape
        recovery_gate_no_external_mounts=$gateNoExternalMounts
        no_arg_unmount_authorized=$noArgUnmountAuthorized
        no_arg_unmount_performed=$noArgUnmountPerformed
        no_arg_unmount_attempt_limit=1
        no_arg_unmount_exit=$unmountEvidence['exit_code']
        no_arg_unmount_timed_out=$unmountEvidence['timed_out']
        no_arg_unmount_output=@($unmountEvidence['lines'])
        spike_shape_candidate_count_before=@($stateBefore['spike_shape_candidates']).Count
        expected_orphan_candidate_count_before=@($stateBefore['expected_orphan_candidates']).Count
        foreign_spike_shape_candidate_count_before=@($stateBefore['foreign_spike_shape_candidates']).Count
        external_mount_count_before=@($stateBefore['external_mounts']).Count
        spike_shape_candidate_count_after=@($stateAfter['spike_shape_candidates']).Count
        expected_orphan_candidate_count_after=@($stateAfter['expected_orphan_candidates']).Count
        external_mount_count_after=@($stateAfter['external_mounts']).Count
        worker_container_count_before=$workerBefore
        worker_container_count_after=$workerAfter
        production_clickhouse_before_ready=$productionBefore['ready']
        production_clickhouse_after_ready=$productionAfter['ready']
        accepted_volume_before_present=$acceptedBefore
        accepted_volume_after_present=$acceptedAfter
        wsl_mount_performed=$false
        wsl_shutdown_performed=$false
        runtime_distro_unregister_performed=$false
        spike_vhdx_mutation_performed=$false
        production_clickhouse_restart_performed=$false
        production_clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        corpus_replay_performed=$false
    }
    $receiptPath = Join-Path $evidenceDir 'receipt.json'
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== WSL NO-ARG UNMOUNT RECOVERY RESULT ====='
    Write-Host "decision=$decision"
    Write-Host 'recovery_gate_authority=single_expected_labeled_orphan_and_zero_external_mounts'
    Write-Host "expected_orphan_label=$expectedOrphanLabel"
    Write-Host "spike_shape_candidate_count_before=$(@($stateBefore['spike_shape_candidates']).Count)"
    Write-Host "expected_orphan_candidate_count_before=$(@($stateBefore['expected_orphan_candidates']).Count)"
    Write-Host "foreign_spike_shape_candidate_count_before=$(@($stateBefore['foreign_spike_shape_candidates']).Count)"
    Write-Host "external_mount_count_before=$(@($stateBefore['external_mounts']).Count)"
    Write-Host "spike_shape_candidate_count_after=$(@($stateAfter['spike_shape_candidates']).Count)"
    Write-Host "expected_orphan_candidate_count_after=$(@($stateAfter['expected_orphan_candidates']).Count)"
    Write-Host "external_mount_count_after=$(@($stateAfter['external_mounts']).Count)"
    Write-Host "no_arg_unmount_authorized=$noArgUnmountAuthorized"
    Write-Host "no_arg_unmount_performed=$noArgUnmountPerformed"
    Write-Host 'no_arg_unmount_attempt_limit=1'
    Write-Host 'no_arg_unmount_exit_authority=evidence_only_post_lsblk_state_is_authoritative'
    Write-Host "worker_container_count_before=$workerBefore"
    Write-Host "worker_container_count_after=$workerAfter"
    Write-Host "production_clickhouse_before_ready=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_after_ready=$($productionAfter['ready'])"
    Write-Host "accepted_volume_before_present=$acceptedBefore"
    Write-Host "accepted_volume_after_present=$acceptedAfter"
    Write-Host 'wsl_mount_performed=False'
    Write-Host 'wsl_shutdown_performed=False'
    Write-Host 'runtime_distro_unregister_performed=False'
    Write-Host 'spike_vhdx_mutation_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'WSL_NOARG_UNMOUNT_RECOVERY_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
