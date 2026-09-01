[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$RuntimeDistro = 'MarkOrbit-ClickHouse-Spike',
    [int]$MountTimeoutSeconds = 30,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$diskSpecs = @(
    [ordered]@{ key='hot_cn'; path='D:\MarkOrbitData\spike\hot_cn_spike.vhdx'; mount='markorbit_hot_cn_spike' },
    [ordered]@{ key='hot_us'; path='D:\MarkOrbitData\spike\hot_us_spike.vhdx'; mount='markorbit_hot_us_spike' },
    [ordered]@{ key='hot_global'; path='D:\MarkOrbitData\spike\hot_global_spike.vhdx'; mount='markorbit_hot_global_spike' },
    [ordered]@{ key='warm'; path='E:\MarkOrbitData\spike\warm_spike.vhdx'; mount='markorbit_warm_spike' }
)

function Invoke-NativeText {
    param([Parameter(Mandatory = $true)][string]$Command,[Parameter(Mandatory = $true)][string[]]$Arguments,[switch]$AllowFailure)
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
    return [ordered]@{ exit_code=$exitCode; lines=@($rendered); timed_out=$false }
}

function Invoke-WslDiskCommandBounded {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('mount','unmount')][string]$Mode,
        [Parameter(Mandatory = $true)][string]$VhdxPath,
        [string]$MountName,
        [int]$TimeoutSeconds = 30
    )
    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $args = if ($Mode -eq 'mount') { @('--mount','--vhd',$VhdxPath,'--name',$MountName) } else { @('--unmount',$VhdxPath) }
        $argumentText = @($args | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + ($_.Replace('"','\"')) + '"' } else { $_ }
        }) -join ' '
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
        return [ordered]@{ exit_code=$exitCode; lines=@($lines); timed_out=$timedOut }
    }
    finally {
        [System.IO.File]::Delete($stdoutPath)
        [System.IO.File]::Delete($stderrPath)
    }
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

function Get-MountProbe([string]$MountName) {
    $target = "/mnt/wsl/$MountName"
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$RuntimeDistro,'-u','root','--','timeout','--signal=TERM','--kill-after=2s','8s','findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$target) -AllowFailure
    $text = (@($probe['lines']) -join ' ').Trim()
    return [ordered]@{
        ready=[bool]($probe['exit_code'] -eq 0 -and $text -match '^ext4\s')
        output=$text
        exit_code=$probe['exit_code']
    }
}

function Invoke-V2([switch]$ApplyV2) {
    $scriptPath = Join-Path $PSScriptRoot 'probe-dedicated-wsl-clickhouse-startup-v2.ps1'
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,'-ExpectedMainSha',$ExpectedMainSha)
    if ($ApplyV2) { $args += @('-Apply','-CleanupMounts') }
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe @args 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $lines = @($output | ForEach-Object { $_.ToString() })
    foreach ($line in $lines) { Write-Host $line }
    $decisionLine = @($lines | Where-Object { $_ -match '^decision=' } | Select-Object -Last 1)
    $decision = if ($decisionLine.Count -eq 1) { ($decisionLine[0] -replace '^decision=','').Trim() } else { $null }
    return [ordered]@{ exit_code=$exitCode; lines=$lines; decision=$decision }
}

