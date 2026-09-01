[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

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

function Invoke-ReadOnlyDiskProfile {
    $scriptPath = Join-Path $PSScriptRoot 'profile-wsl-external-disk-state.ps1'
    $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,'-ExpectedMainSha',$ExpectedMainSha)
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
        orphan_count=Get-ReceiptValue $lines 'orphan_ext4_1g_candidate_count'
        mnt_wsl_mount_count=Get-ReceiptValue $lines 'mnt_wsl_mount_count'
        worker_count_after=Get-ReceiptValue $lines 'worker_container_count_after'
        production_after_ready=Get-ReceiptValue $lines 'production_clickhouse_after_ready'
        accepted_volume_after_present=Get-ReceiptValue $lines 'accepted_volume_after_present'
        no_arg_unmount_authorized=Get-ReceiptValue $lines 'no_arg_unmount_authorized'
        wsl_mount_performed=Get-ReceiptValue $lines 'wsl_mount_performed'
        wsl_unmount_performed=Get-ReceiptValue $lines 'wsl_unmount_performed'
        wsl_shutdown_performed=Get-ReceiptValue $lines 'wsl_shutdown_performed'
    }
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
    $finalDecision = 'READY_FOR_DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V3'
    $postIncidentProfileInvoked = $false
    $resumeGateReady = $false
    $resumeGateDecision = $false
    $resumeGateOrphanFree = $false
    $resumeGateMntWslClear = $false
    $resumeGateProduction = $false
    $resumeGateAcceptedVolume = $false
    $resumeGateWorkers = $false
    $resumeGateProfileReadOnly = $false
    $profileOrphanCount = $null
    $profileMntWslMountCount = $null
    $v2SingleAttemptPerformed = $false

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

        Write-Host 'acceptance_v3_stage=post_incident_read_only_resume_gate'
        $postIncidentProfileInvoked = $true
        $profile = Invoke-ReadOnlyDiskProfile
        if ($profile['exit_code'] -ne 0) { throw "Read-only WSL external disk profile exited $($profile['exit_code'])." }

        $profileOrphanCount = $profile['orphan_count']
        $profileMntWslMountCount = $profile['mnt_wsl_mount_count']
        $resumeGateDecision = [bool]($profile['decision'] -eq 'WSL_EXTERNAL_DISK_STATE_PROFILE_DONE')
        $resumeGateOrphanFree = [bool]($profileOrphanCount -eq '0')
        $resumeGateMntWslClear = [bool]($profileMntWslMountCount -eq '0')
        $resumeGateProduction = [bool]($profile['production_after_ready'] -eq 'True')
        $resumeGateAcceptedVolume = [bool]($profile['accepted_volume_after_present'] -eq 'True')
        $resumeGateWorkers = [bool]($profile['worker_count_after'] -eq '0')
        $resumeGateProfileReadOnly = [bool](
            $profile['no_arg_unmount_authorized'] -eq 'False' -and
            $profile['wsl_mount_performed'] -eq 'False' -and
            $profile['wsl_unmount_performed'] -eq 'False' -and
            $profile['wsl_shutdown_performed'] -eq 'False'
        )
        $resumeGateReady = [bool](
            $resumeGateDecision -and
            $resumeGateOrphanFree -and
            $resumeGateMntWslClear -and
            $resumeGateProduction -and
            $resumeGateAcceptedVolume -and
            $resumeGateWorkers -and
            $resumeGateProfileReadOnly
        )
        Write-Host "acceptance_v3_resume_gate=decision:$resumeGateDecision|orphan_free:$resumeGateOrphanFree|mnt_wsl_clear:$resumeGateMntWslClear|production:$resumeGateProduction|accepted_volume:$resumeGateAcceptedVolume|workers:$resumeGateWorkers|profile_read_only:$resumeGateProfileReadOnly|authorized:$resumeGateReady"
        Write-Host "acceptance_v3_resume_profile=orphan_ext4_1g_candidate_count:$profileOrphanCount|mnt_wsl_mount_count:$profileMntWslMountCount"

        if (-not $resumeGateReady) {
            $finalDecision = 'WSL_CLICKHOUSE_SPIKE_BLOCKED'
        }
        else {
            Write-Host 'acceptance_v3_stage=v2_single_attempt'
            $v2SingleAttemptPerformed = $true
            $first = Invoke-V2 -ApplyV2
            if ($first['exit_code'] -ne 0) { throw "V2 single attempt process exited $($first['exit_code'])." }
            $firstDecision = $first['decision']
            $finalDecision = $firstDecision
        }
    }

    Write-Host '===== DEDICATED WSL CLICKHOUSE FULL ACCEPTANCE V3 RESULT ====='
    Write-Host "decision=$finalDecision"
    Write-Host "first_decision=$firstDecision"
    Write-Host "post_incident_profile_invoked=$postIncidentProfileInvoked"
    Write-Host "resume_gate_ready=$resumeGateReady"
    Write-Host "resume_gate_decision=$resumeGateDecision"
    Write-Host "resume_gate_orphan_free=$resumeGateOrphanFree"
    Write-Host "resume_gate_mnt_wsl_clear=$resumeGateMntWslClear"
    Write-Host "resume_gate_production=$resumeGateProduction"
    Write-Host "resume_gate_accepted_volume=$resumeGateAcceptedVolume"
    Write-Host "resume_gate_workers=$resumeGateWorkers"
    Write-Host "resume_gate_profile_read_only=$resumeGateProfileReadOnly"
    Write-Host "resume_profile_orphan_ext4_1g_candidate_count=$profileOrphanCount"
    Write-Host "resume_profile_mnt_wsl_mount_count=$profileMntWslMountCount"
    Write-Host "v2_single_attempt_performed=$v2SingleAttemptPerformed"
    Write-Host 'automatic_stale_attachment_recovery_authorized=False'
    Write-Host 'automatic_stale_attachment_recovery_performed=False'
    Write-Host 'automatic_stale_attachment_retry_limit=0'
    Write-Host 'no_arg_wsl_unmount_authorized=False'
    Write-Host 'no_arg_wsl_unmount_performed=False'
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
