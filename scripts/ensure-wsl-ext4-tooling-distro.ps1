[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$DistroName = 'Ubuntu-24.04',
    [string]$InstallRoot = 'E:\MarkOrbitData\wsl-tooling\Ubuntu-24.04',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply
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

function Get-WslDistroRecords {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $records = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.DistributionName) { continue }
        $records += [ordered]@{
            registry_key = [string]$key.PSChildName
            name = [string]$item.DistributionName
            version = if ($null -ne $item.Version) { [int]$item.Version } else { $null }
            base_path = if ($item.BasePath) { [string]$item.BasePath } else { $null }
        }
    }
    return @($records)
}

function Get-DefaultWslDistroName {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    $rootItem = Get-ItemProperty -LiteralPath $root -ErrorAction SilentlyContinue
    if (-not $rootItem -or -not $rootItem.DefaultDistribution) { return $null }
    $defaultKey = [string]$rootItem.DefaultDistribution
    $defaultPath = Join-Path $root $defaultKey
    if (-not (Test-Path -LiteralPath $defaultPath)) { return $null }
    $item = Get-ItemProperty -LiteralPath $defaultPath -ErrorAction SilentlyContinue
    if ($item -and $item.DistributionName) { return [string]$item.DistributionName }
    return $null
}

function Normalize-Path([string]$PathValue) {
    if (-not $PathValue) { return $null }
    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue)
    if ($expanded.StartsWith('\\?\')) {
        $expanded = $expanded.Substring(4)
    }
    return [IO.Path]::GetFullPath($expanded).TrimEnd('\')
}

function Get-ToolProbe([string]$Name) {
    $command = 'for c in mkfs.ext4 lsblk blkid e2fsck resize2fs; do command -v "$c" >/dev/null 2>&1 || exit 10; done'
    $result = Invoke-NativeText 'wsl.exe' @('-d',$Name,'-u','root','--','sh','-lc',$command) -AllowFailure
    return [ordered]@{
        ready = [bool]($result['exit_code'] -eq 0)
        exit_code = $result['exit_code']
        lines = @($result['lines'])
    }
}

function Get-ClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) { return [ordered]@{ health = 'missing'; ready = $false } }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $ready = [bool]($health -eq 'healthy' -and $sqlProbe['exit_code'] -eq 0 -and ((@($sqlProbe['lines']) -join '') -match '1'))
    return [ordered]@{ health = $health; ready = $ready }
}

