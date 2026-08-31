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
    [int]$SpikeHttpPort = 18123,
    [int]$SpikeNativePort = 19000,
    [string]$EvidenceRoot = 'reports'
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
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift detected during $Phase."
    }
    if (git status --porcelain) {
        throw "Working tree must be clean during $Phase."
    }
}

function Normalize-WindowsPath([string]$Path) {
    if (-not $Path) { return $null }
    $value = $Path.Trim()
    if ($value.StartsWith('\\?\')) { $value = $value.Substring(4) }
    return $value.TrimEnd('\')
}

function Get-WslDistros {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $distros = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.DistributionName) { continue }
        $distros += [ordered]@{
            name = [string]$item.DistributionName
            version = if ($null -ne $item.Version) { [int]$item.Version } else { $null }
            base_path = if ($item.BasePath) { Normalize-WindowsPath ([string]$item.BasePath) } else { $null }
        }
    }
    return @($distros)
}

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) {
        return [ordered]@{ container_id=$null; health='missing'; ready=$false; version=$null }
    }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $versionProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure
    $ready = [bool]($health -eq 'healthy' -and $sqlProbe['exit_code'] -eq 0 -and ((@($sqlProbe['lines']) -join '').Trim() -eq '1'))
    $version = if ($versionProbe['exit_code'] -eq 0) { (@($versionProbe['lines']) -join '').Trim() } else { $null }
    return [ordered]@{ container_id=$containerId; health=$health; ready=$ready; version=$version }
}

function Test-ToolingDistro([string]$DistroName) {
    $command = 'for c in mkfs.ext4 lsblk blkid findmnt tar; do command -v "$c" >/dev/null 2>&1 || exit 10; done'
    $command = $command.Replace('\"','"')
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$DistroName,'-u','root','--','sh','-lc',$command) -AllowFailure
    return [bool]($probe['exit_code'] -eq 0)
}

function Get-MountProbe([string]$DistroName, [string]$MountName) {
    $target = "/mnt/wsl/$MountName"
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$DistroName,'-u','root','--','findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$target) -AllowFailure
    return [ordered]@{
        target = $target
        mounted = [bool]($probe['exit_code'] -eq 0)
        output = (@($probe['lines']) -join ' ').Trim()
    }
}

function Test-PortListening([int]$Port) {
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return $null }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    return [bool]($listeners.Count -gt 0)
}

