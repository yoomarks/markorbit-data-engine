[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$ToolingDistro = 'Ubuntu-24.04',
    [string]$RuntimeDistro = 'MarkOrbit-ClickHouse-Spike',
    [string]$RuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike',
    [string]$ExportTar = 'F:\MarkOrbitData\spike\MarkOrbit-ClickHouse-Spike-base.tar',
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

$InsertBatchCount = 24
$RowsPerBatch = 100
$runtimeInstallDir = '/opt/markorbit-clickhouse-spike'
$runtimeDataDir = '/var/lib/markorbit-clickhouse-spike'
$packageName = "clickhouse-common-static_${ClickHouseVersion}_amd64.deb"
$packageUrl = "https://packages.clickhouse.com/deb/pool/main/c/clickhouse/$packageName"

$diskSpecs = @(
    [ordered]@{ key='hot_cn'; path='D:\MarkOrbitData\spike\hot_cn_spike.vhdx'; mount='markorbit_hot_cn_spike'; disk='hot_cn'; policy='spike_hot_cn'; table='spike_hot_cn_mt' },
    [ordered]@{ key='hot_us'; path='D:\MarkOrbitData\spike\hot_us_spike.vhdx'; mount='markorbit_hot_us_spike'; disk='hot_us'; policy='spike_hot_us'; table='spike_hot_us_mt' },
    [ordered]@{ key='hot_global'; path='D:\MarkOrbitData\spike\hot_global_spike.vhdx'; mount='markorbit_hot_global_spike'; disk='hot_global'; policy='spike_hot_global'; table='spike_hot_global_mt' },
    [ordered]@{ key='warm'; path='E:\MarkOrbitData\spike\warm_spike.vhdx'; mount='markorbit_warm_spike'; disk='warm'; policy='spike_warm'; table='spike_warm_mt' }
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

function Convert-WindowsPathToWsl([string]$Path) {
    $value = Normalize-WindowsPath $Path
    if ($value -notmatch '^([A-Za-z]):\\(.*)$') { throw "Cannot convert Windows path to WSL path: $Path" }
    $drive = $Matches[1].ToLowerInvariant()
    $rest = $Matches[2].Replace('\','/')
    return "/mnt/$drive/$rest"
}

function Get-WslDistros {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $distros = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.DistributionName) { continue }
        $distros += [ordered]@{
            id = [string]$key.PSChildName
            name = [string]$item.DistributionName
            version = if ($null -ne $item.Version) { [int]$item.Version } else { $null }
            base_path = if ($item.BasePath) { Normalize-WindowsPath ([string]$item.BasePath) } else { $null }
        }
    }
    return @($distros)
}

function Get-DefaultWslDistro {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    $rootItem = Get-ItemProperty -LiteralPath $root -ErrorAction SilentlyContinue
    if (-not $rootItem -or -not $rootItem.DefaultDistribution) { return $null }
    $id = [string]$rootItem.DefaultDistribution
    $distroKey = Join-Path $root $id
    $item = Get-ItemProperty -LiteralPath $distroKey -ErrorAction SilentlyContinue
    if ($item -and $item.DistributionName) { return [string]$item.DistributionName }
    return $null
}

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) { return [ordered]@{ container_id=$null; health='missing'; ready=$false; version=$null } }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $versionProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure
    $ready = [bool]($health -eq 'healthy' -and $sqlProbe['exit_code'] -eq 0 -and ((@($sqlProbe['lines']) -join '').Trim() -eq '1'))
    $version = if ($versionProbe['exit_code'] -eq 0) { (@($versionProbe['lines']) -join '').Trim() } else { $null }
    return [ordered]@{ container_id=$containerId; health=$health; ready=$ready; version=$version }
}

function Test-ToolingDistro {
    $command = 'for c in lsblk blkid findmnt tar curl; do command -v "$c" >/dev/null 2>&1 || exit 10; done'
    $command = $command.Replace('\"','"')
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','sh','-lc',$command) -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

function Get-MountProbe([string]$DistroName, [string]$MountName) {
    $target = "/mnt/wsl/$MountName"
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$DistroName,'-u','root','--','findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$target) -AllowFailure
    $text = (@($probe['lines']) -join ' ').Trim()
    return [ordered]@{ target=$target; ready=[bool]($probe['exit_code'] -eq 0 -and $text -match '^ext4\s'); exit_code=$probe['exit_code']; output=$text }
}

function Test-PortListening([int]$Port) {
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return $null }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    return [bool]($listeners.Count -gt 0)
}

