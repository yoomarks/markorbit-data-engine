[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,

    [Parameter(Mandatory = $true)]
    [string]$OperatorGoToken,

    [switch]$Apply,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:RecoveryIssue = 517
$script:BaseMainSha = '6d9d160ee7ad4714b5143ea2774a94605b47da97'
$script:OperatorGoCommentId = '5533302373'
$script:ExpectedOperatorGoToken = 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_GO_ISSUE_517_COMMENT_5533302373'
$script:ReceiptVersion = 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_V1'
$script:ExpectedDockerVhdx = 'D:\DockerData\DockerDesktopWSL\disk\docker_data.vhdx'
$script:ExpectedDockerVhdxBytes = [int64]852286767104
$script:ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:ExpectedWarmVhdx = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ExpectedWarmVhdxBytes = [int64]13895729152
$script:AcceptedVolume = 'markorbit-data-engine_clickhouse_data'
$script:ExpectedClickHouseVersion = '24.8.14.39'
$script:RawConsumers = @('api','worker','mark-image-worker','qcc-acquisition')
$script:DockerDesktopExe = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
$script:AllowedRecoveryFiles = @(
    'scripts/run-docker-desktop-elevated-wsl-attach-recovery.ps1',
    'tests/test_docker_desktop_elevated_wsl_attach_recovery_contract.py',
    '.github/workflows/docker-desktop-elevated-wsl-attach-recovery-runtime.yml'
)

function Write-ContractHeader {
    Write-Host '===== DOCKER DESKTOP ELEVATED WSL ATTACH RECOVERY ====='
    Write-Host "recovery_issue=$($script:RecoveryIssue)"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host 'docker_desktop_graceful_stop_authorized=True'
    Write-Host 'docker_desktop_elevated_launch_authorized=True'
    Write-Host 'wsl_shutdown_authorized=False'
    Write-Host 'wsl_unmount_authorized=False'
    Write-Host 'wsl_mount_authorized=False'
    Write-Host 'docker_reset_authorized=False'
    Write-Host 'docker_reinstall_authorized=False'
    Write-Host 'vhdx_delete_authorized=False'
    Write-Host 'vhdx_move_authorized=False'
    Write-Host 'vhdx_resize_authorized=False'
    Write-Host 'vhdx_compact_authorized=False'
    Write-Host 'distro_unregister_authorized=False'
    Write-Host 'volume_delete_authorized=False'
    Write-Host 'source_clickhouse_mutation_authorized=False'
    Write-Host 'cn_warm_remount_authorized=False'
    Write-Host 'cn_warm_provisioning_authorized=False'
    Write-Host 'cn_data_transfer_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'source_cleanup_authorized=False'
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_bulk_authorized=False'
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-ExactMain([string]$Phase) {
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git fetch origin main failed.' }
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (& git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (& git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw "Exact main drift during $Phase." }
    if (& git status --porcelain) { throw "Working tree not clean during $Phase." }
}

function Assert-RecoveryProvenance {
    & git merge-base --is-ancestor $script:BaseMainSha $ExpectedMainSha
    if ($LASTEXITCODE -ne 0) { throw 'Recovery base main is not ancestor of expected main.' }
    $changed = @(& git diff --name-only "$($script:BaseMainSha)..$ExpectedMainSha" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedRecoveryFiles })
    $missing = @($script:AllowedRecoveryFiles | Where-Object { $_ -notin $changed })
    Write-Host "recovery_changed_file_count=$($changed.Count)"
    Write-Host "recovery_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "recovery_missing_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'Recovery tooling changed outside exact 3-file boundary.'
    }
}

function Get-FileState([string]$Path,[int64]$ExpectedBytes,[string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ([int64]$item.Length -ne $ExpectedBytes) { throw "$Label length drift: $($item.Length) expected $ExpectedBytes" }
    return [ordered]@{
        path = $item.FullName
        length_bytes = [int64]$item.Length
        last_write_utc = $item.LastWriteTimeUtc.ToString('o')
    }
}

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutMs = 10000,
        [string]$WorkingDirectory = $repoRoot
    )
    $stdout = Join-Path $env:TEMP ("mo_recovery_stdout_" + [guid]::NewGuid().ToString('N') + '.txt')
    $stderr = Join-Path $env:TEMP ("mo_recovery_stderr_" + [guid]::NewGuid().ToString('N') + '.txt')
    $process = $null
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $finished = $process.WaitForExit($TimeoutMs)
        if (-not $finished) {
            try { $process.Kill() } catch {}
            try { $process.WaitForExit() } catch {}
            return [ordered]@{ timed_out=$true; exit_code=$null; stdout=''; stderr=''; arguments=@($Arguments) }
        }
        $out = if (Test-Path -LiteralPath $stdout) { (Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue).Trim() } else { '' }
        $err = if (Test-Path -LiteralPath $stderr) { (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue).Trim() } else { '' }
        return [ordered]@{ timed_out=$false; exit_code=[int]$process.ExitCode; stdout=$out; stderr=$err; arguments=@($Arguments) }
    }
    finally {
        Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
        if ($process) { $process.Dispose() }
    }
}

