[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [int]$MountTimeoutSeconds = 30,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$diskSpecs = @(
    [ordered]@{ key='hot_cn'; path='D:\MarkOrbitData\spike\hot_cn_spike.vhdx' },
    [ordered]@{ key='hot_us'; path='D:\MarkOrbitData\spike\hot_us_spike.vhdx' },
    [ordered]@{ key='hot_global'; path='D:\MarkOrbitData\spike\hot_global_spike.vhdx' },
    [ordered]@{ key='warm'; path='E:\MarkOrbitData\spike\warm_spike.vhdx' }
)

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

function Invoke-WslUnmountIdentityBounded {
    param(
        [Parameter(Mandatory = $true)][string]$VhdxPath,
        [Parameter(Mandatory = $true)][ValidateSet('raw','extended')][string]$Identity,
        [int]$TimeoutSeconds = 30
    )
    $allowedVhdxPaths = @($diskSpecs | ForEach-Object { [string]$_['path'] })
    if ($allowedVhdxPaths -notcontains $VhdxPath) { throw "Refusing WSL detach outside retained spike VHDX scope: $VhdxPath" }
    if ($VhdxPath -match '[\s"]') { throw "Retained spike VHDX path must remain whitespace/quote free for exact WSL detach: $VhdxPath" }

    $detachPath = if ($Identity -eq 'extended') { '\\?\' + $VhdxPath } else { $VhdxPath }
    if ($detachPath -match '[\s"]') { throw "Resolved WSL detach identity must remain whitespace/quote free: $detachPath" }

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $argumentText = '--unmount ' + $detachPath
        $process = Start-Process -FilePath 'wsl.exe' -ArgumentList $argumentText -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
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
        return [ordered]@{ identity=$Identity; detach_path=$detachPath; exit_code=$exitCode; timed_out=$timedOut; lines=@($lines) }
    }
    finally {
        [System.IO.File]::Delete($stdoutPath)
        [System.IO.File]::Delete($stderrPath)
    }
}

function Get-WslVersionEvidence {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& wsl.exe --version 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    return [ordered]@{
        exit_code=$exitCode
        lines=@($output | ForEach-Object { $_.ToString() })
    }
}

function Get-ReceiptValue([string[]]$Lines,[string]$Key) {
    $matches = @($Lines | Where-Object { $_ -match ('^' + [regex]::Escape($Key) + '=') } | Select-Object -Last 1)
    if ($matches.Count -ne 1) { return $null }
    return ($matches[0] -replace ('^' + [regex]::Escape($Key) + '='),'').Trim()
}

function Invoke-V2([switch]$ApplyV2) {
    $scriptPath = Join-Path $PSScriptRoot 'run-dedicated-wsl-clickhouse-full-acceptance-v2.ps1'
    $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,'-ExpectedMainSha',$ExpectedMainSha)
    if ($ApplyV2) { $childArgs += @('-Apply','-CleanupMounts') }
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe @childArgs 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $lines = @($output | ForEach-Object { $_.ToString() })
    foreach ($line in $lines) { Write-Host $line }
    return [ordered]@{
        exit_code=$exitCode
        lines=@($lines)
        decision=Get-ReceiptValue $lines 'decision'
        runtime_stage=Get-ReceiptValue $lines 'runtime_stage'
        server_stopped=Get-ReceiptValue $lines 'server_stopped'
        production_after_ready=Get-ReceiptValue $lines 'production_clickhouse_after_ready'
        accepted_volume_after_present=Get-ReceiptValue $lines 'accepted_volume_after_present'
        worker_count_after=Get-ReceiptValue $lines 'worker_container_count_after'
    }
}