try {
    Write-Host '===== DEDICATED WSL CLICKHOUSE STARTUP PROBE V3 ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Startup probe V3 must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    Write-Host 'probe_v3_stage=v2_preflight'
    $preflight = Invoke-V2
    if ($preflight['exit_code'] -ne 0 -or $preflight['decision'] -ne 'READY_FOR_NATIVE_STARTUP_PROBE_V2') {
        throw "V2 safety preflight did not authorize apply. decision=$($preflight['decision']) exit=$($preflight['exit_code'])"
    }

    $mountEvidence = @()
    $cleanupEvidence = @()
    $advisories = @()
    $nestedDecision = $null
    $decision = 'READY_FOR_NATIVE_STARTUP_PROBE_V3'

    if ($Apply) {
        $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Startup probe V3 requires elevated Administrator PowerShell.' }

        Write-Host 'probe_v3_stage=state_authoritative_mount'
        foreach ($spec in $diskSpecs) {
            $key = [string]$spec['key']
            Write-Host "probe_v3_step=mount_$key"
            $before = Get-MountProbe $spec['mount']
            $commandExit = $null
            $commandTimedOut = $false
            $commandOutput = ''
            if (-not $before['ready']) {
                $mount = Invoke-WslDiskCommandBounded -Mode mount -VhdxPath ([string]$spec['path']) -MountName ([string]$spec['mount']) -TimeoutSeconds $MountTimeoutSeconds
                $commandExit = $mount['exit_code']
                $commandTimedOut = $mount['timed_out']
                $commandOutput = (@($mount['lines']) -join ' | ')
                Start-Sleep -Seconds 1
            }
            $after = Get-MountProbe $spec['mount']
            $verified = [bool]$after['ready']
            $mountEvidence += [ordered]@{ key=$key; command_exit=$commandExit; command_timed_out=$commandTimedOut; state_verified=$verified; state=$after['output']; command_output=$commandOutput }
            Write-Host "mount_state=$key|command_exit=$commandExit|timed_out=$commandTimedOut|verified=$verified|state=$($after['output'])"
            if (-not $verified) { throw "WSL mount state verification failed for $key after command exit $commandExit`: $commandOutput" }
            if ($null -ne $commandExit -and $commandExit -ne 0) {
                $advisories += "MOUNT_COMMAND_EXIT_NONZERO_STATE_VERIFIED_$($key.ToUpperInvariant())"
            }
        }

        Write-Host 'probe_v3_stage=v2_apply'
        $v2ApplyResult = Invoke-V2 -ApplyV2
        $nestedDecision = $v2ApplyResult['decision']
        if ($v2ApplyResult['exit_code'] -ne 0) { throw "V2 apply process exited $($v2ApplyResult['exit_code'])." }

        Write-Host 'probe_v3_stage=state_authoritative_cleanup'
        $allDetached = $true
        foreach ($spec in $diskSpecs) {
            $key = [string]$spec['key']
            Write-Host "probe_v3_step=verify_unmount_$key"
            $beforeCleanup = Get-MountProbe $spec['mount']
            $commandExit = $null
            $commandTimedOut = $false
            $commandOutput = ''
            if ($beforeCleanup['ready']) {
                $unmount = Invoke-WslDiskCommandBounded -Mode unmount -VhdxPath ([string]$spec['path']) -TimeoutSeconds $MountTimeoutSeconds
                $commandExit = $unmount['exit_code']
                $commandTimedOut = $unmount['timed_out']
                $commandOutput = (@($unmount['lines']) -join ' | ')
                Start-Sleep -Seconds 1
            }
            $afterCleanup = Get-MountProbe $spec['mount']
            $detached = [bool](-not $afterCleanup['ready'])
            if (-not $detached) { $allDetached = $false }
            $cleanupEvidence += [ordered]@{ key=$key; command_exit=$commandExit; command_timed_out=$commandTimedOut; detached_verified=$detached; state=$afterCleanup['output']; command_output=$commandOutput }
            Write-Host "cleanup_state=$key|command_exit=$commandExit|timed_out=$commandTimedOut|detached=$detached|state=$($afterCleanup['output'])"
            if ($null -ne $commandExit -and $commandExit -ne 0 -and $detached) {
                $advisories += "UNMOUNT_COMMAND_EXIT_NONZERO_STATE_VERIFIED_$($key.ToUpperInvariant())"
            }
        }
        if (-not $allDetached) { $decision = 'NATIVE_STARTUP_PROBE_V3_CLEANUP_BLOCKED' }
        elseif ($nestedDecision) { $decision = $nestedDecision }
        else { $decision = 'NATIVE_STARTUP_PROBE_V3_BLOCKED' }
    }

    Write-Host '===== DEDICATED WSL CLICKHOUSE STARTUP PROBE V3 RESULT ====='
    Write-Host "decision=$decision"
    if ($nestedDecision) { Write-Host "nested_decision=$nestedDecision" }
    foreach ($advisory in $advisories) { Write-Host "advisory=$advisory" }
    Write-Host 'child_powershell_stderr_capture_safe=True'
    Write-Host 'v3_apply_switch_collision_safe=True'
    Write-Host 'mount_acceptance_authority=findmnt_ext4_state'
    Write-Host 'unmount_acceptance_authority=findmnt_detached_state'
    Write-Host 'runtime_distro_unregister_performed=False'
    Write-Host 'spike_vhdx_delete_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host 'DEDICATED_WSL_CLICKHOUSE_STARTUP_PROBE_V3_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