function Get-DockerDesktopProcesses {
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -in @('Docker Desktop','com.docker.backend','com.docker.build')
    } | Select-Object ProcessName,Id,StartTime)
}

function Invoke-GracefulDesktopStop {
    Write-Host 'stage=graceful_docker_desktop_stop'
    $result = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('desktop','stop','--timeout','30') -TimeoutMs 35000
    Write-Host "desktop_stop_timed_out=$($result.timed_out)"
    Write-Host "desktop_stop_exit_code=$($result.exit_code)"
    if ($result.stderr) { Write-Host "desktop_stop_stderr=$($result.stderr)" }
    if ($result.timed_out -or $result.exit_code -ne 0) { throw 'Graceful Docker Desktop stop did not complete successfully.' }

    $deadline = (Get-Date).AddSeconds(30)
    do {
        $procs = @(Get-DockerDesktopProcesses)
        if ($procs.Count -eq 0) { break }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    $remaining = @(Get-DockerDesktopProcesses)
    Write-Host "docker_desktop_process_count_after_stop=$($remaining.Count)"
    if ($remaining.Count -ne 0) { throw 'Docker Desktop/backend processes remain after graceful stop.' }
}

function Start-ElevatedDockerDesktop {
    Write-Host 'stage=elevated_docker_desktop_launch'
    if (-not (Test-Path -LiteralPath $script:DockerDesktopExe -PathType Leaf)) { throw "Docker Desktop executable missing: $($script:DockerDesktopExe)" }
    $started = Start-Process -FilePath $script:DockerDesktopExe -Verb RunAs -PassThru
    if (-not $started) { throw 'Elevated Docker Desktop launch returned no process.' }
    Write-Host "elevated_launcher_pid=$($started.Id)"
    $started.Dispose()
}

function Wait-DockerEngine {
    Write-Host 'stage=bounded_docker_engine_health_wait'
    $deadline = (Get-Date).AddSeconds(90)
    $attempt = 0
    do {
        $attempt++
        $probe = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('version','--format','{{.Server.Version}}') -TimeoutMs 5000
        Write-Host "docker_engine_probe_attempt=$attempt|timed_out=$($probe.timed_out)|exit_code=$($probe.exit_code)|server_version=$($probe.stdout)"
        if (-not $probe.timed_out -and $probe.exit_code -eq 0 -and -not [string]::IsNullOrWhiteSpace($probe.stdout)) {
            return $probe.stdout.Trim()
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw 'Docker Engine did not become responsive within bounded recovery window.'
}

function Assert-RawConsumersStopped {
    foreach ($service in $script:RawConsumers) {
        $probe = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('compose','--profile','mark-image','--profile','qcc','ps','-q',$service) -TimeoutMs 10000
        if ($probe.timed_out -or $probe.exit_code -ne 0) { throw "Unable to inspect raw consumer $service." }
        $ids = @($probe.stdout -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        Write-Host "raw_consumer_service=$service running_count=$($ids.Count)"
        if ($ids.Count -ne 0) { throw "Raw consumer unexpectedly running after Docker recovery: $service" }
    }
}

function Assert-AcceptedClickHouseSource {
    Write-Host 'stage=accepted_source_clickhouse_health'
    $volume = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('volume','inspect',$script:AcceptedVolume,'--format','{{.Name}}') -TimeoutMs 10000
    if ($volume.timed_out -or $volume.exit_code -ne 0 -or $volume.stdout.Trim() -ne $script:AcceptedVolume) {
        throw 'Accepted ClickHouse named volume is not available after Docker recovery.'
    }
    Write-Host "accepted_volume_ready=$($script:AcceptedVolume)"

    $idProbe = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('compose','ps','-q','clickhouse') -TimeoutMs 10000
    if ($idProbe.timed_out -or $idProbe.exit_code -ne 0 -or [string]::IsNullOrWhiteSpace($idProbe.stdout)) { throw 'Source ClickHouse container is not running.' }
    $clickhouseId = ($idProbe.stdout -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1).Trim()
    Write-Host "source_clickhouse_container_id=$clickhouseId"

    $deadline = (Get-Date).AddSeconds(60)
    $health = ''
    do {
        $healthProbe = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$clickhouseId) -TimeoutMs 10000
        if (-not $healthProbe.timed_out -and $healthProbe.exit_code -eq 0) { $health = $healthProbe.stdout.Trim() }
        Write-Host "source_clickhouse_health=$health"
        if ($health -eq 'healthy' -or $health -eq 'running') { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if ($health -ne 'healthy') { throw "Source ClickHouse did not reach healthy state; final=$health" }

    $version = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -TimeoutMs 20000
    if ($version.timed_out -or $version.exit_code -ne 0) { throw 'Unable to query source ClickHouse version.' }
    $actualVersion = $version.stdout.Trim()
    Write-Host "source_clickhouse_version=$actualVersion"
    if ($actualVersion -ne $script:ExpectedClickHouseVersion) { throw "Source ClickHouse version drift: $actualVersion" }

    $mounts = Invoke-BoundedProcess -FilePath 'docker.exe' -Arguments @('inspect','--format','{{range .Mounts}}{{printf "%s|%s|%s\n" .Type .Name .Destination}}{{end}}',$clickhouseId) -TimeoutMs 10000
    if ($mounts.timed_out -or $mounts.exit_code -ne 0) { throw 'Unable to inspect source ClickHouse mounts.' }
    $expectedMount = "volume|$($script:AcceptedVolume)|/var/lib/clickhouse"
    $mountLines = @($mounts.stdout -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    Write-Host "accepted_source_mount=$expectedMount"
    if ($expectedMount -notin $mountLines) { throw 'Source ClickHouse is not mounted from the accepted named volume.' }
}

Write-ContractHeader

if ($ContractOnly) {
    Write-Host 'contract_only=True'
    Write-Host "expected_go_token=$($script:ExpectedOperatorGoToken)"
    Write-Host 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_CONTRACT_OK'
    Pop-Location
    exit 0
}

$evidenceDir = $null
$receiptPath = $null
$state = [ordered]@{
    version = $script:ReceiptVersion
    recovery_issue = $script:RecoveryIssue
    operator_go_comment_id = $script:OperatorGoCommentId
    operator_go_token = $OperatorGoToken
    engine_sha = $ExpectedMainSha.ToLowerInvariant()
    apply_requested = [bool]$Apply
    graceful_desktop_stop_performed = $false
    elevated_desktop_launch_performed = $false
    docker_engine_recovered = $false
    accepted_volume_verified = $false
    source_clickhouse_verified = $false
    raw_consumers_verified_stopped = $false
    wsl_shutdown_performed = $false
    wsl_unmount_performed = $false
    wsl_mount_performed = $false
    docker_reset_performed = $false
    docker_reinstall_performed = $false
    vhdx_mutation_performed = $false
    volume_delete_performed = $false
    source_clickhouse_mutation_performed = $false
    cn_warm_remount_performed = $false
    cn_warm_provisioning_performed = $false
    cn_data_transfer_performed = $false
    cn_warm_move_performed = $false
    source_cleanup_performed = $false
    cn_replay_performed = $false
    us_bulk_performed = $false
    decision = $null
    next_gate = $null
    last_error = $null
}

try {
    if (-not (Test-IsAdministrator)) { throw 'Administrator PowerShell is required for the elevated recovery probe.' }
    if ($OperatorGoToken -ne $script:ExpectedOperatorGoToken) { throw 'Operator GO token mismatch.' }
    Assert-ExactMain 'entry'
    Assert-RecoveryProvenance

    $dockerBefore = Get-FileState $script:ExpectedDockerVhdx $script:ExpectedDockerVhdxBytes 'Docker data VHDX'
    $fRecovery = Get-FileState $script:ExpectedFRecoveryVhdx $script:ExpectedFRecoveryBytes 'Retained F recovery VHDX'
    $warmBefore = Get-FileState $script:ExpectedWarmVhdx $script:ExpectedWarmVhdxBytes 'CN Warm VHDX'
    if (-not (Test-Path -LiteralPath $script:DockerDesktopExe -PathType Leaf)) { throw "Docker Desktop executable missing: $($script:DockerDesktopExe)" }
    Write-Host "docker_desktop_exe=$($script:DockerDesktopExe)"
    Write-Host "docker_vhdx_length_bytes=$($dockerBefore.length_bytes)"
    Write-Host "f_recovery_vhdx_length_bytes=$($fRecovery.length_bytes)"
    Write-Host "warm_vhdx_length_bytes=$($warmBefore.length_bytes)"

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot "reports\docker_wsl_attach_elevated_recovery_$stamp"
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
    $receiptPath = Join-Path $evidenceDir 'docker_wsl_attach_elevated_recovery.json'
    Write-Host "evidence_directory=$evidenceDir"

    if (-not $Apply) {
        $state.decision = 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_READY_FOR_APPLY'
        $state.next_gate = 'EXPLICIT_APPLY_REQUIRED'
        $state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
        Write-Host "decision=$($state.decision)"
        Write-Host "receipt_path=$receiptPath"
        Pop-Location
        exit 0
    }

    Invoke-GracefulDesktopStop
    $state.graceful_desktop_stop_performed = $true

    # Re-prove protected files before elevated launch. Docker's own data VHDX may legitimately change after launch,
    # so only exact presence/identity is frozen before the launch; F recovery and Warm remain untouched throughout.
    [void](Get-FileState $script:ExpectedDockerVhdx $script:ExpectedDockerVhdxBytes 'Docker data VHDX after graceful stop')
    [void](Get-FileState $script:ExpectedFRecoveryVhdx $script:ExpectedFRecoveryBytes 'Retained F recovery VHDX after graceful stop')
    [void](Get-FileState $script:ExpectedWarmVhdx $script:ExpectedWarmVhdxBytes 'CN Warm VHDX after graceful stop')

    Start-ElevatedDockerDesktop
    $state.elevated_desktop_launch_performed = $true

    $serverVersion = Wait-DockerEngine
    $state.docker_engine_recovered = $true
    Write-Host "docker_server_version=$serverVersion"

    Assert-RawConsumersStopped
    $state.raw_consumers_verified_stopped = $true
    Assert-AcceptedClickHouseSource
    $state.accepted_volume_verified = $true
    $state.source_clickhouse_verified = $true
    Assert-RawConsumersStopped

    [void](Get-FileState $script:ExpectedFRecoveryVhdx $script:ExpectedFRecoveryBytes 'Retained F recovery VHDX final')
    $warmFinal = Get-FileState $script:ExpectedWarmVhdx $script:ExpectedWarmVhdxBytes 'CN Warm VHDX final'
    if ($warmFinal.last_write_utc -ne $warmBefore.last_write_utc) { throw 'CN Warm VHDX changed during Docker recovery probe.' }

    Assert-ExactMain 'final'
    $state.decision = 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_SUCCEEDED'
    $state.next_gate = 'FRESH_READ_ONLY_CN_WARM_ATTACHMENT_GATE'
    $state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host "decision=$($state.decision)"
    Write-Host "next_gate=$($state.next_gate)"
    Write-Host 'graceful_desktop_stop_performed=True'
    Write-Host 'elevated_desktop_launch_performed=True'
    Write-Host 'docker_engine_recovered=True'
    Write-Host 'accepted_volume_verified=True'
    Write-Host 'source_clickhouse_verified=True'
    Write-Host 'raw_consumers_verified_stopped=True'
    Write-Host 'wsl_shutdown_performed=False'
    Write-Host 'wsl_unmount_performed=False'
    Write-Host 'wsl_mount_performed=False'
    Write-Host 'docker_reset_performed=False'
    Write-Host 'docker_reinstall_performed=False'
    Write-Host 'vhdx_mutation_performed=False'
    Write-Host 'volume_delete_performed=False'
    Write-Host 'source_clickhouse_mutation_performed=False'
    Write-Host 'cn_warm_remount_performed=False'
    Write-Host 'cn_warm_provisioning_performed=False'
    Write-Host 'cn_data_transfer_performed=False'
    Write-Host 'cn_warm_move_performed=False'
    Write-Host 'source_cleanup_performed=False'
    Write-Host 'cn_replay_performed=False'
    Write-Host 'us_bulk_performed=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_DONE'
}
catch {
    $state.last_error = $_.Exception.Message
    $state.decision = 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_FAILED'
    $state.next_gate = 'BLOCKED_FOR_INFRASTRUCTURE_RECOVERY_REVIEW'
    if ($receiptPath) {
        try { $state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8 } catch {}
    }
    Write-Host "decision=$($state.decision)"
    Write-Host "next_gate=$($state.next_gate)"
    Write-Host "last_error=$($state.last_error)"
    if ($receiptPath) { Write-Host "receipt_path=$receiptPath" }
    Write-Host 'DOCKER_WSL_ATTACH_ELEVATED_RECOVERY_FAILED'
    Pop-Location
    exit 1
}

Pop-Location