try {
    Write-Host '===== DEDICATED WSL CLICKHOUSE FULL ACCEPTANCE V3 ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Full acceptance V3 must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $wslVersion = Get-WslVersionEvidence
    Write-Host "wsl_version_probe_exit=$($wslVersion['exit_code'])"
    foreach ($line in @($wslVersion['lines'])) { Write-Host "wsl_version_evidence=$line" }

    $firstDecision = $null
    $retryDecision = $null
    $finalDecision = 'READY_FOR_DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V3'
    $staleRecoveryPerformed = $false
    $recoveryEvidence = @()
    $recoveryGateDecision = $false
    $recoveryGateStage = $false
    $recoveryGateServerStopped = $false
    $recoveryGateProduction = $false
    $recoveryGateAcceptedVolume = $false
    $recoveryGateWorkers = $false

    if (-not $Apply) {
        Write-Host 'acceptance_v3_stage=v2_preflight'
        $preflight = Invoke-V2
        if ($preflight['exit_code'] -ne 0) { throw "V2 preflight process exited $($preflight['exit_code'])." }
        $firstDecision = $preflight['decision']
        $finalDecision = $firstDecision
    }
    else {
        $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Full acceptance V3 requires elevated Administrator PowerShell.' }

        Write-Host 'acceptance_v3_stage=v2_first_attempt'
        $first = Invoke-V2 -ApplyV2
        if ($first['exit_code'] -ne 0) { throw "V2 first attempt process exited $($first['exit_code'])." }
        $firstDecision = $first['decision']
        $finalDecision = $firstDecision

        $recoveryGateDecision = [bool]($firstDecision -eq 'WSL_CLICKHOUSE_SPIKE_BLOCKED')
        $recoveryGateStage = [bool]($first['runtime_stage'] -eq 'mount_external_disks')
        $recoveryGateServerStopped = [bool]($first['server_stopped'] -eq 'True')
        $recoveryGateProduction = [bool]($first['production_after_ready'] -eq 'True')
        $recoveryGateAcceptedVolume = [bool]($first['accepted_volume_after_present'] -eq 'True')
        $recoveryGateWorkers = [bool]($first['worker_count_after'] -eq '0')
        $recoverableMountFailure = [bool](
            $recoveryGateDecision -and
            $recoveryGateStage -and
            $recoveryGateServerStopped -and
            $recoveryGateProduction -and
            $recoveryGateAcceptedVolume -and
            $recoveryGateWorkers
        )
        Write-Host "acceptance_v3_recovery_gate=decision:$recoveryGateDecision|stage:$recoveryGateStage|server_stopped:$recoveryGateServerStopped|production:$recoveryGateProduction|accepted_volume:$recoveryGateAcceptedVolume|workers:$recoveryGateWorkers|authorized:$recoverableMountFailure"

        if ($recoverableMountFailure) {
            Write-Host 'acceptance_v3_stage=stale_attachment_recovery'
            $staleRecoveryPerformed = $true
            foreach ($spec in $diskSpecs) {
                $key = [string]$spec['key']
                foreach ($identity in @('raw','extended')) {
                    Write-Host "acceptance_v3_step=exact_unmount_${key}_$identity"
                    $result = Invoke-WslUnmountIdentityBounded -VhdxPath ([string]$spec['path']) -Identity $identity -TimeoutSeconds $MountTimeoutSeconds
                    $outputText = (@($result['lines']) -join ' | ')
                    $recoveryEvidence += [ordered]@{ key=$key; identity=$identity; detach_path=$result['detach_path']; exit_code=$result['exit_code']; timed_out=$result['timed_out']; output=$outputText }
                    Write-Host "stale_recovery=$key|identity=$identity|detach_path=$($result['detach_path'])|exit=$($result['exit_code'])|timed_out=$($result['timed_out'])|output=$outputText"
                    if ($result['timed_out']) { throw "Timed out reconciling stale WSL attachment for $key identity=$identity." }
                }
            }
            Start-Sleep -Seconds 2

            Write-Host 'acceptance_v3_stage=v2_retry_once'
            $retry = Invoke-V2 -ApplyV2
            if ($retry['exit_code'] -ne 0) { throw "V2 retry process exited $($retry['exit_code'])." }
            $retryDecision = $retry['decision']
            $finalDecision = $retryDecision
        }
    }

    Write-Host '===== DEDICATED WSL CLICKHOUSE FULL ACCEPTANCE V3 RESULT ====='
    Write-Host "decision=$finalDecision"
    Write-Host "first_decision=$firstDecision"
    if ($retryDecision) { Write-Host "retry_decision=$retryDecision" }
    Write-Host "stale_attachment_recovery_performed=$staleRecoveryPerformed"
    Write-Host 'recovery_gate_authority=stable_ascii_v2_receipt'
    Write-Host 'recovery_unmount_argument_authority=dual_raw_extended_exact_retained_vhdx_identity'
    Write-Host 'recovery_unmount_exit_authority=evidence_only_retry_v2_state_is_authoritative'
    Write-Host "recovery_gate_decision=$recoveryGateDecision"
    Write-Host "recovery_gate_stage=$recoveryGateStage"
    Write-Host "recovery_gate_server_stopped=$recoveryGateServerStopped"
    Write-Host "recovery_gate_production=$recoveryGateProduction"
    Write-Host "recovery_gate_accepted_volume=$recoveryGateAcceptedVolume"
    Write-Host "recovery_gate_workers=$recoveryGateWorkers"
    Write-Host 'stale_attachment_recovery_scope=retained_spike_vhdx_only'
    Write-Host 'stale_attachment_identity_attempts_per_vhdx=2'
    Write-Host 'stale_attachment_retry_limit=1'
    Write-Host 'wsl_shutdown_performed=False'
    Write-Host 'runtime_distro_unregister_performed=False'
    Write-Host 'spike_vhdx_delete_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host 'DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V3_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
