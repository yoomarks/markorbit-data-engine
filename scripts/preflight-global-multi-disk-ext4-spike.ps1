[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

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
    return [ordered]@{ exit_code = $exitCode; lines = @($rendered) }
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
            base_path = if ($item.BasePath) { [string]$item.BasePath } else { $null }
        }
    }
    return @($distros)
}

function Test-MkfsExt4([string]$DistroName) {
    $result = Invoke-NativeText 'wsl.exe' @('-d', $DistroName, '--', 'sh', '-lc', 'command -v mkfs.ext4 >/dev/null 2>&1') -AllowFailure
    return [bool]($result.exit_code -eq 0)
}

try {
    Write-Host '===== GLOBAL MULTI-DISK EXT4 SPIKE PREFLIGHT ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'Spike preflight must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "global_multi_disk_ext4_spike_preflight_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'preflight_stage=workers'
    $workerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerIds = @($workerProbe['lines'] | Where-Object { $_.Trim() })
    $workerCount = $workerIds.Count

    Write-Host 'preflight_stage=wsl'
    $hostOs = Get-CimInstance Win32_OperatingSystem
    $wslVersion = Invoke-NativeText 'wsl.exe' @('--version') -AllowFailure
    $wslStatus = Invoke-NativeText 'wsl.exe' @('--status') -AllowFailure
    $wslHelp = Invoke-NativeText 'wsl.exe' @('--help') -AllowFailure
    $wslHelpText = @($wslHelp['lines']) -join "`n"
    $wslMountSupported = [bool]($wslHelpText -match '(?im)(^|\s)--mount(\s|$)')
    $wslVhdOptionSupported = [bool]($wslHelpText -match '(?im)(^|\s)--vhd(\s|$)')
    $wslInstallLocationSupported = [bool]($wslHelpText -match '(?im)(^|\s)--location(\s|$)')
    $wslMountVhdSupported = [bool]($wslVersion.exit_code -eq 0 -and $wslHelp.exit_code -eq 0 -and $wslMountSupported -and $wslVhdOptionSupported)

    $distros = @(Get-WslDistros)
    $formattingDistros = @()
    foreach ($distro in $distros) {
        if ($distro.version -ne 2) { continue }
        if ($distro.name -match '(?i)^docker-desktop') { continue }
        $hasMkfs = Test-MkfsExt4 $distro.name
        $formattingDistros += [ordered]@{ name = $distro.name; version = $distro.version; mkfs_ext4 = $hasMkfs }
    }
    $formattingReady = [bool](@($formattingDistros | Where-Object { $_.mkfs_ext4 }).Count -gt 0)

    Write-Host 'preflight_stage=docker'
    $dockerInfoRaw = Invoke-NativeText 'docker' @('info','--format','{{json .}}')
    $dockerInfo = (($dockerInfoRaw.lines -join "`n") | ConvertFrom-Json)
    $dockerContext = ((Invoke-NativeText 'docker' @('context','show')).lines -join '').Trim()
    $dockerKernel = [string]$dockerInfo.KernelVersion
    $dockerOs = [string]$dockerInfo.OperatingSystem
    $dockerOsType = [string]$dockerInfo.OSType
    $dockerDesktopLinux = [bool]($dockerOsType -eq 'linux' -and $dockerOs -match '(?i)docker desktop')
    $dockerWslKernel = [bool]($dockerKernel -match '(?i)microsoft.*wsl|wsl2')

    Write-Host 'preflight_stage=clickhouse'
    $volumeInspectRaw = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume)
    $volumeInspect = @(($volumeInspectRaw.lines -join "`n") | ConvertFrom-Json)
    if ($volumeInspect.Count -ne 1) { throw "Expected exactly one Docker volume inspect result for $AcceptedVolume." }
    $clickhouseContainerId = (((Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse')).lines -join '').Trim())
    if (-not $clickhouseContainerId) { throw 'ClickHouse service container is not running.' }
    $clickhouseHealth = (((Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$clickhouseContainerId)).lines -join '').Trim())
    $clickhouseProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $clickhouseReady = [bool]($clickhouseHealth -eq 'healthy' -and $clickhouseProbe.exit_code -eq 0 -and (($clickhouseProbe.lines -join '') -match '1'))

    Write-Host 'preflight_stage=vhd_creation_primitives'
    $newVhdAvailable = [bool](Get-Command New-VHD -ErrorAction SilentlyContinue)
    $diskpartAvailable = [bool](Get-Command diskpart.exe -ErrorAction SilentlyContinue)
    $vhdCreationReady = [bool]($newVhdAvailable -or $diskpartAvailable)

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdministrator = [bool]$adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    $blockers = @()
    if ($workerCount -ne 0) { $blockers += 'WORKER_CONTAINER_PRESENT' }
    if ($wslVersion.exit_code -ne 0) { $blockers += 'WSL_VERSION_UNAVAILABLE' }
    if (-not $wslMountVhdSupported) { $blockers += 'WSL_MOUNT_VHD_UNCONFIRMED' }
    if (-not $formattingReady) { $blockers += 'NO_WSL2_FORMATTING_DISTRO_WITH_MKFS_EXT4' }
    if (-not $dockerDesktopLinux) { $blockers += 'DOCKER_DESKTOP_LINUX_RUNTIME_UNCONFIRMED' }
    if (-not $dockerWslKernel) { $blockers += 'DOCKER_WSL2_KERNEL_UNCONFIRMED' }
    if (-not $clickhouseReady) { $blockers += 'CLICKHOUSE_NOT_HEALTHY' }
    if (-not $vhdCreationReady) { $blockers += 'NO_VHDX_CREATION_PRIMITIVE' }

    $decision = if ($blockers.Count -eq 0) { 'READY_FOR_BOUNDED_EXT4_SPIKE' } else { 'SPIKE_PREFLIGHT_BLOCKED' }
    $report = [ordered]@{
        preflight_version = 'GLOBAL_MULTI_DISK_EXT4_SPIKE_PREFLIGHT_V1'
        read_only = $true
        decision = $decision
        engine_sha = $ExpectedMainSha.Trim().ToLowerInvariant()
        worker_container_count_all_states = $workerCount
        windows_is_administrator = $isAdministrator
        windows = [ordered]@{
            caption = [string]$hostOs.Caption
            version = [string]$hostOs.Version
            build_number = [string]$hostOs.BuildNumber
        }
        wsl = [ordered]@{
            version_exit_code = $wslVersion.exit_code
            version_lines = @($wslVersion.lines)
            status_exit_code = $wslStatus.exit_code
            status_lines = @($wslStatus.lines)
            help_exit_code = $wslHelp.exit_code
            mount_supported = $wslMountSupported
            vhd_option_supported = $wslVhdOptionSupported
            install_location_supported = $wslInstallLocationSupported
            mount_vhd_supported = $wslMountVhdSupported
            distros = @($distros)
            formatting_distros = @($formattingDistros)
        }
        docker = [ordered]@{
            context = $dockerContext
            operating_system = $dockerOs
            os_type = $dockerOsType
            kernel_version = $dockerKernel
            docker_desktop_linux = $dockerDesktopLinux
            wsl2_kernel = $dockerWslKernel
        }
        clickhouse = [ordered]@{
            health = $clickhouseHealth
            probe_exit_code = $clickhouseProbe.exit_code
            ready = $clickhouseReady
            accepted_volume = $AcceptedVolume
            accepted_volume_mountpoint = [string]$volumeInspect[0].Mountpoint
        }
        vhdx_creation = [ordered]@{
            new_vhd_available = $newVhdAvailable
            diskpart_available = $diskpartAvailable
            ready = $vhdCreationReady
        }
        blockers = @($blockers)
        destructive_action_performed = $false
        vhdx_create_performed = $false
        vhdx_mount_performed = $false
        filesystem_format_performed = $false
        docker_restart_performed = $false
        clickhouse_mutation_performed = $false
        corpus_replay_performed = $false
    }

    $reportPath = Join-Path $evidenceDir 'global_multi_disk_ext4_spike_preflight.json'
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== GLOBAL MULTI-DISK EXT4 SPIKE PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "worker_container_count_all_states=$workerCount"
    Write-Host "windows_caption=$([string]$hostOs.Caption)"
    Write-Host "windows_version=$([string]$hostOs.Version)"
    Write-Host "windows_build_number=$([string]$hostOs.BuildNumber)"
    foreach ($line in @($wslVersion['lines'])) { Write-Host "wsl_version_line=$line" }
    Write-Host "wsl_help_mount_supported=$wslMountSupported"
    Write-Host "wsl_help_vhd_option_supported=$wslVhdOptionSupported"
    Write-Host "wsl_help_install_location_supported=$wslInstallLocationSupported"
    Write-Host "wsl_mount_vhd_supported=$wslMountVhdSupported"
    foreach ($distro in $distros) { Write-Host "wsl_distro=$($distro.name)|version=$($distro.version)|base_path=$($distro.base_path)" }
    Write-Host "wsl_formatting_distro_ready=$formattingReady"
    foreach ($distro in $formattingDistros) { Write-Host "wsl_formatting_distro=$($distro.name)|version=$($distro.version)|mkfs_ext4=$($distro.mkfs_ext4)" }
    Write-Host "docker_desktop_linux=$dockerDesktopLinux"
    Write-Host "docker_wsl2_kernel=$dockerWslKernel"
    Write-Host "clickhouse_health=$clickhouseHealth"
    Write-Host "clickhouse_ready=$clickhouseReady"
    Write-Host "new_vhd_available=$newVhdAvailable"
    Write-Host "diskpart_available=$diskpartAvailable"
    Write-Host "windows_is_administrator=$isAdministrator"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'destructive_action_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'GLOBAL_MULTI_DISK_EXT4_SPIKE_PREFLIGHT_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