try {
    Write-Host '===== WSL EXT4 TOOLING DISTRO OPERATOR ====='
    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'WSL tooling operator must run from local main.'
    }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "wsl_ext4_tooling_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $evidenceDir = (Resolve-Path -LiteralPath $evidenceDir).Path

    Write-Host 'tooling_stage=safety'
    $workerProbe = Invoke-NativeText 'docker' @('ps','-aq','--filter','label=com.docker.compose.project=markorbit-data-engine','--filter','label=com.docker.compose.service=worker')
    $workerIds = @($workerProbe['lines'] | Where-Object { $_.Trim() })
    $workerCount = $workerIds.Count
    $volumeProbe = Invoke-NativeText 'docker' @('volume','inspect',$AcceptedVolume) -AllowFailure
    $acceptedVolumePresent = [bool]($volumeProbe['exit_code'] -eq 0)
    $clickhouseBefore = Get-ClickHouseHealth

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdministrator = [bool]$adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    Write-Host 'tooling_stage=wsl_inventory'
    $wslVersion = Invoke-NativeText 'wsl.exe' @('--version') -AllowFailure
    $online = Invoke-NativeText 'wsl.exe' @('--list','--online') -AllowFailure
    $onlineText = @($online['lines']) -join "`n"
    $distroAvailableOnline = [bool]($online['exit_code'] -eq 0 -and $onlineText -match [regex]::Escape($DistroName))
    $defaultBefore = Get-DefaultWslDistroName
    $distrosBefore = @(Get-WslDistroRecords)
    $existing = @($distrosBefore | Where-Object { $_['name'] -eq $DistroName })

    $normalizedInstallRoot = Normalize-Path $InstallRoot
    $installDriveName = [IO.Path]::GetPathRoot($normalizedInstallRoot).TrimEnd('\').TrimEnd(':')
    $installDrive = Get-PSDrive -Name $installDriveName -PSProvider FileSystem -ErrorAction SilentlyContinue
    $installDriveFree = if ($installDrive) { [int64]$installDrive.Free } else { [int64]0 }
    $minimumFreeBytes = [int64](10GB)

    $existingLocationMatches = $false
    $existingVersion = $null
    $toolProbeBefore = [ordered]@{ ready = $false; exit_code = $null; lines = @() }
    if ($existing.Count -eq 1) {
        $existingVersion = $existing[0]['version']
        $existingLocationMatches = [bool]((Normalize-Path $existing[0]['base_path']) -eq $normalizedInstallRoot)
        if ($existingLocationMatches -and $existingVersion -eq 2) {
            $toolProbeBefore = Get-ToolProbe $DistroName
        }
    }

    $targetExistsUnregistered = [bool]((Test-Path -LiteralPath $normalizedInstallRoot) -and $existing.Count -eq 0)
    $blockers = @()
    if ($workerCount -ne 0) { $blockers += 'WORKER_CONTAINER_PRESENT' }
    if (-not $acceptedVolumePresent) { $blockers += 'ACCEPTED_CLICKHOUSE_VOLUME_MISSING' }
    if (-not $clickhouseBefore['ready']) { $blockers += 'CLICKHOUSE_NOT_READY' }
    if ($wslVersion['exit_code'] -ne 0) { $blockers += 'WSL_VERSION_UNAVAILABLE' }
    if (-not $installDrive) { $blockers += 'INSTALL_DRIVE_MISSING' }
    if ($installDriveFree -lt $minimumFreeBytes) { $blockers += 'INSTALL_DRIVE_FREE_SPACE_BELOW_10GIB' }
    if ($existing.Count -gt 1) { $blockers += 'DUPLICATE_TOOLING_DISTRO_REGISTRATION' }
    if ($existing.Count -eq 1 -and -not $existingLocationMatches) { $blockers += 'EXISTING_TOOLING_DISTRO_WRONG_LOCATION' }
    if ($targetExistsUnregistered) { $blockers += 'INSTALL_ROOT_EXISTS_WITHOUT_REGISTERED_DISTRO' }
    if ($existing.Count -eq 0 -and -not $distroAvailableOnline) { $blockers += 'DISTRO_NOT_AVAILABLE_ONLINE' }

    $alreadyReady = [bool]($existing.Count -eq 1 -and $existingLocationMatches -and $existingVersion -eq 2 -and $toolProbeBefore['ready'])
    $preflightReady = [bool]($blockers.Count -eq 0)
    $applyPerformed = $false
    $distroInstallPerformed = $false
    $packageInstallPerformed = $false
    $defaultRestorePerformed = $false

    if ($Apply) {
        Write-Host 'tooling_stage=apply'
        if (-not $preflightReady) {
            throw "WSL tooling apply blocked: $($blockers -join ', ')"
        }
        if (-not $isAdministrator) {
            throw 'WSL tooling apply requires an elevated Administrator PowerShell session.'
        }
        $applyPerformed = $true

        try {
            if (-not $alreadyReady -and $existing.Count -eq 0) {
                $parent = Split-Path -Parent $normalizedInstallRoot
                New-Item -ItemType Directory -Force -Path $parent | Out-Null
                $installResult = Invoke-NativeText 'wsl.exe' @('--install','-d',$DistroName,'--location',$normalizedInstallRoot,'--no-launch','--web-download') -AllowFailure
                if ($installResult['exit_code'] -ne 0) {
                    throw "WSL distro install failed with exit code $($installResult['exit_code']): $(@($installResult['lines']) -join [Environment]::NewLine)"
                }
                $distroInstallPerformed = $true
            }

            $distrosAfterInstall = @(Get-WslDistroRecords)
            $installed = @($distrosAfterInstall | Where-Object { $_['name'] -eq $DistroName })
            if ($installed.Count -ne 1) { throw "Expected exactly one registered $DistroName after install." }
            if ((Normalize-Path $installed[0]['base_path']) -ne $normalizedInstallRoot) {
                throw 'Installed distro base path does not match expected E: tooling root.'
            }
            if ($installed[0]['version'] -ne 2) {
                $setVersion = Invoke-NativeText 'wsl.exe' @('--set-version',$DistroName,'2') -AllowFailure
                if ($setVersion['exit_code'] -ne 0) {
                    throw "Unable to set $DistroName to WSL2: $(@($setVersion['lines']) -join [Environment]::NewLine)"
                }
            }

            $toolProbe = Get-ToolProbe $DistroName
            if (-not $toolProbe['ready']) {
                $apt = Invoke-NativeText 'wsl.exe' @('-d',$DistroName,'-u','root','--','sh','-lc','export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y e2fsprogs util-linux') -AllowFailure
                if ($apt['exit_code'] -ne 0) {
                    throw "Unable to install ext4 tooling packages: $(@($apt['lines']) -join [Environment]::NewLine)"
                }
                $packageInstallPerformed = $true
            }
        }
        finally {
            if ($defaultBefore) {
                $defaultNow = Get-DefaultWslDistroName
                if ($defaultNow -ne $defaultBefore) {
                    $restoreDefault = Invoke-NativeText 'wsl.exe' @('--set-default',$defaultBefore) -AllowFailure
                    if ($restoreDefault['exit_code'] -ne 0) {
                        throw "Unable to restore original default WSL distro $defaultBefore."
                    }
                    $defaultRestorePerformed = $true
                }
            }
        }
    }

    Write-Host 'tooling_stage=acceptance'
    $distrosFinal = @(Get-WslDistroRecords)
    $final = @($distrosFinal | Where-Object { $_['name'] -eq $DistroName })
    $finalBasePath = if ($final.Count -eq 1) { Normalize-Path $final[0]['base_path'] } else { $null }
    $finalVersion = if ($final.Count -eq 1) { $final[0]['version'] } else { $null }
    $toolProbeFinal = if ($final.Count -eq 1 -and $finalBasePath -eq $normalizedInstallRoot -and $finalVersion -eq 2) { Get-ToolProbe $DistroName } else { [ordered]@{ ready = $false; exit_code = $null; lines = @() } }
    $defaultFinal = Get-DefaultWslDistroName
    $clickhouseAfter = Get-ClickHouseHealth

    $toolingReady = [bool]($final.Count -eq 1 -and $finalBasePath -eq $normalizedInstallRoot -and $finalVersion -eq 2 -and $toolProbeFinal['ready'] -and $clickhouseAfter['ready'] -and $workerCount -eq 0)
    $decision = if ($toolingReady) { 'WSL_EXT4_TOOLING_READY' } elseif ($preflightReady -and -not $Apply) { 'READY_FOR_WSL_EXT4_TOOLING_APPLY' } else { 'WSL_EXT4_TOOLING_BLOCKED' }

    $report = [ordered]@{
        receipt_version = 'WSL_EXT4_TOOLING_DISTRO_V1'
        decision = $decision
        engine_sha = $ExpectedMainSha.Trim().ToLowerInvariant()
        apply_requested = [bool]$Apply
        apply_performed = $applyPerformed
        windows_is_administrator = $isAdministrator
        worker_container_count_all_states = $workerCount
        accepted_clickhouse_volume_present = $acceptedVolumePresent
        clickhouse_before = $clickhouseBefore
        clickhouse_after = $clickhouseAfter
        distro_name = $DistroName
        expected_install_root = $normalizedInstallRoot
        install_drive_free_bytes = $installDriveFree
        distro_available_online = $distroAvailableOnline
        default_distro_before = $defaultBefore
        default_distro_final = $defaultFinal
        distro_final_count = $final.Count
        distro_final_base_path = $finalBasePath
        distro_final_version = $finalVersion
        required_tools_ready = $toolProbeFinal['ready']
        blockers = @($blockers)
        distro_install_performed = $distroInstallPerformed
        package_install_performed = $packageInstallPerformed
        default_restore_performed = $defaultRestorePerformed
        wsl_shutdown_performed = $false
        existing_vhdx_mutation_performed = $false
        docker_restart_performed = $false
        clickhouse_mutation_performed = $false
        corpus_replay_performed = $false
    }
    $reportPath = Join-Path $evidenceDir 'wsl_ext4_tooling_distro.json'
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    Write-Host '===== WSL EXT4 TOOLING DISTRO RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "apply_requested=$([bool]$Apply)"
    Write-Host "windows_is_administrator=$isAdministrator"
    Write-Host "worker_container_count_all_states=$workerCount"
    Write-Host "distro_name=$DistroName"
    Write-Host "expected_install_root=$normalizedInstallRoot"
    Write-Host "distro_available_online=$distroAvailableOnline"
    Write-Host "default_distro_before=$defaultBefore"
    Write-Host "default_distro_final=$defaultFinal"
    Write-Host "distro_final_count=$($final.Count)"
    Write-Host "distro_final_base_path=$finalBasePath"
    Write-Host "distro_final_version=$finalVersion"
    Write-Host "required_tools_ready=$($toolProbeFinal['ready'])"
    Write-Host "clickhouse_before_ready=$($clickhouseBefore['ready'])"
    Write-Host "clickhouse_after_ready=$($clickhouseAfter['ready'])"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host "distro_install_performed=$distroInstallPerformed"
    Write-Host "package_install_performed=$packageInstallPerformed"
    Write-Host "default_restore_performed=$defaultRestorePerformed"
    Write-Host 'wsl_shutdown_performed=False'
    Write-Host 'existing_vhdx_mutation_performed=False'
    Write-Host 'docker_restart_performed=False'
    Write-Host 'clickhouse_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'WSL_EXT4_TOOLING_DISTRO_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
