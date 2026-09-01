[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$RuntimeDistro = 'MarkOrbit-ClickHouse-Spike',
    [string]$RuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$ClickHouseVersion = '24.8.14.39',
    [int]$SpikeHttpPort = 18123,
    [int]$SpikeNativePort = 19000,
    [int]$RuntimeTimeoutSeconds = 20,
    [int]$MountTimeoutSeconds = 30,
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply,
    [switch]$CleanupMounts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$InsertBatchCount = 24
$RowsPerBatch = 100
$runtimeInstallDir = '/opt/markorbit-clickhouse-full-acceptance'
$runtimeDataDir = '/var/lib/markorbit-clickhouse-full-acceptance'
$fullConfigPath = "$runtimeInstallDir/etc/config.xml"
$startupProbeConfigPath = '/opt/markorbit-clickhouse-startup-probe-v2/etc/config.xml'

$diskSpecs = @(
    [ordered]@{ key='hot_cn'; path='D:\MarkOrbitData\spike\hot_cn_spike.vhdx'; mount='markorbit_hot_cn_spike'; disk='hot_cn'; policy='spike_hot_cn'; table='full_accept_hot_cn_mt' },
    [ordered]@{ key='hot_us'; path='D:\MarkOrbitData\spike\hot_us_spike.vhdx'; mount='markorbit_hot_us_spike'; disk='hot_us'; policy='spike_hot_us'; table='full_accept_hot_us_mt' },
    [ordered]@{ key='hot_global'; path='D:\MarkOrbitData\spike\hot_global_spike.vhdx'; mount='markorbit_hot_global_spike'; disk='hot_global'; policy='spike_hot_global'; table='full_accept_hot_global_mt' },
    [ordered]@{ key='warm'; path='E:\MarkOrbitData\spike\warm_spike.vhdx'; mount='markorbit_warm_spike'; disk='warm'; policy='spike_warm'; table='full_accept_warm_mt' }
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
        $args = if ($Mode -eq 'mount') {
            @('--mount','--vhd',$VhdxPath,'--name',$MountName)
        }
        else {
            @('--unmount',$VhdxPath)
        }
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

function Normalize-WindowsPath([string]$Path) {
    if (-not $Path) { return $null }
    $value = $Path.Trim()
    if ($value.StartsWith('\\?\')) { $value = $value.Substring(4) }
    return $value.TrimEnd('\')
}

function Test-SameWindowsPath([string]$Left,[string]$Right) {
    if (-not $Left -or -not $Right) { return $false }
    return (Normalize-WindowsPath $Left).ToLowerInvariant() -eq (Normalize-WindowsPath $Right).ToLowerInvariant()
}

function Convert-WindowsPathToWsl([string]$Path) {
    $value = Normalize-WindowsPath $Path
    if ($value -notmatch '^([A-Za-z]):\\(.*)$') { throw "Cannot convert Windows path to WSL path: $Path" }
    return "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2].Replace('\','/'))"
}

function Get-WslDistros {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $result = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.DistributionName) { continue }
        $result += [ordered]@{
            name=[string]$item.DistributionName
            version=if ($null -ne $item.Version) { [int]$item.Version } else { $null }
            base_path=if ($item.BasePath) { Normalize-WindowsPath ([string]$item.BasePath) } else { $null }
        }
    }
    return @($result)
}

function Invoke-RuntimeTextBounded {
    param([Parameter(Mandatory = $true)][string[]]$Arguments,[int]$TimeoutSeconds=$RuntimeTimeoutSeconds,[switch]$AllowFailure)
    $timeoutArgs = @('timeout','--signal=TERM','--kill-after=5s',"${TimeoutSeconds}s") + $Arguments
    $result = Invoke-NativeText 'wsl.exe' (@('-d',$RuntimeDistro,'-u','root','--') + $timeoutArgs) -AllowFailure
    $result['timed_out'] = [bool]($result['exit_code'] -eq 124)
    if (-not $AllowFailure -and $result['exit_code'] -ne 0) {
        throw "Runtime command failed with exit code $($result['exit_code']): $(@($result['lines']) -join [Environment]::NewLine)"
    }
    return $result
}

function Invoke-RuntimeShellBounded {
    param([Parameter(Mandatory = $true)][string]$Command,[int]$TimeoutSeconds=$RuntimeTimeoutSeconds,[switch]$AllowFailure)
    return Invoke-RuntimeTextBounded @('sh','-lc',$Command) -TimeoutSeconds $TimeoutSeconds -AllowFailure:$AllowFailure
}

function Invoke-SpikeSql {
    param([Parameter(Mandatory = $true)][string]$Query,[switch]$MultiQuery,[switch]$AllowFailure)
    $args = @('clickhouse','client','--host','127.0.0.1','--port',$SpikeNativePort.ToString(),'--connect_timeout','5')
    if ($MultiQuery) { $args += '--multiquery' }
    $args += @('--query',$Query)
    return Invoke-RuntimeTextBounded $args -TimeoutSeconds 15 -AllowFailure:$AllowFailure
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

function Get-MountProbe([string]$MountName) {
    $target = "/mnt/wsl/$MountName"
    $probe = Invoke-RuntimeTextBounded @('findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$target) -TimeoutSeconds 10 -AllowFailure
    $text = (@($probe['lines']) -join ' ').Trim()
    return [ordered]@{ ready=[bool]($probe['exit_code'] -eq 0 -and $text -match '^ext4\s'); output=$text }
}

function Get-ConfigScopedProcesses([string]$ConfigPath) {
    $template = @'
ps -eo pid=,comm=,args= | awk -v needle='__CONFIG__' '$2=="clickhouse" && index($0, needle)>0 {print $1 "|" $0}'
'@
    $command = $template.Replace('__CONFIG__',$ConfigPath)
    $probe = Invoke-RuntimeShellBounded $command -TimeoutSeconds 8 -AllowFailure
    if ($probe['timed_out'] -or $probe['exit_code'] -ne 0) { throw "Unable to inspect config-scoped ClickHouse processes for $ConfigPath." }
    return @($probe['lines'] | Where-Object { $_.Trim() })
}

function Stop-ConfigScopedServer([string]$ConfigPath) {
    $before = @(Get-ConfigScopedProcesses $ConfigPath)
    $template = @'
set +e
pids=$(ps -eo pid=,comm=,args= | awk -v needle='__CONFIG__' '$2=="clickhouse" && index($0, needle)>0 {print $1}')
if [ -n "$pids" ]; then
  kill $pids 2>/dev/null || true
  for i in $(seq 1 15); do
    left=$(ps -eo pid=,comm=,args= | awk -v needle='__CONFIG__' '$2=="clickhouse" && index($0, needle)>0 {print $1}')
    [ -z "$left" ] && break
    sleep 1
  done
  left=$(ps -eo pid=,comm=,args= | awk -v needle='__CONFIG__' '$2=="clickhouse" && index($0, needle)>0 {print $1}')
  [ -z "$left" ] || kill -9 $left 2>/dev/null || true
fi
exit 0
'@
    $command = $template.Replace('__CONFIG__',$ConfigPath)
    [void](Invoke-RuntimeShellBounded $command -TimeoutSeconds 22 -AllowFailure)
    $after = @(Get-ConfigScopedProcesses $ConfigPath)
    return [ordered]@{ before=@($before); after=@($after); stopped=[bool]($after.Count -eq 0) }
}

function Mount-SpikeDisk([System.Collections.IDictionary]$Spec) {
    $before = Get-MountProbe ([string]$Spec['mount'])
    $commandExit = $null
    $commandTimedOut = $false
    $commandOutput = ''
    if (-not $before['ready']) {
        $mount = Invoke-WslDiskCommandBounded -Mode mount -VhdxPath ([string]$Spec['path']) -MountName ([string]$Spec['mount']) -TimeoutSeconds $MountTimeoutSeconds
        $commandExit = $mount['exit_code']
        $commandTimedOut = $mount['timed_out']
        $commandOutput = (@($mount['lines']) -join ' | ')
        Start-Sleep -Seconds 1
    }
    $after = Get-MountProbe ([string]$Spec['mount'])
    return [ordered]@{
        key=[string]$Spec['key']; command_exit=$commandExit; command_timed_out=$commandTimedOut
        command_output=$commandOutput; verified=[bool]$after['ready']; state=$after['output']
    }
}

function Unmount-SpikeDisk([System.Collections.IDictionary]$Spec) {
    $before = Get-MountProbe ([string]$Spec['mount'])
    $commandExit = $null
    $commandTimedOut = $false
    $commandOutput = ''
    if ($before['ready']) {
        $unmount = Invoke-WslDiskCommandBounded -Mode unmount -VhdxPath ([string]$Spec['path']) -TimeoutSeconds $MountTimeoutSeconds
        $commandExit = $unmount['exit_code']
        $commandTimedOut = $unmount['timed_out']
        $commandOutput = (@($unmount['lines']) -join ' | ')
        Start-Sleep -Seconds 1
    }
    $after = Get-MountProbe ([string]$Spec['mount'])
    return [ordered]@{
        key=[string]$Spec['key']; command_exit=$commandExit; command_timed_out=$commandTimedOut
        command_output=$commandOutput; detached=[bool](-not $after['ready']); state=$after['output']
    }
}

function Write-Utf8File([string]$Path,[string]$Content) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path,$Content,$utf8NoBom)
}

try {
    Write-Host '===== DEDICATED WSL CLICKHOUSE FULL ACCEPTANCE V2 ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Full acceptance must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "dedicated_wsl_clickhouse_full_acceptance_v2_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'acceptance_stage=preflight'
    $workerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerCountBefore = @($workerProbe['lines'] | Where-Object { $_.Trim() }).Count
    $productionBefore = Get-ProductionClickHouseHealth
    $volumeBefore = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $runtimeMatches = @((Get-WslDistros) | Where-Object { $_['name'] -eq $RuntimeDistro })
    $runtimeExact = [bool]($runtimeMatches.Count -eq 1 -and $runtimeMatches[0]['version'] -eq 2 -and (Test-SameWindowsPath $runtimeMatches[0]['base_path'] $RuntimeRoot))
    $versionProbe = Invoke-RuntimeTextBounded @('clickhouse','client','--version') -TimeoutSeconds 10 -AllowFailure
    $versionText = (@($versionProbe['lines']) -join ' ').Trim()
    $packageReady = [bool]($versionProbe['exit_code'] -eq 0 -and $versionText -match [regex]::Escape($ClickHouseVersion))
    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdministrator = [bool]$adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $residualStartupBefore = @(Get-ConfigScopedProcesses $startupProbeConfigPath)
    $residualFullBefore = @(Get-ConfigScopedProcesses $fullConfigPath)

    $blockers = @()
    if ($workerCountBefore -ne 0) { $blockers += 'WORKER_CONTAINER_PRESENT' }
    if (-not $productionBefore['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_READY' }
    if ($productionBefore['version'] -ne $ClickHouseVersion) { $blockers += 'PRODUCTION_CLICKHOUSE_VERSION_MISMATCH' }
    if ($volumeBefore['exit_code'] -ne 0) { $blockers += 'ACCEPTED_VOLUME_MISSING' }
    if (-not $runtimeExact) { $blockers += 'RUNTIME_IDENTITY_MISMATCH' }
    if (-not $packageReady) { $blockers += 'EXACT_CLICKHOUSE_PACKAGE_NOT_READY' }
    foreach ($spec in $diskSpecs) {
        if (-not (Test-Path -LiteralPath $spec['path'])) { $blockers += "RETAINED_$($spec['key'].ToString().ToUpperInvariant())_VHDX_MISSING" }
    }
    if ($Apply -and -not $CleanupMounts) { $blockers += 'CLEANUP_MOUNTS_REQUIRED_FOR_ACCEPTANCE' }

    $preflightReady = [bool]($blockers.Count -eq 0)
    $decision = if ($preflightReady) { 'READY_FOR_DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE' } else { 'WSL_CLICKHOUSE_SPIKE_BLOCKED' }
    $runtimeStage = 'preflight'
    $runtimeError = $null
    $mountEvidence = @()
    $cleanupEvidence = @()
    $storageDisksEvidence = @()
    $storagePoliciesEvidence = @()
    $mergeTreeProofs = @()
    $connectivityEvidence = @()
    $serverLogErrorMatches = @()
    $runtimeIp = $null
    $runtimeClickHouseVersion = $null
    $daemonLaunchExit = $null
    $daemonPidObserved = $false
    $serverStarted = $false
    $serverStopped = $false
    $allDetached = $false
    $residualStartupCleanup = $null
    $residualFullCleanup = $null
    $dockerDirectNative = $false
    $appDirectHttp = $false
    $dockerStableNative = $false
    $appStableHttp = $false
    $stableEndpoint = $null

    if ($Apply) {
        Write-Host 'acceptance_stage=apply'
        if (-not $preflightReady) { throw "Full acceptance apply blocked: $($blockers -join ', ')" }
        if (-not $isAdministrator) { throw 'Full acceptance requires elevated Administrator PowerShell.' }

        try {
            $runtimeStage = 'residual_cleanup'
            Write-Host 'acceptance_step=cleanup_residual_startup_probe'
            $residualStartupCleanup = Stop-ConfigScopedServer $startupProbeConfigPath
            if (-not $residualStartupCleanup['stopped']) { throw 'Unable to stop residual startup-probe ClickHouse process.' }
            Write-Host 'acceptance_step=cleanup_residual_full_acceptance'
            $residualFullCleanup = Stop-ConfigScopedServer $fullConfigPath
            if (-not $residualFullCleanup['stopped']) { throw 'Unable to stop residual full-acceptance ClickHouse process.' }

            $runtimeStage = 'mount_external_disks'
            foreach ($spec in $diskSpecs) {
                $key = [string]$spec['key']
                Write-Host "acceptance_step=mount_$key"
                $mountResult = Mount-SpikeDisk $spec
                $mountEvidence += $mountResult
                Write-Host "mount_state=$key|command_exit=$($mountResult['command_exit'])|timed_out=$($mountResult['command_timed_out'])|verified=$($mountResult['verified'])|state=$($mountResult['state'])"
                if (-not $mountResult['verified']) { throw "State-authoritative ext4 mount failed for $key`: $($mountResult['command_output'])" }
                $linuxRoot = "/mnt/wsl/$($spec['mount'])"
                $prepare = Invoke-RuntimeShellBounded "mkdir -p '$linuxRoot/native-clickhouse-full-acceptance' && chmod 0777 '$linuxRoot/native-clickhouse-full-acceptance'" -TimeoutSeconds 10 -AllowFailure
                if ($prepare['exit_code'] -ne 0) { throw "Unable to prepare full-acceptance directory for $key." }
            }

            $runtimeStage = 'prepare_minimal_config'
            Write-Host 'acceptance_step=write_minimal_config'
            $configPath = Join-Path $evidenceDir 'full-acceptance-config.xml'
            $usersPath = Join-Path $evidenceDir 'full-acceptance-users.xml'
            $configXml = @"
<clickhouse>
  <logger><level>trace</level><log>$runtimeInstallDir/log/server.log</log><errorlog>$runtimeInstallDir/log/error.log</errorlog><size>10M</size><count>2</count></logger>
  <listen_host>0.0.0.0</listen_host>
  <http_port>$SpikeHttpPort</http_port>
  <tcp_port>$SpikeNativePort</tcp_port>
  <interserver_http_port>19009</interserver_http_port>
  <path>$runtimeDataDir/</path>
  <tmp_path>$runtimeDataDir/tmp/</tmp_path>
  <user_files_path>$runtimeDataDir/user_files/</user_files_path>
  <format_schema_path>$runtimeDataDir/format_schemas/</format_schema_path>
  <user_directories><users_xml><path>users.xml</path></users_xml><local_directory><path>$runtimeDataDir/access/</path></local_directory></user_directories>
  <default_profile>default</default_profile><default_database>default</default_database><mlock_executable>false</mlock_executable>
  <storage_configuration>
    <disks>
      <hot_cn><type>local</type><path>/mnt/wsl/markorbit_hot_cn_spike/native-clickhouse-full-acceptance/</path></hot_cn>
      <hot_us><type>local</type><path>/mnt/wsl/markorbit_hot_us_spike/native-clickhouse-full-acceptance/</path></hot_us>
      <hot_global><type>local</type><path>/mnt/wsl/markorbit_hot_global_spike/native-clickhouse-full-acceptance/</path></hot_global>
      <warm><type>local</type><path>/mnt/wsl/markorbit_warm_spike/native-clickhouse-full-acceptance/</path></warm>
    </disks>
    <policies>
      <spike_hot_cn><volumes><main><disk>hot_cn</disk></main></volumes></spike_hot_cn>
      <spike_hot_us><volumes><main><disk>hot_us</disk></main></volumes></spike_hot_us>
      <spike_hot_global><volumes><main><disk>hot_global</disk></main></volumes></spike_hot_global>
      <spike_warm><volumes><main><disk>warm</disk></main></volumes></spike_warm>
    </policies>
  </storage_configuration>
</clickhouse>
"@
            $usersXml = @'
<clickhouse><profiles><default/></profiles><users><default><password></password><networks><ip>::/0</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users><quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas></clickhouse>
'@
            Write-Utf8File $configPath $configXml
            Write-Utf8File $usersPath $usersXml
            $configWsl = Convert-WindowsPathToWsl $configPath
            $usersWsl = Convert-WindowsPathToWsl $usersPath
            $prepareConfig = Invoke-RuntimeShellBounded "set -eu; mkdir -p '$runtimeInstallDir/log' '$runtimeInstallDir/etc' '$runtimeDataDir/tmp' '$runtimeDataDir/user_files' '$runtimeDataDir/format_schemas' '$runtimeDataDir/access'; rm -f '$runtimeInstallDir/log/server.log' '$runtimeInstallDir/log/error.log' '$runtimeInstallDir/server.pid'; cp '$configWsl' '$fullConfigPath'; cp '$usersWsl' '$runtimeInstallDir/etc/users.xml'; chmod 0644 '$fullConfigPath' '$runtimeInstallDir/etc/users.xml'" -TimeoutSeconds 15 -AllowFailure
            if ($prepareConfig['exit_code'] -ne 0) { throw "Unable to prepare minimal full-acceptance config: $(@($prepareConfig['lines']) -join [Environment]::NewLine)" }

            foreach ($key in @('path','tmp_path','logger.log','logger.errorlog')) {
                $extract = Invoke-RuntimeTextBounded @('clickhouse','extract-from-config','--config-file',$fullConfigPath,'--key',$key) -TimeoutSeconds 10 -AllowFailure
                if ($extract['exit_code'] -ne 0) { throw "Config extraction failed for $key`: $(@($extract['lines']) -join [Environment]::NewLine)" }
            }

            $runtimeStage = 'start_native_clickhouse'
            Write-Host 'acceptance_step=start_daemon'
            $launch = Invoke-RuntimeShellBounded "clickhouse server --config-file='$fullConfigPath' --daemon --pid-file='$runtimeInstallDir/server.pid'" -TimeoutSeconds 15 -AllowFailure
            $daemonLaunchExit = $launch['exit_code']
            if ($launch['timed_out']) { throw 'TIMEOUT starting full-acceptance ClickHouse daemon.' }
            if ($daemonLaunchExit -ne 0) { throw "Unable to launch full-acceptance ClickHouse daemon: $(@($launch['lines']) -join [Environment]::NewLine)" }

            Write-Host 'acceptance_step=pid_wait'
            for ($attempt=0; $attempt -lt 10; $attempt++) {
                $pidProbe = Invoke-RuntimeShellBounded "test -s '$runtimeInstallDir/server.pid' && cat '$runtimeInstallDir/server.pid'" -TimeoutSeconds 3 -AllowFailure
                if ($pidProbe['exit_code'] -eq 0) { $daemonPidObserved = $true; break }
                Start-Sleep -Seconds 1
            }

            Write-Host 'acceptance_step=readiness_wait'
            $startupReady = $false
            for ($attempt=0; $attempt -lt 20; $attempt++) {
                $probe = Invoke-SpikeSql -Query 'SELECT 1' -AllowFailure
                if ($probe['exit_code'] -eq 0 -and ((@($probe['lines']) -join '').Trim() -eq '1')) { $startupReady = $true; break }
                Start-Sleep -Seconds 1
            }
            if (-not $startupReady) {
                $diagnostic = Invoke-RuntimeShellBounded "for f in '$runtimeInstallDir/log/error.log' '$runtimeInstallDir/log/server.log'; do echo ===`$f===; tail -n 200 `"`$f`" 2>/dev/null || true; done" -TimeoutSeconds 10 -AllowFailure
                throw "Full-acceptance ClickHouse did not become SQL-ready: $(@($diagnostic['lines']) -join [Environment]::NewLine)"
            }
            $serverStarted = $true
            $versionSql = Invoke-SpikeSql -Query 'SELECT version()' -AllowFailure
            $runtimeClickHouseVersion = (@($versionSql['lines']) -join '').Trim()
            if ($versionSql['exit_code'] -ne 0 -or $runtimeClickHouseVersion -ne $ClickHouseVersion) { throw "Runtime version mismatch: $runtimeClickHouseVersion" }

            $runtimeStage = 'storage_policy_acceptance'
            Write-Host 'acceptance_step=system_disks'
            $diskEvidence = Invoke-SpikeSql -Query "SELECT name, path FROM system.disks WHERE name IN ('hot_cn','hot_us','hot_global','warm') ORDER BY name FORMAT TSV"
            $storageDisksEvidence = @($diskEvidence['lines'])
            if ($storageDisksEvidence.Count -ne 4) { throw "Expected 4 external disks, got $($storageDisksEvidence.Count)." }
            Write-Host 'acceptance_step=storage_policies'
            $policyEvidence = Invoke-SpikeSql -Query "SELECT policy_name, volume_name, arrayStringConcat(disks, ',') FROM system.storage_policies WHERE policy_name IN ('spike_hot_cn','spike_hot_us','spike_hot_global','spike_warm') ORDER BY policy_name FORMAT TSV"
            $storagePoliciesEvidence = @($policyEvidence['lines'])
            if ($storagePoliciesEvidence.Count -ne 4) { throw "Expected 4 storage policies, got $($storagePoliciesEvidence.Count)." }

            $runtimeStage = 'mergetree_acceptance'
            foreach ($spec in $diskSpecs) {
                $key = [string]$spec['key']; $table = [string]$spec['table']; $policy = [string]$spec['policy']; $disk = [string]$spec['disk']
                Write-Host "acceptance_step=mergetree_$key"
                [void](Invoke-SpikeSql -Query "DROP TABLE IF EXISTS default.$table")
                [void](Invoke-SpikeSql -Query "CREATE TABLE default.$table (id UInt64, payload String) ENGINE=MergeTree ORDER BY id SETTINGS storage_policy='$policy'")
                $statements = @()
                for ($batch=0; $batch -lt $InsertBatchCount; $batch++) {
                    $base = $batch * 1000
                    $statements += "INSERT INTO default.$table SELECT number + $base, repeat(toString(number), 4) FROM numbers($RowsPerBatch);"
                }
                $insert = Invoke-SpikeSql -Query ($statements -join [Environment]::NewLine) -MultiQuery -AllowFailure
                if ($insert['exit_code'] -ne 0) { throw "MergeTree insert failed for $key`: $(@($insert['lines']) -join [Environment]::NewLine)" }

                $mergeObserved = $false
                $partsText = ''
                for ($attempt=0; $attempt -lt 30; $attempt++) {
                    $parts = Invoke-SpikeSql -Query "SELECT count(), sum(rows), max(level), uniqExact(disk_name), any(disk_name) FROM system.parts WHERE database='default' AND table='$table' AND active FORMAT TSV" -AllowFailure
                    if ($parts['exit_code'] -eq 0 -and $parts['lines'].Count -eq 1) {
                        $partsText = [string]$parts['lines'][0]
                        $fields = $partsText -split "`t"
                        if ($fields.Count -ge 5 -and [int]$fields[2] -gt 0) { $mergeObserved = $true; break }
                    }
                    Start-Sleep -Seconds 1
                }

                $expectedRows = $InsertBatchCount * $RowsPerBatch
                $expectedSum = [int64]0
                for ($batch=0; $batch -lt $InsertBatchCount; $batch++) {
                    $base = $batch * 1000
                    $expectedSum += ([int64]$RowsPerBatch * [int64]$base) + ([int64]($RowsPerBatch - 1) * [int64]$RowsPerBatch / 2)
                }
                $select = Invoke-SpikeSql -Query "SELECT count(), sum(id) FROM default.$table FORMAT TSV" -AllowFailure
                $selectText = if ($select['lines'].Count -eq 1) { [string]$select['lines'][0] } else { '' }
                $selectFields = $selectText -split "`t"
                $selectReady = [bool]($select['exit_code'] -eq 0 -and $selectFields.Count -ge 2 -and [int64]$selectFields[0] -eq $expectedRows -and [int64]$selectFields[1] -eq $expectedSum)
                $partsFields = if ($partsText) { $partsText -split "`t" } else { @() }
                $diskReady = [bool]($partsFields.Count -ge 5 -and [int]$partsFields[3] -eq 1 -and $partsFields[4] -eq $disk)
                $tmpProbe = Invoke-RuntimeShellBounded "find '/mnt/wsl/$($spec['mount'])/native-clickhouse-full-acceptance' -name 'tmp_insert_*' -print -quit" -TimeoutSeconds 10 -AllowFailure
                $tmpCount = @($tmpProbe['lines'] | Where-Object { $_.Trim() }).Count
                $proofReady = [bool]($mergeObserved -and $selectReady -and $diskReady -and $tmpCount -eq 0)
                $mergeTreeProofs += [ordered]@{ key=$key; parts=$partsText; merge=$mergeObserved; select=$selectText; select_ready=$selectReady; disk_ready=$diskReady; tmp_insert_count=$tmpCount; ready=$proofReady }
                [void](Invoke-SpikeSql -Query "DROP TABLE IF EXISTS default.$table")
                if (-not $proofReady) { throw "MergeTree acceptance failed for $key." }
            }

            $runtimeStage = 'filesystem_log_acceptance'
            Write-Host 'acceptance_step=filesystem_log_scan'
            $logProbe = Invoke-RuntimeShellBounded "cat '$runtimeInstallDir/log/error.log' '$runtimeInstallDir/log/server.log' 2>/dev/null | grep -Ei 'permission denied|operation not permitted|cannot rename|failed to rename|tmp_insert_.*rename' || true" -TimeoutSeconds 10 -AllowFailure
            $serverLogErrorMatches = @($logProbe['lines'] | Where-Object { $_.Trim() })
            if ($serverLogErrorMatches.Count -gt 0) { throw 'Filesystem rename/permission failure class found in ClickHouse logs.' }

            $runtimeStage = 'connectivity_acceptance'
            Write-Host 'acceptance_step=resolve_runtime_ip'
            $ipProbe = Invoke-RuntimeTextBounded @('hostname','-I') -TimeoutSeconds 5 -AllowFailure
            if ($ipProbe['exit_code'] -eq 0) {
                foreach ($token in ((@($ipProbe['lines']) -join ' ') -split '\s+')) {
                    if ($token -match '^\d{1,3}(\.\d{1,3}){3}$') { $runtimeIp = $token; break }
                }
            }
            if (-not $runtimeIp) { throw 'Unable to resolve dedicated WSL IPv4.' }

            Write-Host 'acceptance_step=docker_direct_native'
            $directNative = Invoke-NativeText 'docker' @('run','--rm','--entrypoint','clickhouse-client','clickhouse/clickhouse-server:24.8','--host',$runtimeIp,'--port',$SpikeNativePort.ToString(),'--connect_timeout','5','--query','SELECT 1') -AllowFailure
            $dockerDirectNative = [bool]($directNative['exit_code'] -eq 0 -and ((@($directNative['lines']) -join '').Trim() -eq '1'))
            $connectivityEvidence += "docker_direct_native_exit=$($directNative['exit_code'])|$(@($directNative['lines']) -join ' | ')"

            Write-Host 'acceptance_step=app_direct_http'
            $pythonDirect = "import clickhouse_connect; c=clickhouse_connect.get_client(host='$runtimeIp', port=$SpikeHttpPort, username='default', password='', connect_timeout=5); print(c.query('SELECT 1').result_rows[0][0])"
            $appDirect = Invoke-NativeText 'docker' @('compose','run','--rm','--no-deps','--entrypoint','python','api','-c',$pythonDirect) -AllowFailure
            $appDirectHttp = [bool]($appDirect['exit_code'] -eq 0 -and ((@($appDirect['lines']) -join "`n") -match '(?m)^1$'))
            $connectivityEvidence += "app_direct_http_exit=$($appDirect['exit_code'])|$(@($appDirect['lines']) -join ' | ')"

            Write-Host 'acceptance_step=docker_stable_native'
            $stableNative = Invoke-NativeText 'docker' @('run','--rm','--entrypoint','clickhouse-client','clickhouse/clickhouse-server:24.8','--host','host.docker.internal','--port',$SpikeNativePort.ToString(),'--connect_timeout','5','--query','SELECT 1') -AllowFailure
            $dockerStableNative = [bool]($stableNative['exit_code'] -eq 0 -and ((@($stableNative['lines']) -join '').Trim() -eq '1'))
            $connectivityEvidence += "docker_stable_native_exit=$($stableNative['exit_code'])|$(@($stableNative['lines']) -join ' | ')"

            Write-Host 'acceptance_step=app_stable_http'
            $pythonStable = "import clickhouse_connect; c=clickhouse_connect.get_client(host='host.docker.internal', port=$SpikeHttpPort, username='default', password='', connect_timeout=5); print(c.query('SELECT 1').result_rows[0][0])"
            $appStable = Invoke-NativeText 'docker' @('compose','run','--rm','--no-deps','--entrypoint','python','api','-c',$pythonStable) -AllowFailure
            $appStableHttp = [bool]($appStable['exit_code'] -eq 0 -and ((@($appStable['lines']) -join "`n") -match '(?m)^1$'))
            $connectivityEvidence += "app_stable_http_exit=$($appStable['exit_code'])|$(@($appStable['lines']) -join ' | ')"

            if ($dockerStableNative -and $appStableHttp) {
                $stableEndpoint = 'host.docker.internal'
                $decision = 'DEDICATED_WSL_CLICKHOUSE_GO'
            }
            else {
                $decision = 'WSL_CLICKHOUSE_STORAGE_GO_CONNECTIVITY_BLOCKED'
            }
        }
        catch {
            $runtimeError = $_.Exception.Message
            $decision = 'WSL_CLICKHOUSE_SPIKE_BLOCKED'
        }
        finally {
            Write-Host 'acceptance_stage=cleanup'
            $fullStop = Stop-ConfigScopedServer $fullConfigPath
            $serverStopped = [bool]$fullStop['stopped']
            if (-not $serverStopped) {
                if (-not $runtimeError) { $runtimeError = 'Full-acceptance ClickHouse process could not be stopped safely.' }
                $decision = 'WSL_CLICKHOUSE_SPIKE_BLOCKED'
            }
            if ($serverStopped -and $CleanupMounts) {
                $allDetached = $true
                foreach ($spec in $diskSpecs) {
                    $cleanupResult = Unmount-SpikeDisk $spec
                    $cleanupEvidence += $cleanupResult
                    Write-Host "cleanup_state=$($cleanupResult['key'])|command_exit=$($cleanupResult['command_exit'])|timed_out=$($cleanupResult['command_timed_out'])|detached=$($cleanupResult['detached'])|state=$($cleanupResult['state'])"
                    if (-not $cleanupResult['detached']) { $allDetached = $false }
                }
                if (-not $allDetached) {
                    if (-not $runtimeError) { $runtimeError = 'One or more spike VHDX files remained mounted after cleanup.' }
                    $decision = 'WSL_CLICKHOUSE_SPIKE_BLOCKED'
                }
            }
        }
    }

    Write-Host 'acceptance_stage=receipt'
    $workerAfterProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerCountAfter = @($workerAfterProbe['lines'] | Where-Object { $_.Trim() }).Count
    $productionAfter = Get-ProductionClickHouseHealth
    $volumeAfter = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    if ($Apply -and (-not $productionAfter['ready'] -or $workerCountAfter -ne 0 -or $volumeAfter['exit_code'] -ne 0)) {
        if (-not $runtimeError) { $runtimeError = 'Production safety invariant failed after full acceptance.' }
        $decision = 'WSL_CLICKHOUSE_SPIKE_BLOCKED'
    }

    $report = [ordered]@{
        receipt_version='DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V2'
        decision=$decision
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        apply_requested=[bool]$Apply
        cleanup_mounts_requested=[bool]$CleanupMounts
        runtime_stage=$runtimeStage
        runtime_error=$runtimeError
        runtime_identity_ready=$runtimeExact
        package_ready=$packageReady
        runtime_clickhouse_version=$runtimeClickHouseVersion
        daemon_launch_exit=$daemonLaunchExit
        daemon_pid_observed=$daemonPidObserved
        residual_startup_processes_before=@($residualStartupBefore)
        residual_full_processes_before=@($residualFullBefore)
        residual_startup_cleanup=$residualStartupCleanup
        residual_full_cleanup=$residualFullCleanup
        mount_evidence=@($mountEvidence)
        storage_disks_evidence=@($storageDisksEvidence)
        storage_policies_evidence=@($storagePoliciesEvidence)
        mergetree_proofs=@($mergeTreeProofs)
        server_log_error_matches=@($serverLogErrorMatches)
        runtime_ip=$runtimeIp
        connectivity=[ordered]@{
            docker_direct_native=$dockerDirectNative
            app_direct_http=$appDirectHttp
            docker_stable_native=$dockerStableNative
            app_stable_http=$appStableHttp
            stable_endpoint=$stableEndpoint
            evidence=@($connectivityEvidence)
        }
        server_started=$serverStarted
        server_stopped=$serverStopped
        cleanup_evidence=@($cleanupEvidence)
        all_detached=$allDetached
        worker_container_count_before=$workerCountBefore
        worker_container_count_after=$workerCountAfter
        production_clickhouse_before=$productionBefore
        production_clickhouse_after=$productionAfter
        accepted_volume_before_present=[bool]($volumeBefore['exit_code'] -eq 0)
        accepted_volume_after_present=[bool]($volumeAfter['exit_code'] -eq 0)
        blockers=@($blockers)
        runtime_distro_unregister_performed=$false
        spike_vhdx_delete_performed=$false
        production_clickhouse_restart_performed=$false
        production_clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        corpus_replay_performed=$false
    }
    $report | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $evidenceDir 'dedicated_wsl_clickhouse_full_acceptance_v2.json') -Encoding UTF8

    Write-Host '===== DEDICATED WSL CLICKHOUSE FULL ACCEPTANCE V2 RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "runtime_stage=$runtimeStage"
    if ($runtimeError) { Write-Host "runtime_error=$runtimeError" }
    Write-Host "runtime_clickhouse_version=$runtimeClickHouseVersion"
    Write-Host "daemon_launch_exit=$daemonLaunchExit"
    Write-Host "daemon_pid_observed=$daemonPidObserved"
    Write-Host "residual_startup_process_count_before=$($residualStartupBefore.Count)"
    Write-Host "residual_full_process_count_before=$($residualFullBefore.Count)"
    foreach ($mountResult in $mountEvidence) { Write-Host "native_disk=$($mountResult['key'])|ext4=$($mountResult['verified'])|state=$($mountResult['state'])" }
    foreach ($line in $storageDisksEvidence) { Write-Host "system_disk=$line" }
    foreach ($line in $storagePoliciesEvidence) { Write-Host "storage_policy=$line" }
    foreach ($proof in $mergeTreeProofs) { Write-Host "mergetree=$($proof['key'])|ready=$($proof['ready'])|merge=$($proof['merge'])|select=$($proof['select_ready'])|disk=$($proof['disk_ready'])|tmp_insert=$($proof['tmp_insert_count'])" }
    Write-Host "docker_direct_native=$dockerDirectNative"
    Write-Host "app_direct_http=$appDirectHttp"
    Write-Host "docker_stable_native=$dockerStableNative"
    Write-Host "app_stable_http=$appStableHttp"
    Write-Host "stable_endpoint=$stableEndpoint"
    Write-Host "server_log_error_match_count=$($serverLogErrorMatches.Count)"
    Write-Host "server_started=$serverStarted"
    Write-Host "server_stopped=$serverStopped"
    Write-Host "all_spike_disks_detached=$allDetached"
    Write-Host "worker_container_count_before=$workerCountBefore"
    Write-Host "worker_container_count_after=$workerCountAfter"
    Write-Host "production_clickhouse_before_ready=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_after_ready=$($productionAfter['ready'])"
    Write-Host "accepted_volume_before_present=$([bool]($volumeBefore['exit_code'] -eq 0))"
    Write-Host "accepted_volume_after_present=$([bool]($volumeAfter['exit_code'] -eq 0))"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'runtime_distro_unregister_performed=False'
    Write-Host 'spike_vhdx_delete_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'DEDICATED_WSL_CLICKHOUSE_FULL_ACCEPTANCE_V2_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