function Invoke-RuntimeText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments,[switch]$AllowFailure)
    return Invoke-NativeText 'wsl.exe' (@('-d',$RuntimeDistro,'-u','root','--') + $Arguments) -AllowFailure:$AllowFailure
}

function Invoke-RuntimeShell {
    param([Parameter(Mandatory = $true)][string]$Command,[switch]$AllowFailure)
    return Invoke-RuntimeText @('sh','-lc',$Command) -AllowFailure:$AllowFailure
}

function Invoke-SpikeSql {
    param([Parameter(Mandatory = $true)][string]$Query,[switch]$MultiQuery,[switch]$AllowFailure)
    $args = @('clickhouse','client','--host','127.0.0.1','--port',$SpikeNativePort.ToString())
    if ($MultiQuery) { $args += '--multiquery' }
    $args += @('--query',$Query)
    return Invoke-RuntimeText $args -AllowFailure:$AllowFailure
}

function Stop-SpikeServer {
    $command = "if [ -f '$runtimeInstallDir/server.pid' ]; then pid=`$(cat '$runtimeInstallDir/server.pid'); if kill -0 `"`$pid`" 2>/dev/null; then kill `"`$pid`"; for i in `$(seq 1 30); do kill -0 `"`$pid`" 2>/dev/null || exit 0; sleep 1; done; kill -9 `"`$pid`" 2>/dev/null || true; fi; fi; exit 0"
    $probe = Invoke-RuntimeShell $command -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

function Unmount-SpikePath([string]$VhdxPath) {
    $probe = Invoke-NativeText 'wsl.exe' @('--unmount',$VhdxPath) -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

function Write-Utf8File([string]$Path, [string]$Content) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

try {
    Write-Host '===== DEDICATED WSL CLICKHOUSE BOUNDED SPIKE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Dedicated WSL ClickHouse spike must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "dedicated_wsl_clickhouse_spike_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'spike_stage=preflight'
    $workerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerCountBefore = @($workerProbe['lines'] | Where-Object { $_.Trim() }).Count
    $volumeBefore = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $acceptedVolumeBeforePresent = [bool]($volumeBefore['exit_code'] -eq 0)
    $productionBefore = Get-ProductionClickHouseHealth
    $distros = @(Get-WslDistros)
    $toolingMatches = @($distros | Where-Object { $_['name'] -eq $ToolingDistro })
    $toolingReady = [bool]($toolingMatches.Count -eq 1 -and $toolingMatches[0]['version'] -eq 2 -and (Test-ToolingDistro))
    $runtimeMatches = @($distros | Where-Object { $_['name'] -eq $RuntimeDistro })
    $runtimeRegistered = [bool]($runtimeMatches.Count -gt 0)
    $runtimeRootExists = Test-Path -LiteralPath $RuntimeRoot
    $exportTarExists = Test-Path -LiteralPath $ExportTar
    $defaultDistroBefore = Get-DefaultWslDistro
    $httpListeningBefore = Test-PortListening $SpikeHttpPort
    $nativeListeningBefore = Test-PortListening $SpikeNativePort
    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdministrator = [bool]$adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    $retainedDisks = @()
    foreach ($spec in $diskSpecs) {
        $exists = Test-Path -LiteralPath $spec['path']
        $sizeBytes = if ($exists) { [int64](Get-Item -LiteralPath $spec['path']).Length } else { [int64]0 }
        $mountProbe = if ($toolingReady) { Get-MountProbe $ToolingDistro $spec['mount'] } else { [ordered]@{ ready=$false; exit_code=-1; output=''; target="/mnt/wsl/$($spec['mount'])" } }
        $retainedDisks += [ordered]@{ key=$spec['key']; path=$spec['path']; exists=[bool]$exists; size_bytes=$sizeBytes; mounted=[bool]($mountProbe['exit_code'] -eq 0); mount_output=$mountProbe['output'] }
    }

    $blockers = @()
    if ($workerCountBefore -ne 0) { $blockers += 'WORKER_CONTAINER_PRESENT' }
    if (-not $acceptedVolumeBeforePresent) { $blockers += 'ACCEPTED_CLICKHOUSE_VOLUME_MISSING' }
    if (-not $productionBefore['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_READY' }
    if ($productionBefore['version'] -ne $ClickHouseVersion) { $blockers += "PRODUCTION_CLICKHOUSE_VERSION_NOT_$ClickHouseVersion" }
    if (-not $toolingReady) { $blockers += 'TOOLING_DISTRO_NOT_READY' }
    foreach ($disk in $retainedDisks) {
        if (-not $disk['exists']) { $blockers += "RETAINED_$($disk['key'].ToString().ToUpperInvariant())_VHDX_MISSING" }
        if ($disk['mounted']) { $blockers += "RETAINED_$($disk['key'].ToString().ToUpperInvariant())_VHDX_STILL_MOUNTED" }
    }
    if ($runtimeRegistered) { $blockers += 'SPIKE_RUNTIME_DISTRO_ALREADY_REGISTERED' }
    if ($runtimeRootExists) { $blockers += 'SPIKE_RUNTIME_ROOT_ALREADY_EXISTS' }
    if ($exportTarExists) { $blockers += 'SPIKE_EXPORT_TAR_ALREADY_EXISTS' }
    if ($httpListeningBefore -eq $true) { $blockers += "SPIKE_HTTP_PORT_${SpikeHttpPort}_IN_USE" }
    if ($nativeListeningBefore -eq $true) { $blockers += "SPIKE_NATIVE_PORT_${SpikeNativePort}_IN_USE" }

    $preflightReady = [bool]($blockers.Count -eq 0)
    $decision = if ($preflightReady) { 'READY_FOR_DEDICATED_WSL_CLICKHOUSE_APPLY' } else { 'WSL_CLICKHOUSE_SPIKE_BLOCKED' }
    $runtimeStage = 'preflight'
    $runtimeError = $null
    $exportPerformed = $false
    $importPerformed = $false
    $packageInstallPerformed = $false
    $configPrepared = $false
    $spikeMountPerformed = $false
    $serverStarted = $false
    $serverStopped = $false
    $spikeUnmountPerformed = $false
    $defaultRestorePerformed = $false
    $runtimeDistroUnregisterPerformed = $false
    $spikeVhdxDeletePerformed = $false
    $productionClickHouseRestartPerformed = $false
    $productionClickHouseMutationPerformed = $false
    $acceptedVolumeMutationPerformed = $false
    $corpusReplayPerformed = $false
    $mountedPaths = @()
    $diskRuntime = @()
    $storageDisksEvidence = @()
    $storagePoliciesEvidence = @()
    $mergeTreeProofs = @()
    $packageSha256 = $null
    $runtimeClickHouseVersion = $null
    $runtimeIp = $null
    $dockerDirectNative = $false
    $appDirectHttp = $false
    $dockerStableNative = $false
    $appStableHttp = $false
    $stableEndpoint = $null
    $serverLogErrorMatches = @()

    if ($Apply) {
        Write-Host 'spike_stage=apply'
        if (-not $preflightReady) { throw "Dedicated WSL ClickHouse apply blocked: $($blockers -join ', ')" }
        if (-not $isAdministrator) { throw 'Dedicated WSL ClickHouse apply requires an elevated Administrator PowerShell session.' }

        try {
            $runtimeStage = 'export_import_runtime'
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ExportTar) | Out-Null
            $export = Invoke-NativeText 'wsl.exe' @('--export',$ToolingDistro,$ExportTar) -AllowFailure
            if ($export['exit_code'] -ne 0 -or -not (Test-Path -LiteralPath $ExportTar)) {
                throw "WSL export failed: $(@($export['lines']) -join [Environment]::NewLine)"
            }
            $exportPerformed = $true
            New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
            $import = Invoke-NativeText 'wsl.exe' @('--import',$RuntimeDistro,$RuntimeRoot,$ExportTar,'--version','2') -AllowFailure
            if ($import['exit_code'] -ne 0) { throw "WSL import failed: $(@($import['lines']) -join [Environment]::NewLine)" }
            $importPerformed = $true

            $runtimeProbe = Invoke-RuntimeShell 'printf RUNTIME_OK' -AllowFailure
            if ($runtimeProbe['exit_code'] -ne 0 -or ((@($runtimeProbe['lines']) -join '').Trim() -ne 'RUNTIME_OK')) { throw 'Imported runtime distro did not start successfully.' }

            $runtimeStage = 'install_clickhouse'
            $installCommand = "set -eu; tmp='/tmp/$packageName'; curl -fL --retry 3 --connect-timeout 15 '$packageUrl' -o `"`$tmp`"; sha256sum `"`$tmp`"; dpkg -i `"`$tmp`" >/tmp/markorbit-clickhouse-dpkg.log 2>&1; clickhouse client --version"
            $install = Invoke-RuntimeShell $installCommand -AllowFailure
            if ($install['exit_code'] -ne 0) { throw "ClickHouse exact-version install failed: $(@($install['lines']) -join [Environment]::NewLine)" }
            $packageInstallPerformed = $true
            foreach ($line in @($install['lines'])) {
                if ($line -match '^([0-9a-fA-F]{64})\s+') { $packageSha256 = $Matches[1].ToLowerInvariant() }
            }
            $versionProbe = Invoke-RuntimeText @('clickhouse','client','--version') -AllowFailure
            $versionText = (@($versionProbe['lines']) -join ' ').Trim()
            if ($versionProbe['exit_code'] -ne 0 -or $versionText -notmatch [regex]::Escape($ClickHouseVersion)) { throw "Installed ClickHouse version mismatch: $versionText" }

            $runtimeStage = 'mount_external_disks'
            foreach ($spec in $diskSpecs) {
                $mount = Invoke-NativeText 'wsl.exe' @('--mount','--vhd',[string]$spec['path'],'--name',[string]$spec['mount']) -AllowFailure
                if ($mount['exit_code'] -ne 0) { throw "Unable to mount retained VHDX $($spec['path']): $(@($mount['lines']) -join [Environment]::NewLine)" }
                $mountedPaths += [string]$spec['path']
                $spikeMountPerformed = $true
                Start-Sleep -Seconds 1
                $mountProbe = Get-MountProbe $RuntimeDistro $spec['mount']
                if (-not $mountProbe['ready']) { throw "Runtime distro does not see ext4 for $($spec['key']): $($mountProbe['output'])" }
                $linuxRoot = "/mnt/wsl/$($spec['mount'])"
                $prepare = Invoke-RuntimeShell "mkdir -p '$linuxRoot/native-clickhouse' && chmod 0777 '$linuxRoot/native-clickhouse'" -AllowFailure
                if ($prepare['exit_code'] -ne 0) { throw "Unable to prepare native ClickHouse directory on $linuxRoot." }
                $diskRuntime += [ordered]@{ key=$spec['key']; path=$spec['path']; mount_path=$linuxRoot; filesystem=$mountProbe['output']; ext4_ready=$true }
            }

            $runtimeStage = 'prepare_config'
            $productionConfigPath = Join-Path $evidenceDir 'production-config.xml'
            $copyConfig = Invoke-NativeText 'docker' @('cp',"$($productionBefore['container_id']):/etc/clickhouse-server/config.xml",$productionConfigPath) -AllowFailure
            if ($copyConfig['exit_code'] -ne 0 -or -not (Test-Path -LiteralPath $productionConfigPath)) { throw 'Unable to copy production ClickHouse base config.' }

            $usersPath = Join-Path $evidenceDir 'spike-users.xml'
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
  <quotas>
    <default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default>
  </quotas>
</clickhouse>
'@
            Write-Utf8File $usersPath $usersXml

            $overridePath = Join-Path $evidenceDir 'spike-override.xml'
            $overrideXml = @"
<clickhouse>
  <listen_host replace="replace">0.0.0.0</listen_host>
  <http_port replace="replace">$SpikeHttpPort</http_port>
  <tcp_port replace="replace">$SpikeNativePort</tcp_port>
  <path replace="replace">$runtimeDataDir/</path>
  <tmp_path replace="replace">$runtimeDataDir/tmp/</tmp_path>
  <user_files_path replace="replace">$runtimeDataDir/user_files/</user_files_path>
  <format_schema_path replace="replace">$runtimeDataDir/format_schemas/</format_schema_path>
  <storage_configuration>
    <disks>
      <hot_cn><type>local</type><path>/mnt/wsl/markorbit_hot_cn_spike/native-clickhouse/</path></hot_cn>
      <hot_us><type>local</type><path>/mnt/wsl/markorbit_hot_us_spike/native-clickhouse/</path></hot_us>
      <hot_global><type>local</type><path>/mnt/wsl/markorbit_hot_global_spike/native-clickhouse/</path></hot_global>
      <warm><type>local</type><path>/mnt/wsl/markorbit_warm_spike/native-clickhouse/</path></warm>
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
            Write-Utf8File $overridePath $overrideXml

            $prodConfigWsl = Convert-WindowsPathToWsl $productionConfigPath
            $usersWsl = Convert-WindowsPathToWsl $usersPath
            $overrideWsl = Convert-WindowsPathToWsl $overridePath
            $prepareConfig = Invoke-RuntimeShell "set -eu; mkdir -p '$runtimeInstallDir/etc/config.d' '$runtimeDataDir/tmp' '$runtimeDataDir/user_files' '$runtimeDataDir/format_schemas'; cp '$prodConfigWsl' '$runtimeInstallDir/etc/config.xml'; cp '$usersWsl' '$runtimeInstallDir/etc/users.xml'; cp '$overrideWsl' '$runtimeInstallDir/etc/config.d/markorbit-spike.xml'; chmod 0644 '$runtimeInstallDir/etc/config.xml' '$runtimeInstallDir/etc/users.xml' '$runtimeInstallDir/etc/config.d/markorbit-spike.xml'" -AllowFailure
            if ($prepareConfig['exit_code'] -ne 0) { throw "Unable to prepare isolated ClickHouse config: $(@($prepareConfig['lines']) -join [Environment]::NewLine)" }
            $configPrepared = $true

            $runtimeStage = 'start_native_clickhouse'
            $startCommand = "set -eu; rm -f '$runtimeInstallDir/server.pid'; nohup clickhouse server --config-file='$runtimeInstallDir/etc/config.xml' >'$runtimeInstallDir/console.log' 2>&1 & echo `"`$!`" >'$runtimeInstallDir/server.pid'"
            $start = Invoke-RuntimeShell $startCommand -AllowFailure
            if ($start['exit_code'] -ne 0) { throw "Unable to start native ClickHouse: $(@($start['lines']) -join [Environment]::NewLine)" }
            $serverStarted = $true

            $ready = $false
            for ($attempt=0; $attempt -lt 30; $attempt++) {
                $probe = Invoke-SpikeSql -Query 'SELECT 1' -AllowFailure
                if ($probe['exit_code'] -eq 0 -and ((@($probe['lines']) -join '').Trim() -eq '1')) { $ready = $true; break }
                Start-Sleep -Seconds 2
            }
            if (-not $ready) {
                $logs = Invoke-RuntimeShell "tail -n 200 '$runtimeInstallDir/console.log' 2>/dev/null || true" -AllowFailure
                throw "Native ClickHouse did not become ready: $(@($logs['lines']) -join [Environment]::NewLine)"
            }
            $versionSql = Invoke-SpikeSql -Query 'SELECT version()' -AllowFailure
            $runtimeClickHouseVersion = (@($versionSql['lines']) -join '').Trim()
            if ($versionSql['exit_code'] -ne 0 -or $runtimeClickHouseVersion -ne $ClickHouseVersion) { throw "Native ClickHouse SQL version mismatch: $runtimeClickHouseVersion" }

            $runtimeStage = 'storage_policy_acceptance'
            $diskEvidence = Invoke-SpikeSql -Query "SELECT name, path FROM system.disks WHERE name IN ('hot_cn','hot_us','hot_global','warm') ORDER BY name FORMAT TSV"
            $storageDisksEvidence = @($diskEvidence['lines'])
            if ($storageDisksEvidence.Count -ne 4) { throw "Expected four native external disks; got $($storageDisksEvidence.Count)." }
            $policyEvidence = Invoke-SpikeSql -Query "SELECT policy_name, volume_name, arrayStringConcat(disks, ',') FROM system.storage_policies WHERE policy_name IN ('spike_hot_cn','spike_hot_us','spike_hot_global','spike_warm') ORDER BY policy_name FORMAT TSV"
            $storagePoliciesEvidence = @($policyEvidence['lines'])
            if ($storagePoliciesEvidence.Count -ne 4) { throw "Expected four native storage policies; got $($storagePoliciesEvidence.Count)." }

            $runtimeStage = 'mergetree_acceptance'
            $allMergeTreeReady = $true
            foreach ($spec in $diskSpecs) {
                $table = [string]$spec['table']; $policy = [string]$spec['policy']; $disk = [string]$spec['disk']
                [void](Invoke-SpikeSql -Query "DROP TABLE IF EXISTS default.$table")
                [void](Invoke-SpikeSql -Query "CREATE TABLE default.$table (id UInt64, payload String) ENGINE=MergeTree ORDER BY id SETTINGS storage_policy='$policy'")
                $statements = @()
                for ($batch=0; $batch -lt $InsertBatchCount; $batch++) {
                    $base = $batch * 1000
                    $statements += "INSERT INTO default.$table SELECT number + $base, repeat(toString(number), 4) FROM numbers($RowsPerBatch);"
                }
                $insert = Invoke-SpikeSql -Query ($statements -join [Environment]::NewLine) -MultiQuery -AllowFailure
                if ($insert['exit_code'] -ne 0) { throw "MergeTree insert failed for $table`: $(@($insert['lines']) -join [Environment]::NewLine)" }

                $mergeObserved = $false; $partsText = $null
                for ($attempt=0; $attempt -lt 30; $attempt++) {
                    $parts = Invoke-SpikeSql -Query "SELECT count(), sum(rows), max(level), uniqExact(disk_name), any(disk_name) FROM system.parts WHERE database='default' AND table='$table' AND active FORMAT TSV" -AllowFailure
                    if ($parts['exit_code'] -eq 0 -and $parts['lines'].Count -eq 1) {
                        $partsText = [string]$parts['lines'][0]
                        $fields = $partsText -split "`t"
                        if ($fields.Count -ge 5 -and [int]$fields[2] -gt 0) { $mergeObserved = $true; break }
                    }
                    Start-Sleep -Seconds 2
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
                $diskNameReady = $false
                if ($partsText) {
                    $partsFields = $partsText -split "`t"
                    $diskNameReady = [bool]($partsFields.Count -ge 5 -and [int]$partsFields[3] -eq 1 -and $partsFields[4] -eq $disk)
                }
                $tmpProbe = Invoke-RuntimeShell "find '/mnt/wsl/$($spec['mount'])/native-clickhouse' -name 'tmp_insert_*' -print -quit" -AllowFailure
                $tmpInsertCount = @($tmpProbe['lines'] | Where-Object { $_.Trim() }).Count
                $proofReady = [bool]($mergeObserved -and $selectReady -and $diskNameReady -and $tmpInsertCount -eq 0)
                if (-not $proofReady) { $allMergeTreeReady = $false }
                $mergeTreeProofs += [ordered]@{ key=$spec['key']; table=$table; policy=$policy; disk=$disk; parts_evidence=$partsText; background_merge_observed=$mergeObserved; select_evidence=$selectText; select_verified=$selectReady; disk_name_verified=$diskNameReady; tmp_insert_count=$tmpInsertCount; ready=$proofReady }
                [void](Invoke-SpikeSql -Query "DROP TABLE IF EXISTS default.$table")
            }
            if (-not $allMergeTreeReady) { throw 'One or more native WSL MergeTree disk proofs failed.' }

            $runtimeStage = 'connectivity_acceptance'
            $ipProbe = Invoke-RuntimeText @('hostname','-I') -AllowFailure
            if ($ipProbe['exit_code'] -eq 0) {
                foreach ($token in ((@($ipProbe['lines']) -join ' ') -split '\s+')) {
                    if ($token -match '^\d{1,3}(\.\d{1,3}){3}$') { $runtimeIp = $token; break }
                }
            }
            if (-not $runtimeIp) { throw 'Unable to resolve dedicated WSL runtime IPv4 address.' }

            $directNative = Invoke-NativeText 'docker' @('run','--rm','--entrypoint','clickhouse-client','clickhouse/clickhouse-server:24.8','--host',$runtimeIp,'--port',$SpikeNativePort.ToString(),'--query','SELECT 1') -AllowFailure
            $dockerDirectNative = [bool]($directNative['exit_code'] -eq 0 -and ((@($directNative['lines']) -join '').Trim() -eq '1'))
            $pythonProbe = "import clickhouse_connect; c=clickhouse_connect.get_client(host='$runtimeIp', port=$SpikeHttpPort, username='default', password=''); print(c.query('SELECT 1').result_rows[0][0])"
            $appDirect = Invoke-NativeText 'docker' @('compose','run','--rm','--no-deps','--entrypoint','python','api','-c',$pythonProbe) -AllowFailure
            $appDirectHttp = [bool]($appDirect['exit_code'] -eq 0 -and ((@($appDirect['lines']) -join "`n") -match '(?m)^1$'))

            $stableNative = Invoke-NativeText 'docker' @('run','--rm','--entrypoint','clickhouse-client','clickhouse/clickhouse-server:24.8','--host','host.docker.internal','--port',$SpikeNativePort.ToString(),'--query','SELECT 1') -AllowFailure
            $dockerStableNative = [bool]($stableNative['exit_code'] -eq 0 -and ((@($stableNative['lines']) -join '').Trim() -eq '1'))
            $stablePython = "import clickhouse_connect; c=clickhouse_connect.get_client(host='host.docker.internal', port=$SpikeHttpPort, username='default', password=''); print(c.query('SELECT 1').result_rows[0][0])"
            $appStable = Invoke-NativeText 'docker' @('compose','run','--rm','--no-deps','--entrypoint','python','api','-c',$stablePython) -AllowFailure
            $appStableHttp = [bool]($appStable['exit_code'] -eq 0 -and ((@($appStable['lines']) -join "`n") -match '(?m)^1$'))

            $console = Invoke-RuntimeShell "cat '$runtimeInstallDir/console.log' 2>/dev/null || true" -AllowFailure
            @($console['lines']) | Set-Content -LiteralPath (Join-Path $evidenceDir 'native-clickhouse-console.log') -Encoding UTF8
            $serverLogErrorMatches = @($console['lines'] | Where-Object { $_ -match '(?i)(permission denied|operation not permitted|cannot rename|failed to rename|tmp_insert_.*rename)' })
            if ($serverLogErrorMatches.Count -gt 0) { throw 'Native ClickHouse log contains filesystem rename/permission failure class.' }

            if ($dockerDirectNative -and $appDirectHttp -and $dockerStableNative -and $appStableHttp) {
                $stableEndpoint = 'host.docker.internal'
                $decision = 'DEDICATED_WSL_CLICKHOUSE_GO'
            }
            elseif ($dockerDirectNative -and $appDirectHttp) {
                $decision = 'WSL_CLICKHOUSE_STORAGE_GO_CONNECTIVITY_BLOCKED'
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
            if ($importPerformed) { $serverStopped = Stop-SpikeServer }
            if ($CleanupMounts) {
                foreach ($vhdx in @($mountedPaths)) {
                    if (Unmount-SpikePath $vhdx) { $spikeUnmountPerformed = $true }
                }
            }
            $defaultDistroFinalDuringCleanup = Get-DefaultWslDistro
            if ($defaultDistroBefore -and $defaultDistroFinalDuringCleanup -and $defaultDistroFinalDuringCleanup -ne $defaultDistroBefore) {
                $restore = Invoke-NativeText 'wsl.exe' @('--set-default',$defaultDistroBefore) -AllowFailure
                $defaultRestorePerformed = [bool]($restore['exit_code'] -eq 0)
            }
        }
    }

    Write-Host 'spike_stage=acceptance'
    $workerAfterProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerCountAfter = @($workerAfterProbe['lines'] | Where-Object { $_.Trim() }).Count
    $productionAfter = Get-ProductionClickHouseHealth
    $volumeAfter = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $acceptedVolumeAfterPresent = [bool]($volumeAfter['exit_code'] -eq 0)
    $defaultDistroFinal = Get-DefaultWslDistro
    $runtimeFinal = @((Get-WslDistros) | Where-Object { $_['name'] -eq $RuntimeDistro })
    $runtimeFinalRegistered = [bool]($runtimeFinal.Count -eq 1)
    $runtimeFinalBasePath = if ($runtimeFinalRegistered) { $runtimeFinal[0]['base_path'] } else { $null }

    if ($Apply -and (-not $productionAfter['ready'] -or $workerCountAfter -ne 0 -or -not $acceptedVolumeAfterPresent)) {
        if (-not $runtimeError) { $runtimeError = 'Production safety invariant failed after dedicated WSL ClickHouse spike.' }
        $decision = 'WSL_CLICKHOUSE_SPIKE_BLOCKED'
    }

    $report = [ordered]@{
        receipt_version = 'DEDICATED_WSL_CLICKHOUSE_SPIKE_V1'
        decision = $decision
        engine_sha = $ExpectedMainSha.Trim().ToLowerInvariant()
        apply_requested = [bool]$Apply
        cleanup_mounts_requested = [bool]$CleanupMounts
        runtime_stage = $runtimeStage
        runtime_error = $runtimeError
        windows_is_administrator = $isAdministrator
        worker_container_count_before = $workerCountBefore
        worker_container_count_after = $workerCountAfter
        production_clickhouse_before = $productionBefore
        production_clickhouse_after = $productionAfter
        accepted_volume_before_present = $acceptedVolumeBeforePresent
        accepted_volume_after_present = $acceptedVolumeAfterPresent
        tooling_ready = $toolingReady
        clickhouse_version_target = $ClickHouseVersion
        runtime_clickhouse_version = $runtimeClickHouseVersion
        package_url = $packageUrl
        package_sha256 = $packageSha256
        runtime = [ordered]@{ distro=$RuntimeDistro; root=Normalize-WindowsPath $RuntimeRoot; registered_final=$runtimeFinalRegistered; base_path_final=$runtimeFinalBasePath; export_tar=Normalize-WindowsPath $ExportTar; runtime_ip=$runtimeIp; http_port=$SpikeHttpPort; native_port=$SpikeNativePort }
        default_distro_before = $defaultDistroBefore
        default_distro_final = $defaultDistroFinal
        disk_runtime = @($diskRuntime)
        storage_disks_evidence = @($storageDisksEvidence)
        storage_policies_evidence = @($storagePoliciesEvidence)
        mergetree_proofs = @($mergeTreeProofs)
        connectivity = [ordered]@{ docker_direct_native=$dockerDirectNative; app_direct_http=$appDirectHttp; docker_stable_native=$dockerStableNative; app_stable_http=$appStableHttp; stable_endpoint=$stableEndpoint }
        server_log_error_matches = @($serverLogErrorMatches)
        blockers = @($blockers)
        export_performed = $exportPerformed
        import_performed = $importPerformed
        package_install_performed = $packageInstallPerformed
        config_prepared = $configPrepared
        spike_mount_performed = $spikeMountPerformed
        server_started = $serverStarted
        server_stopped = $serverStopped
        spike_unmount_performed = $spikeUnmountPerformed
        default_restore_performed = $defaultRestorePerformed
        runtime_distro_unregister_performed = $runtimeDistroUnregisterPerformed
        spike_vhdx_delete_performed = $spikeVhdxDeletePerformed
        production_clickhouse_restart_performed = $productionClickHouseRestartPerformed
        production_clickhouse_mutation_performed = $productionClickHouseMutationPerformed
        accepted_volume_mutation_performed = $acceptedVolumeMutationPerformed
        corpus_replay_performed = $corpusReplayPerformed
    }
    $reportPath = Join-Path $evidenceDir 'dedicated_wsl_clickhouse_spike.json'
    $report | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== DEDICATED WSL CLICKHOUSE BOUNDED SPIKE RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "runtime_stage=$runtimeStage"
    if ($runtimeError) { Write-Host "runtime_error=$runtimeError" }
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "cleanup_mounts_requested=$([bool]$CleanupMounts)"
    Write-Host "windows_is_administrator=$isAdministrator"
    Write-Host "worker_container_count_before=$workerCountBefore"
    Write-Host "worker_container_count_after=$workerCountAfter"
    Write-Host "production_clickhouse_before_ready=$($productionBefore['ready'])"
    Write-Host "production_clickhouse_after_ready=$($productionAfter['ready'])"
    Write-Host "production_clickhouse_version=$($productionBefore['version'])"
    Write-Host "accepted_volume_before_present=$acceptedVolumeBeforePresent"
    Write-Host "accepted_volume_after_present=$acceptedVolumeAfterPresent"
    Write-Host "runtime_distro=$RuntimeDistro"
    Write-Host "runtime_registered_final=$runtimeFinalRegistered"
    Write-Host "runtime_base_path_final=$runtimeFinalBasePath"
    Write-Host "runtime_clickhouse_version=$runtimeClickHouseVersion"
    Write-Host "package_sha256=$packageSha256"
    Write-Host "runtime_ip=$runtimeIp"
    foreach ($disk in $diskRuntime) { Write-Host "native_disk=$($disk['key'])|ext4=$($disk['ext4_ready'])|mount=$($disk['mount_path'])" }
    foreach ($line in $storageDisksEvidence) { Write-Host "system_disk=$line" }
    foreach ($line in $storagePoliciesEvidence) { Write-Host "storage_policy=$line" }
    foreach ($proof in $mergeTreeProofs) { Write-Host "mergetree=$($proof['key'])|ready=$($proof['ready'])|merge=$($proof['background_merge_observed'])|select=$($proof['select_verified'])|disk=$($proof['disk_name_verified'])|tmp_insert=$($proof['tmp_insert_count'])" }
    Write-Host "docker_direct_native=$dockerDirectNative"
    Write-Host "app_direct_http=$appDirectHttp"
    Write-Host "docker_stable_native=$dockerStableNative"
    Write-Host "app_stable_http=$appStableHttp"
    Write-Host "stable_endpoint=$stableEndpoint"
    Write-Host "server_log_error_match_count=$($serverLogErrorMatches.Count)"
    Write-Host "server_stopped=$serverStopped"
    Write-Host "spike_unmount_performed=$spikeUnmountPerformed"
    Write-Host "default_distro_before=$defaultDistroBefore"
    Write-Host "default_distro_final=$defaultDistroFinal"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'runtime_distro_unregister_performed=False'
    Write-Host 'spike_vhdx_delete_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'DEDICATED_WSL_CLICKHOUSE_SPIKE_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