try {
    Write-Host '===== DEDICATED WSL CLICKHOUSE SPIKE PREFLIGHT ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Dedicated WSL ClickHouse preflight must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "dedicated_wsl_clickhouse_preflight_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'preflight_stage=safety'
    $workerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerCount = @($workerProbe['lines'] | Where-Object { $_.Trim() }).Count
    $volumeProbe = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $acceptedVolumePresent = [bool]($volumeProbe['exit_code'] -eq 0)
    $production = Get-ProductionClickHouseHealth

    Write-Host 'preflight_stage=retained_spike_disks'
    $distros = @(Get-WslDistros)
    $tooling = @($distros | Where-Object { $_['name'] -eq $ToolingDistro })
    $toolingRegistered = [bool]($tooling.Count -eq 1)
    $toolingVersion2 = [bool]($toolingRegistered -and $tooling[0]['version'] -eq 2)
    $toolingReady = if ($toolingVersion2) { Test-ToolingDistro $ToolingDistro } else { $false }

    $retainedDisks = @()
    foreach ($spec in $diskSpecs) {
        $exists = Test-Path -LiteralPath $spec['path']
        $sizeBytes = if ($exists) { [int64](Get-Item -LiteralPath $spec['path']).Length } else { [int64]0 }
        $mountProbe = if ($toolingReady) { Get-MountProbe $ToolingDistro $spec['mount'] } else { [ordered]@{ target="/mnt/wsl/$($spec['mount'])"; mounted=$null; output='' } }
        $retainedDisks += [ordered]@{
            key = $spec['key']
            path = $spec['path']
            exists = [bool]$exists
            size_bytes = $sizeBytes
            mount_name = $spec['mount']
            mounted = $mountProbe['mounted']
            mount_output = $mountProbe['output']
        }
    }

    Write-Host 'preflight_stage=wsl_runtime'
    $wslVersion = Invoke-NativeText 'wsl.exe' @('--version') -AllowFailure
    $wslHelp = Invoke-NativeText 'wsl.exe' @('--help') -AllowFailure
    $wslHelpText = @($wslHelp['lines']) -join "`n"
    $exportMentioned = [bool]($wslHelpText -match '(?im)--export')
    $importMentioned = [bool]($wslHelpText -match '(?im)--import')
    $runtimeRegistration = @($distros | Where-Object { $_['name'] -eq $RuntimeDistro })
    $runtimeRegistered = [bool]($runtimeRegistration.Count -gt 0)
    $runtimeRootExists = Test-Path -LiteralPath $RuntimeRoot
    $exportTarExists = Test-Path -LiteralPath $ExportTar

    $toolingArch = $null
    $packageEndpointReachable = $null
    if ($toolingReady) {
        $archProbe = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'--','dpkg','--print-architecture') -AllowFailure
        if ($archProbe['exit_code'] -eq 0) { $toolingArch = (@($archProbe['lines']) -join '').Trim() }
        $networkCommand = 'if command -v curl >/dev/null 2>&1; then curl -fsSI --max-time 15 https://packages.clickhouse.com/ >/dev/null; elif command -v wget >/dev/null 2>&1; then wget -q --spider --timeout=15 https://packages.clickhouse.com/; else exit 12; fi'
        $networkProbe = Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'--','sh','-lc',$networkCommand) -AllowFailure
        $packageEndpointReachable = [bool]($networkProbe['exit_code'] -eq 0)
    }

    Write-Host 'preflight_stage=capacity_ports'
    $dDrive = Get-PSDrive -Name D -PSProvider FileSystem -ErrorAction SilentlyContinue
    $fDrive = Get-PSDrive -Name F -PSProvider FileSystem -ErrorAction SilentlyContinue
    $minimumDBytes = [int64](10GB)
    $minimumFBytes = [int64](10GB)
    $httpListening = Test-PortListening $SpikeHttpPort
    $nativeListening = Test-PortListening $SpikeNativePort

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdministrator = [bool]$adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    $blockers = @()
    $advisories = @()
    if ($workerCount -ne 0) { $blockers += 'WORKER_CONTAINER_PRESENT' }
    if (-not $acceptedVolumePresent) { $blockers += 'ACCEPTED_CLICKHOUSE_VOLUME_MISSING' }
    if (-not $production['ready']) { $blockers += 'PRODUCTION_CLICKHOUSE_NOT_READY' }
    if (-not $toolingRegistered) { $blockers += 'TOOLING_DISTRO_MISSING' }
    elseif (-not $toolingVersion2) { $blockers += 'TOOLING_DISTRO_NOT_WSL2' }
    elseif (-not $toolingReady) { $blockers += 'TOOLING_DISTRO_REQUIRED_TOOLS_MISSING' }
    foreach ($disk in $retainedDisks) {
        if (-not $disk['exists']) { $blockers += "RETAINED_$($disk['key'].ToString().ToUpperInvariant())_VHDX_MISSING" }
        if ($disk['mounted'] -eq $true) { $blockers += "RETAINED_$($disk['key'].ToString().ToUpperInvariant())_VHDX_STILL_MOUNTED" }
    }
    if ($runtimeRegistered) { $blockers += 'SPIKE_RUNTIME_DISTRO_ALREADY_REGISTERED' }
    if ($runtimeRootExists) { $blockers += 'SPIKE_RUNTIME_ROOT_ALREADY_EXISTS' }
    if ($exportTarExists) { $blockers += 'SPIKE_EXPORT_TAR_ALREADY_EXISTS' }
    if (-not $dDrive -or [int64]$dDrive.Free -lt $minimumDBytes) { $blockers += 'D_DRIVE_FREE_SPACE_BELOW_10GIB' }
    if (-not $fDrive -or [int64]$fDrive.Free -lt $minimumFBytes) { $blockers += 'F_DRIVE_FREE_SPACE_BELOW_10GIB' }
    if ($httpListening -eq $true) { $blockers += "SPIKE_HTTP_PORT_${SpikeHttpPort}_IN_USE" }
    if ($nativeListening -eq $true) { $blockers += "SPIKE_NATIVE_PORT_${SpikeNativePort}_IN_USE" }
    if ($null -eq $httpListening -or $null -eq $nativeListening) { $advisories += 'WINDOWS_TCP_LISTENER_PROBE_UNAVAILABLE' }
    if ($wslVersion['exit_code'] -ne 0) { $blockers += 'WSL_VERSION_UNAVAILABLE' }
    if (-not $exportMentioned -or -not $importMentioned) { $advisories += 'WSL_HELP_IMPORT_EXPORT_TEXT_UNCONFIRMED_COMMAND_EXECUTION_WILL_DECIDE' }
    if ($packageEndpointReachable -eq $false) { $advisories += 'CLICKHOUSE_PACKAGE_ENDPOINT_UNREACHABLE_INSTALL_METHOD_MUST_DECIDE' }

    $decision = if ($blockers.Count -eq 0) { 'READY_FOR_DEDICATED_WSL_CLICKHOUSE_SPIKE' } else { 'DEDICATED_WSL_CLICKHOUSE_PREFLIGHT_BLOCKED' }

    $report = [ordered]@{
        receipt_version = 'DEDICATED_WSL_CLICKHOUSE_PREFLIGHT_V1'
        read_only = $true
        decision = $decision
        engine_sha = $ExpectedMainSha.Trim().ToLowerInvariant()
        windows_is_administrator = $isAdministrator
        worker_container_count_all_states = $workerCount
        accepted_clickhouse_volume = $AcceptedVolume
        accepted_volume_present = $acceptedVolumePresent
        production_clickhouse = $production
        tooling_distro = [ordered]@{
            name = $ToolingDistro
            registered = $toolingRegistered
            version2 = $toolingVersion2
            ready = $toolingReady
            base_path = if ($toolingRegistered) { $tooling[0]['base_path'] } else { $null }
            architecture = $toolingArch
            clickhouse_package_endpoint_reachable = $packageEndpointReachable
        }
        retained_spike_disks = @($retainedDisks)
        wsl = [ordered]@{
            version_exit_code = $wslVersion['exit_code']
            version_lines = @($wslVersion['lines'])
            help_exit_code = $wslHelp['exit_code']
            export_mentioned = $exportMentioned
            import_mentioned = $importMentioned
        }
        runtime = [ordered]@{
            distro_name = $RuntimeDistro
            registered = $runtimeRegistered
            root = Normalize-WindowsPath $RuntimeRoot
            root_exists = $runtimeRootExists
            export_tar = Normalize-WindowsPath $ExportTar
            export_tar_exists = $exportTarExists
            spike_http_port = $SpikeHttpPort
            spike_http_port_listening = $httpListening
            spike_native_port = $SpikeNativePort
            spike_native_port_listening = $nativeListening
        }
        capacity = [ordered]@{
            d_free_bytes = if ($dDrive) { [int64]$dDrive.Free } else { $null }
            f_free_bytes = if ($fDrive) { [int64]$fDrive.Free } else { $null }
        }
        blockers = @($blockers)
        advisories = @($advisories)
        destructive_action_performed = $false
        wsl_export_performed = $false
        wsl_import_performed = $false
        distro_registration_changed = $false
        vhdx_mount_performed = $false
        filesystem_format_performed = $false
        clickhouse_install_performed = $false
        docker_restart_performed = $false
        production_clickhouse_mutation_performed = $false
        corpus_replay_performed = $false
    }

    $reportPath = Join-Path $evidenceDir 'dedicated_wsl_clickhouse_preflight.json'
    $report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== DEDICATED WSL CLICKHOUSE SPIKE PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "worker_container_count_all_states=$workerCount"
    Write-Host "production_clickhouse_ready=$($production['ready'])"
    Write-Host "production_clickhouse_version=$($production['version'])"
    Write-Host "accepted_volume_present=$acceptedVolumePresent"
    Write-Host "tooling_distro_registered=$toolingRegistered"
    Write-Host "tooling_distro_version2=$toolingVersion2"
    Write-Host "tooling_ready=$toolingReady"
    Write-Host "tooling_architecture=$toolingArch"
    Write-Host "clickhouse_package_endpoint_reachable=$packageEndpointReachable"
    foreach ($disk in $retainedDisks) { Write-Host "retained_disk=$($disk['key'])|exists=$($disk['exists'])|bytes=$($disk['size_bytes'])|mounted=$($disk['mounted'])|path=$($disk['path'])" }
    Write-Host "runtime_distro=$RuntimeDistro"
    Write-Host "runtime_distro_registered=$runtimeRegistered"
    Write-Host "runtime_root=$RuntimeRoot"
    Write-Host "runtime_root_exists=$runtimeRootExists"
    Write-Host "export_tar=$ExportTar"
    Write-Host "export_tar_exists=$exportTarExists"
    Write-Host "spike_http_port=$SpikeHttpPort|listening=$httpListening"
    Write-Host "spike_native_port=$SpikeNativePort|listening=$nativeListening"
    Write-Host "windows_is_administrator=$isAdministrator"
    foreach ($line in @($wslVersion['lines'])) { Write-Host "wsl_version_line=$line" }
    Write-Host "wsl_help_export_mentioned=$exportMentioned"
    Write-Host "wsl_help_import_mentioned=$importMentioned"
    foreach ($advisory in $advisories) { Write-Host "advisory=$advisory" }
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'destructive_action_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'DEDICATED_WSL_CLICKHOUSE_PREFLIGHT_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
