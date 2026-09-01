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

function Invoke-WslUnmountBounded {
    param(
        [Parameter(Mandatory = $true)][string]$VhdxPath,
        [int]$TimeoutSeconds = 30
    )
    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $argumentText = '--unmount "' + ($VhdxPath.Replace('"','\"')) + '"'
        $process = Start-Process -FilePath 'wsl.exe' -ArgumentList $argumentText -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $completed = $process.WaitForExit($TimeoutSeconds * 1000)
        $timedOut = -not $completed
        if ($timedOut) {
            try { $process.Kill() } catch { }
            try { $process.WaitForExit() } catch { }
        }
        $lines = @()
        if (Test-Path -LiteralPath $stdoutPath) { $lines += @(Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue) }
        if (Test-Path -LiteralPath $stderrPath) { $lines += @(Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue) }
        $exitCode = if ($timedOut) { 124 } else { $process.ExitCode }
        return [ordered]@{ exit_code=$exitCode; timed_out=$timedOut; lines=@($lines) }
    }
    finally {
        [System.IO.File]::Delete($stdoutPath)
        [System.IO.File]::Delete($stderrPath)
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
        stale_attachment=[bool](@($lines | Where-Object { $_ -match 'WSL_E_DISK_ALREADY_MOUNTED' }).Count -gt 0)
    }
}

try {
    Write-Host '===== DEDICATED WSL CLICKHOUSE FULL ACCEPTANCE V3 ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Full acceptance V3 must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $firstDecision = $null
    $retryDecision = $null
    $finalDecision = 'READY_FOR_DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V3'
    $staleRecoveryPerformed = $false
    $recoveryEvidence = @()

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

        $recoverableStaleAttachment = [bool](
            $firstDecision -eq 'WSL_CLICKHOUSE_SPIKE_BLOCKED' -and
            $first['runtime_stage'] -eq 'mount_external_disks' -and
            $first['stale_attachment'] -and
            $first['server_stopped'] -eq 'True' -and
            $first['production_after_ready'] -eq 'True' -and
            $first['accepted_volume_after_present'] -eq 'True' -and
            $first['worker_count_after'] -eq '0'
        )

        if ($recoverableStaleAttachment) {
            Write-Host 'acceptance_v3_stage=stale_attachment_recovery'
            $staleRecoveryPerformed = $true
            foreach ($spec in $diskSpecs) {
                $key = [string]$spec['key']
                Write-Host "acceptance_v3_step=exact_unmount_$key"
                $result = Invoke-WslUnmountBounded -VhdxPath ([string]$spec['path']) -TimeoutSeconds $MountTimeoutSeconds
                $outputText = (@($result['lines']) -join ' | ')
                $recoveryEvidence += [ordered]@{ key=$key; exit_code=$result['exit_code']; timed_out=$result['timed_out']; output=$outputText }
                Write-Host "stale_recovery=$key|exit=$($result['exit_code'])|timed_out=$($result['timed_out'])|output=$outputText"
                if ($result['timed_out']) { throw "Timed out reconciling stale WSL attachment for $key." }
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
    Write-Host 'stale_attachment_recovery_scope=retained_spike_vhdx_only'
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
