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
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply,
    [switch]$CleanupMounts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$runtimeInstallDir = '/opt/markorbit-clickhouse-startup-probe'
$runtimeDataDir = '/var/lib/markorbit-clickhouse-startup-probe'
$diskSpecs = @(
    [ordered]@{ key='hot_cn'; path='D:\MarkOrbitData\spike\hot_cn_spike.vhdx'; mount='markorbit_hot_cn_spike'; disk='hot_cn' },
    [ordered]@{ key='hot_us'; path='D:\MarkOrbitData\spike\hot_us_spike.vhdx'; mount='markorbit_hot_us_spike'; disk='hot_us' },
    [ordered]@{ key='hot_global'; path='D:\MarkOrbitData\spike\hot_global_spike.vhdx'; mount='markorbit_hot_global_spike'; disk='hot_global' },
    [ordered]@{ key='warm'; path='E:\MarkOrbitData\spike\warm_spike.vhdx'; mount='markorbit_warm_spike'; disk='warm' }
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
    return [ordered]@{ exit_code=$exitCode; lines=@($rendered) }
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

function Invoke-RuntimeText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments,[switch]$AllowFailure)
    return Invoke-NativeText 'wsl.exe' (@('-d',$RuntimeDistro,'-u','root','--') + $Arguments) -AllowFailure:$AllowFailure
}

function Invoke-RuntimeShell {
    param([Parameter(Mandatory = $true)][string]$Command,[switch]$AllowFailure)
    return Invoke-RuntimeText @('sh','-lc',$Command) -AllowFailure:$AllowFailure
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
    $probe = Invoke-RuntimeText @('findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$target) -AllowFailure
    $text = (@($probe['lines']) -join ' ').Trim()
    return [ordered]@{ ready=[bool]($probe['exit_code'] -eq 0 -and $text -match '^ext4\s'); output=$text }
}

function Stop-ProbeServer {
    $command = "if [ -f '$runtimeInstallDir/server.pid' ]; then pid=`$(cat '$runtimeInstallDir/server.pid'); if kill -0 `"`$pid`" 2>/dev/null; then kill `"`$pid`"; for i in `$(seq 1 20); do kill -0 `"`$pid`" 2>/dev/null || exit 0; sleep 1; done; kill -9 `"`$pid`" 2>/dev/null || true; fi; fi; exit 0"
    $probe = Invoke-RuntimeShell $command -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

function Write-Utf8File([string]$Path,[string]$Content) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path,$Content,$utf8NoBom)
}

try {
    Write-Host '===== DEDICATED WSL CLICKHOUSE STARTUP PROBE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Startup probe must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "dedicated_wsl_clickhouse_startup_probe_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'probe_stage=preflight'
    $workerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerCountBefore = @($workerProbe['lines'] | Where-Object { $_.Trim() }).Count
    $productionBefore = Get-ProductionClickHouseHealth
    $volumeBefore = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $runtimeMatches = @((Get-WslDistros) | Where-Object { $_['name'] -eq $RuntimeDistro })
    $runtimeExact = [bool]($runtimeMatches.Count -eq 1 -and $runtimeMatches[0]['version'] -eq 2 -and (Test-SameWindowsPath $runtimeMatches[0]['base_path'] $RuntimeRoot))
    $versionProbe = Invoke-RuntimeText @('clickhouse','client','--version') -AllowFailure
    $versionText = (@($versionProbe['lines']) -join ' ').Trim()
    $packageReady = [bool]($versionProbe['exit_code'] -eq 0 -and $versionText -match [regex]::Escape($ClickHouseVersion))
    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdministrator = [bool]$adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    $blockers = @()
    if ($workerCountBefore -ne 0) { $blockers += 'WORKER_CONTAINER_PRESENT' }
    if (-not $productionBefore['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_READY' }
    if ($productionBefore['version'] -ne $ClickHouseVersion) { $blockers += 'PRODUCTION_CLICKHOUSE_VERSION_MISMATCH' }
    if ($volumeBefore['exit_code'] -ne 0) { $blockers += 'ACCEPTED_VOLUME_MISSING' }
    if (-not $runtimeExact) { $blockers += 'RUNTIME_IDENTITY_MISMATCH' }
    if (-not $packageReady) { $blockers += 'EXACT_CLICKHOUSE_PACKAGE_NOT_READY' }
    foreach ($spec in $diskSpecs) {
        if (-not (Test-Path -LiteralPath $spec['path'])) { $blockers += "RETAINED_$($spec['key'].ToString().ToUpperInvariant())_VHDX_MISSING" }
        $probe = Get-MountProbe $spec['mount']
        if ($probe['ready']) { $blockers += "RETAINED_$($spec['key'].ToString().ToUpperInvariant())_VHDX_ALREADY_MOUNTED" }
    }
    $preflightReady = [bool]($blockers.Count -eq 0)

    $decision = if ($preflightReady) { 'READY_FOR_NATIVE_STARTUP_PROBE' } else { 'NATIVE_STARTUP_PROBE_BLOCKED' }
    $runtimeError = $null
    $configExtractEvidence = @()
    $mountedPaths = @()
    $serverStarted = $false
    $serverStopped = $false
    $spikeUnmountPerformed = $false
    $startupReady = $false
    $runtimeClickHouseVersion = $null
    $logTail = @()

    if ($Apply) {
        Write-Host 'probe_stage=apply'
        if (-not $preflightReady) { throw "Startup probe apply blocked: $($blockers -join ', ')" }
        if (-not $isAdministrator) { throw 'Startup probe requires elevated Administrator PowerShell.' }
        try {
            foreach ($spec in $diskSpecs) {
                $mount = Invoke-NativeText 'wsl.exe' @('--mount','--vhd',[string]$spec['path'],'--name',[string]$spec['mount']) -AllowFailure
                if ($mount['exit_code'] -ne 0) { throw "Unable to mount $($spec['path']): $(@($mount['lines']) -join [Environment]::NewLine)" }
                $mountedPaths += [string]$spec['path']
                Start-Sleep -Seconds 1
                $probe = Get-MountProbe $spec['mount']
                if (-not $probe['ready']) { throw "Dedicated runtime does not see ext4 for $($spec['key']): $($probe['output'])" }
                $linuxRoot = "/mnt/wsl/$($spec['mount'])"
                $prepareDisk = Invoke-RuntimeShell "mkdir -p '$linuxRoot/native-clickhouse-probe' && chmod 0777 '$linuxRoot/native-clickhouse-probe'" -AllowFailure
                if ($prepareDisk['exit_code'] -ne 0) { throw "Unable to prepare probe directory on $linuxRoot." }
            }

            $configPath = Join-Path $evidenceDir 'startup-probe-config.xml'
            $usersPath = Join-Path $evidenceDir 'startup-probe-users.xml'
            $configXml = @"
<clickhouse>
  <logger>
    <level>trace</level>
    <log>$runtimeInstallDir/log/server.log</log>
    <errorlog>$runtimeInstallDir/log/error.log</errorlog>
    <size>10M</size>
    <count>2</count>
  </logger>
  <listen_host>0.0.0.0</listen_host>
  <http_port>$SpikeHttpPort</http_port>
  <tcp_port>$SpikeNativePort</tcp_port>
  <interserver_http_port>19009</interserver_http_port>
  <path>$runtimeDataDir/</path>
  <tmp_path>$runtimeDataDir/tmp/</tmp_path>
  <user_files_path>$runtimeDataDir/user_files/</user_files_path>
  <format_schema_path>$runtimeDataDir/format_schemas/</format_schema_path>
  <user_directories>
    <users_xml><path>users.xml</path></users_xml>
    <local_directory><path>$runtimeDataDir/access/</path></local_directory>
  </user_directories>
  <default_profile>default</default_profile>
  <default_database>default</default_database>
  <mlock_executable>false</mlock_executable>
  <storage_configuration>
    <disks>
      <hot_cn><type>local</type><path>/mnt/wsl/markorbit_hot_cn_spike/native-clickhouse-probe/</path></hot_cn>
      <hot_us><type>local</type><path>/mnt/wsl/markorbit_hot_us_spike/native-clickhouse-probe/</path></hot_us>
      <hot_global><type>local</type><path>/mnt/wsl/markorbit_hot_global_spike/native-clickhouse-probe/</path></hot_global>
      <warm><type>local</type><path>/mnt/wsl/markorbit_warm_spike/native-clickhouse-probe/</path></warm>
    </disks>
  </storage_configuration>
</clickhouse>
"@
            $usersXml = @'
<clickhouse>
  <profiles><default/></profiles>
  <users>
    <default>
      <password></password>
      <networks><ip>::/0</ip></networks>
      <profile>default</profile>
      <quota>default</quota>
      <access_management>1</access_management>
    </default>
  </users>
  <quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas>
</clickhouse>
'@
            Write-Utf8File $configPath $configXml
            Write-Utf8File $usersPath $usersXml
            $configWsl = Convert-WindowsPathToWsl $configPath
            $usersWsl = Convert-WindowsPathToWsl $usersPath
            $prepareConfig = Invoke-RuntimeShell "set -eu; mkdir -p '$runtimeInstallDir/log' '$runtimeInstallDir/etc' '$runtimeDataDir/tmp' '$runtimeDataDir/user_files' '$runtimeDataDir/format_schemas' '$runtimeDataDir/access'; cp '$configWsl' '$runtimeInstallDir/etc/config.xml'; cp '$usersWsl' '$runtimeInstallDir/etc/users.xml'; chmod 0644 '$runtimeInstallDir/etc/config.xml' '$runtimeInstallDir/etc/users.xml'" -AllowFailure
            if ($prepareConfig['exit_code'] -ne 0) { throw "Unable to prepare startup probe config: $(@($prepareConfig['lines']) -join [Environment]::NewLine)" }

            foreach ($key in @('path','tmp_path','user_files_path','logger.log','logger.errorlog','storage_configuration.disks.*.path')) {
                $extract = Invoke-RuntimeText @('clickhouse','extract-from-config','--config-file',"$runtimeInstallDir/etc/config.xml",'--key',$key) -AllowFailure
                $configExtractEvidence += "$key|exit=$($extract['exit_code'])|$(@($extract['lines']) -join ';')"
                if ($extract['exit_code'] -ne 0) { throw "ClickHouse config extraction failed for $key`: $(@($extract['lines']) -join [Environment]::NewLine)" }
            }

            $startCommand = "set -eu; rm -f '$runtimeInstallDir/server.pid' '$runtimeInstallDir/console.log'; nohup clickhouse server --config-file='$runtimeInstallDir/etc/config.xml' >'$runtimeInstallDir/console.log' 2>&1 & echo `"`$!`" >'$runtimeInstallDir/server.pid'"
            $start = Invoke-RuntimeShell $startCommand -AllowFailure
            if ($start['exit_code'] -ne 0) { throw "Unable to launch startup probe: $(@($start['lines']) -join [Environment]::NewLine)" }
            $serverStarted = $true

            for ($attempt=0; $attempt -lt 20; $attempt++) {
                $probe = Invoke-RuntimeText @('clickhouse','client','--host','127.0.0.1','--port',$SpikeNativePort.ToString(),'--query','SELECT 1') -AllowFailure
                if ($probe['exit_code'] -eq 0 -and ((@($probe['lines']) -join '').Trim() -eq '1')) { $startupReady = $true; break }
                Start-Sleep -Seconds 2
            }

            $collectLogs = Invoke-RuntimeShell "for f in '$runtimeInstallDir/console.log' '$runtimeInstallDir/log/error.log' '$runtimeInstallDir/log/server.log'; do echo ===`$f===; tail -n 160 `"`$f`" 2>/dev/null || true; done" -AllowFailure
            $logTail = @($collectLogs['lines'])
            @($logTail) | Set-Content -LiteralPath (Join-Path $evidenceDir 'startup-probe-runtime.log') -Encoding UTF8
            if (-not $startupReady) { throw "Minimal native ClickHouse startup failed: $(@($logTail) -join [Environment]::NewLine)" }

            $sqlVersion = Invoke-RuntimeText @('clickhouse','client','--host','127.0.0.1','--port',$SpikeNativePort.ToString(),'--query','SELECT version()') -AllowFailure
            $runtimeClickHouseVersion = (@($sqlVersion['lines']) -join '').Trim()
            if ($sqlVersion['exit_code'] -ne 0 -or $runtimeClickHouseVersion -ne $ClickHouseVersion) { throw "Startup probe version mismatch: $runtimeClickHouseVersion" }
            $decision = 'NATIVE_MINIMAL_CONFIG_STARTUP_GO'
        }
        catch {
            $runtimeError = $_.Exception.Message
            $decision = 'NATIVE_STARTUP_PROBE_BLOCKED'
        }
        finally {
            if ($serverStarted) { $serverStopped = Stop-ProbeServer }
            if ($CleanupMounts) {
                foreach ($vhdx in @($mountedPaths)) {
                    $unmount = Invoke-NativeText 'wsl.exe' @('--unmount',$vhdx) -AllowFailure
                    if ($unmount['exit_code'] -eq 0) { $spikeUnmountPerformed = $true }
                }
            }
        }
    }

    Write-Host 'probe_stage=acceptance'
    $workerAfterProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerCountAfter = @($workerAfterProbe['lines'] | Where-Object { $_.Trim() }).Count
    $productionAfter = Get-ProductionClickHouseHealth
    $volumeAfter = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    if ($Apply -and (-not $productionAfter['ready'] -or $workerCountAfter -ne 0 -or $volumeAfter['exit_code'] -ne 0)) {
        if (-not $runtimeError) { $runtimeError = 'Production safety invariant failed after startup probe.' }
        $decision = 'NATIVE_STARTUP_PROBE_BLOCKED'
    }

    $report = [ordered]@{
        receipt_version='DEDICATED_WSL_CLICKHOUSE_STARTUP_PROBE_V1'
        decision=$decision
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        apply_requested=[bool]$Apply
        cleanup_mounts_requested=[bool]$CleanupMounts
        runtime_error=$runtimeError
        runtime_identity_ready=$runtimeExact
        package_ready=$packageReady
        runtime_clickhouse_version=$runtimeClickHouseVersion
        startup_ready=$startupReady
        config_extract_evidence=@($configExtractEvidence)
        log_tail=@($logTail)
        worker_container_count_before=$workerCountBefore
        worker_container_count_after=$workerCountAfter
        production_clickhouse_before=$productionBefore
        production_clickhouse_after=$productionAfter
        accepted_volume_before_present=[bool]($volumeBefore['exit_code'] -eq 0)
        accepted_volume_after_present=[bool]($volumeAfter['exit_code'] -eq 0)
        server_started=$serverStarted
        server_stopped=$serverStopped
        spike_unmount_performed=$spikeUnmountPerformed
        runtime_distro_unregister_performed=$false
        spike_vhdx_delete_performed=$false
        production_clickhouse_restart_performed=$false
        production_clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        corpus_replay_performed=$false
        blockers=@($blockers)
    }
    $report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $evidenceDir 'startup_probe.json') -Encoding UTF8

    Write-Host '===== DEDICATED WSL CLICKHOUSE STARTUP PROBE RESULT ====='
    Write-Host "decision=$decision"
    if ($runtimeError) { Write-Host "runtime_error=$runtimeError" }
    Write-Host "startup_ready=$startupReady"
    Write-Host "runtime_clickhouse_version=$runtimeClickHouseVersion"
    foreach ($line in $configExtractEvidence) { Write-Host "config_extract=$line" }
    Write-Host "worker_container_count_before=$workerCountBefore"
    Write-Host "worker_container_count_after=$workerCountAfter"
    Write-Host "production_clickhouse_before_ready=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_after_ready=$($productionAfter['ready'])"
    Write-Host "server_started=$serverStarted"
    Write-Host "server_stopped=$serverStopped"
    Write-Host "spike_unmount_performed=$spikeUnmountPerformed"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'runtime_distro_unregister_performed=False'
    Write-Host 'spike_vhdx_delete_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'DEDICATED_WSL_CLICKHOUSE_STARTUP_PROBE_DONE'
    Assert-ExactMain 'exit'
}
finally { Pop-Location }
