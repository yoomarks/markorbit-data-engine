[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$RuntimeDistro = 'MarkOrbit-ClickHouse-Spike',
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$acceptedArchitectureDecision = 'DEDICATED_WSL_CLICKHOUSE_GO'
$acceptedArchitectureProofSha = '1d990dc8ab44bdb827538961309c6c33fb38234f'

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
    $lines = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command $($Arguments -join ' ') failed with exit code $exitCode`: $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
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

function Get-ProductionClickHouseHealth {
    $containerProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($containerProbe['lines']) -join '').Trim()
    if (-not $containerId) { return [ordered]@{ ready=$false; container_id=$null; health=$null } }
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $health = (@($healthProbe['lines']) -join '').Trim()
    $ready = [bool](
        $healthProbe['exit_code'] -eq 0 -and
        $health -eq 'healthy' -and
        $sqlProbe['exit_code'] -eq 0 -and
        ((@($sqlProbe['lines']) -join '').Trim() -eq '1')
    )
    return [ordered]@{ ready=$ready; container_id=$containerId; health=$health }
}

function Get-WorkerContainerCount {
    $probe = Invoke-NativeText 'docker' @('compose','ps','-a','-q','worker') -AllowFailure
    if ($probe['exit_code'] -ne 0) { return -1 }
    return @($probe['lines'] | Where-Object { $_.Trim() }).Count
}

function Get-ClickHouseTsv([string]$Query) {
    $probe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--format','TSVRaw','--query',$Query) -AllowFailure
    if ($probe['exit_code'] -ne 0) { return $null }
    $line = @($probe['lines'] | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($line.Count -ne 1) { return $null }
    return [string]$line[0]
}

function Get-RawDataPathEvidence {
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        return [ordered]@{ configured=$false; configured_path=$null; resolved_existing_ancestor=$null; drive_root=$null }
    }
    $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match '^\s*RAW_DATA_PATH\s*=' } |
        Select-Object -First 1
    if (-not $line) {
        return [ordered]@{ configured=$false; configured_path=$null; resolved_existing_ancestor=$null; drive_root=$null }
    }
    $value = (($line -split '=',2)[1]).Trim().Trim('"').Trim("'")
    if (-not [System.IO.Path]::IsPathRooted($value)) { $value = Join-Path $repoRoot $value }
    $probe = $value
    while ($probe -and -not (Test-Path -LiteralPath $probe)) {
        $parent = Split-Path -Parent $probe
        if (-not $parent -or $parent -eq $probe) { break }
        $probe = $parent
    }
    $root = if ($probe -and (Test-Path -LiteralPath $probe)) { [System.IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $probe).Path) } else { $null }
    return [ordered]@{
        configured=$true
        configured_path=$value
        resolved_existing_ancestor=$probe
        drive_root=$root
    }
}

function Get-ShallowHostInventoryRoot {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$RunId
    )
    if ([string]::IsNullOrWhiteSpace($RunId) -or $RunId -notmatch '^[0-9A-Za-z_-]+$') {
        throw 'Host inventory run id must be a short filesystem-safe token.'
    }
    return Join-Path $RepositoryRoot (Join-Path 'reports' (Join-Path '_hi' $RunId))
}

function Invoke-HostInventory {
    $runId = '{0}_{1}' -f (Get-Date -Format 'yyyyMMdd_HHmmssfff'), $PID
    $inventoryRoot = Get-ShallowHostInventoryRoot -RepositoryRoot $repoRoot -RunId $runId
    New-Item -ItemType Directory -Path $inventoryRoot -Force | Out-Null
    Write-Host 'host_inventory_evidence_strategy=SHALLOW_REPO_REPORTS'
    Write-Host "host_inventory_evidence_root=$inventoryRoot"

    $scriptPath = Join-Path $PSScriptRoot 'inventory-global-multi-disk-host.ps1'
    $childArgs = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$scriptPath,
        '-ExpectedMainSha',$ExpectedMainSha,
        '-AcceptedVolume',$AcceptedVolume,
        '-EvidenceRoot',$inventoryRoot
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& powershell.exe @childArgs 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $lines = @($output | ForEach-Object { $_.ToString() })
    foreach ($line in $lines) { Write-Host $line }
    if ($exitCode -ne 0) { throw "Global multi-disk host inventory exited $exitCode." }

    $directories = @(Get-ChildItem -LiteralPath $inventoryRoot -Directory -Filter 'global_multi_disk_host_inventory_*' |
        Sort-Object LastWriteTime -Descending)
    if ($directories.Count -ne 1) { throw "Expected exactly one isolated host inventory directory; observed $($directories.Count)." }
    $reportPath = Join-Path $directories[0].FullName 'global_multi_disk_host_inventory.json'
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) { throw 'Host inventory JSON receipt is missing.' }
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    return [ordered]@{
        report=$report
        receipt_path=$reportPath
        evidence_root=$inventoryRoot
    }
}

try {
    Write-Host '===== PRODUCTION MULTI-DISK MIGRATION READINESS ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Production migration readiness must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Production migration readiness requires elevated Administrator PowerShell.'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_multi_disk_migration_readiness_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    Write-Host 'readiness_stage=production_invariants'
    $workerCount = Get-WorkerContainerCount
    $production = Get-ProductionClickHouseHealth
    if ($workerCount -ne 0) { throw "Worker containers must be zero; observed $workerCount." }
    if (-not $production['ready']) { throw 'Production ClickHouse must be healthy.' }

    Write-Host 'readiness_stage=host_inventory'
    $inventoryResult = Invoke-HostInventory
    $inventory = $inventoryResult['report']

    Write-Host 'readiness_stage=accepted_source_snapshot'
    $diskLine = Get-ClickHouseTsv "SELECT name, path, free_space, total_space FROM system.disks WHERE name = 'default'"
    $partsLine = Get-ClickHouseTsv 'SELECT countDistinct(table), count(), coalesce(sum(rows),0), coalesce(sum(bytes_on_disk),0) FROM system.parts WHERE active'
    if (-not $diskLine -or -not $partsLine) { throw 'Unable to collect read-only ClickHouse source capacity/baseline.' }
    $diskFields = $diskLine -split "`t"
    $partsFields = $partsLine -split "`t"
    if ($diskFields.Count -ne 4 -or $partsFields.Count -ne 4) { throw 'Unexpected ClickHouse source snapshot format.' }

    $sourceDisk = [ordered]@{
        name=[string]$diskFields[0]
        path=[string]$diskFields[1]
        free_bytes=[int64]$diskFields[2]
        total_bytes=[int64]$diskFields[3]
    }
    $sourceBaseline = [ordered]@{
        active_table_count=[int64]$partsFields[0]
        active_part_count=[int64]$partsFields[1]
        active_rows=[int64]$partsFields[2]
        active_bytes_on_disk=[int64]$partsFields[3]
    }

    Write-Host 'readiness_stage=raw_cold_identity'
    $rawData = Get-RawDataPathEvidence

    Write-Host 'readiness_stage=wsl_advisory_inventory'
    $wslVersion = Invoke-NativeText 'wsl.exe' @('--version') -AllowFailure
    $wslList = Invoke-NativeText 'wsl.exe' @('--list','--verbose') -AllowFailure
    foreach ($line in @($wslVersion['lines'])) { Write-Host "wsl_version_evidence=$line" }
    foreach ($line in @($wslList['lines'])) { Write-Host "wsl_distro_evidence=$line" }

    Write-Host 'readiness_stage=legacy_cutover_guard'
    $legacyPath = Join-Path $PSScriptRoot 'migrate-clickhouse-volume-to-hot.ps1'
    $legacyText = if (Test-Path -LiteralPath $legacyPath -PathType Leaf) { Get-Content -LiteralPath $legacyPath -Raw -Encoding UTF8 } else { '' }
    $legacyWindowsBindDetected = [bool](
        $legacyText.Contains('docker-compose.hot-cold-storage.yml') -or
        $legacyText.Contains('type=bind,source=')
    )

    $driveMap = @{}
    foreach ($drive in @($inventory.drives)) { $driveMap[[string]$drive.drive] = $drive }
    $blockers = @()
    $warnings = @()

    if ([string]$inventory.status -ne 'PASS') { $blockers += 'GLOBAL_MULTI_DISK_HOST_INVENTORY_NOT_PASS' }
    foreach ($letter in @('D:','E:','F:')) {
        if (-not $driveMap.ContainsKey($letter) -or -not [bool]$driveMap[$letter].present) {
            $blockers += "TARGET_DRIVE_MISSING_$($letter.TrimEnd(':'))"
        }
        elseif ([int64]$driveMap[$letter].free_bytes -le 0) {
            $blockers += "TARGET_DRIVE_HAS_NO_FREE_BYTES_$($letter.TrimEnd(':'))"
        }
    }
    if ([string]$inventory.current_clickhouse_volume.name -ne $AcceptedVolume) { $blockers += 'ACCEPTED_VOLUME_IDENTITY_MISMATCH' }
    if ([string]$inventory.current_clickhouse_volume.driver -ne 'local') { $blockers += 'ACCEPTED_VOLUME_DRIVER_NOT_LOCAL' }
    if (-not [bool]$rawData.configured) { $blockers += 'RAW_DATA_PATH_NOT_CONFIGURED' }
    elseif ([string]$rawData.drive_root -ne 'F:\') { $blockers += 'RAW_DATA_PATH_NOT_ON_F' }
    if ($sourceDisk.name -ne 'default' -or $sourceDisk.total_bytes -le 0) { $blockers += 'ACCEPTED_SOURCE_DISK_SNAPSHOT_INVALID' }
    if ($sourceBaseline.active_rows -le 0 -or $sourceBaseline.active_table_count -le 0) { $blockers += 'ACCEPTED_SOURCE_LOGICAL_BASELINE_INVALID' }
    if (-not $legacyWindowsBindDetected) { $warnings += 'LEGACY_WINDOWS_BIND_CUTOVER_SIGNATURE_NOT_DETECTED' }
    if ($wslVersion['exit_code'] -ne 0 -or $wslList['exit_code'] -ne 0) { $warnings += 'WSL_ADVISORY_INVENTORY_INCOMPLETE' }

    foreach ($letter in @('D:','E:','F:')) {
        if ($driveMap.ContainsKey($letter)) {
            $media = @($driveMap[$letter].physical_disks | ForEach-Object {
                if ($_.physical_media_type) { [string]$_.physical_media_type }
                elseif ($_.wmi_media_type) { [string]$_.wmi_media_type }
                else { 'Unknown' }
            })
            if ($media.Count -eq 0 -or $media -contains 'Unknown') { $warnings += "PHYSICAL_MEDIA_TYPE_INCOMPLETE_$($letter.TrimEnd(':'))" }
        }
    }

    $readyForSizing = [bool]($blockers.Count -eq 0)
    $decision = if ($readyForSizing) {
        'PRODUCTION_MULTI_DISK_MIGRATION_READINESS_READY_FOR_SIZING_PLAN'
    } else {
        'PRODUCTION_MULTI_DISK_MIGRATION_READINESS_BLOCKED'
    }

    $receipt = [ordered]@{
        receipt_version='PRODUCTION_MULTI_DISK_MIGRATION_READINESS_V1'
        decision=$decision
        read_only=$true
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        architecture_authority=[ordered]@{
            decision=$acceptedArchitectureDecision
            proof_main_sha=$acceptedArchitectureProofSha
            dedicated_wsl_clickhouse_required=$true
            docker_application_plane_allowed=$true
            stable_endpoint='host.docker.internal'
        }
        host_inventory_evidence=[ordered]@{
            strategy='SHALLOW_REPO_REPORTS'
            evidence_root=[string]$inventoryResult['evidence_root']
            receipt_path=[string]$inventoryResult['receipt_path']
        }
        production=[ordered]@{
            clickhouse_ready=[bool]$production['ready']
            clickhouse_health=[string]$production['health']
            worker_container_count=$workerCount
            accepted_volume_name=$AcceptedVolume
            accepted_volume_driver=[string]$inventory.current_clickhouse_volume.driver
            accepted_volume_mountpoint=[string]$inventory.current_clickhouse_volume.mountpoint
            source_disk=$sourceDisk
            source_baseline=$sourceBaseline
        }
        host_drives=@($inventory.drives)
        raw_cold=[ordered]@{
            raw_data_path_configured=[bool]$rawData.configured
            raw_data_path=[string]$rawData.configured_path
            raw_data_drive_root=[string]$rawData.drive_root
            raw_source_recopy_required=$false
            frozen_source_recopy_authorized=$false
            role='Raw/Cold/Archive/Snapshots/Recovery'
            clickhouse_primary_mergetree_allowed=$false
        }
        target_layout=[ordered]@{
            hot_cn=[ordered]@{ host_drive='D:'; filesystem='ext4'; capacity_bytes=$null; sizing_dependency='#262'; state='WAITING_FOR_SIZING_PLAN' }
            hot_us=[ordered]@{ host_drive='D:'; filesystem='ext4'; capacity_bytes=$null; sizing_dependency='#340'; state='WAITING_FOR_SIZING_PLAN' }
            hot_global=[ordered]@{ host_drive='D:'; filesystem='ext4'; capacity_bytes=$null; sizing_dependency='future jurisdiction evidence'; state='WAITING_FOR_SIZING_PLAN' }
            warm=[ordered]@{ host_drive='E:'; filesystem='ext4'; capacity_bytes=$null; sizing_dependency='#262/#340'; state='WAITING_FOR_SIZING_PLAN' }
            raw_cold=[ordered]@{ host_drive='F:'; filesystem='native_windows'; state='EXISTING_ROLE' }
        }
        migration_contract=[ordered]@{
            ready_for_sizing_plan=$readyForSizing
            live_migration_authorized=$false
            vhdx_create_authorized=$false
            vhdx_resize_authorized=$false
            vhdx_mount_authorized=$false
            source_volume_delete_authorized=$false
            legacy_windows_bind_cutover_authorized=$false
            legacy_windows_bind_signature_detected=$legacyWindowsBindDetected
            forward_only_copy_required=$true
            source_volume_retained_until_final_acceptance=$true
            parity_gate_required=$true
            rollback_gate_required=$true
            full_cn_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
        wsl_advisory=[ordered]@{
            runtime_distro=$RuntimeDistro
            version_exit=$wslVersion['exit_code']
            list_exit=$wslList['exit_code']
            version_lines=@($wslVersion['lines'])
            distro_lines=@($wslList['lines'])
        }
        blockers=@($blockers)
        warnings=@($warnings)
        vhdx_create_performed=$false
        vhdx_resize_performed=$false
        vhdx_mount_performed=$false
        vhdx_move_performed=$false
        wsl_unmount_performed=$false
        wsl_shutdown_performed=$false
        docker_restart_performed=$false
        docker_prune_performed=$false
        production_clickhouse_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        source_copy_performed=$false
        corpus_replay_performed=$false
    }

    $receiptPath = Join-Path $evidenceDir 'receipt.json'
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== PRODUCTION MULTI-DISK MIGRATION READINESS RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "architecture_decision=$acceptedArchitectureDecision"
    Write-Host "architecture_proof_main_sha=$acceptedArchitectureProofSha"
    Write-Host "ready_for_sizing_plan=$readyForSizing"
    Write-Host 'host_inventory_evidence_strategy=SHALLOW_REPO_REPORTS'
    Write-Host "host_inventory_receipt_path=$([string]$inventoryResult['receipt_path'])"
    foreach ($letter in @('D:','E:','F:')) {
        if ($driveMap.ContainsKey($letter)) {
            $key = $letter.TrimEnd(':')
            Write-Host "drive_${key}_total_bytes=$([int64]$driveMap[$letter].total_bytes)"
            Write-Host "drive_${key}_free_bytes=$([int64]$driveMap[$letter].free_bytes)"
            Write-Host "drive_${key}_filesystem=$([string]$driveMap[$letter].filesystem)"
        }
    }
    Write-Host "accepted_volume=$AcceptedVolume"
    Write-Host "source_default_disk_total_bytes=$($sourceDisk.total_bytes)"
    Write-Host "source_default_disk_free_bytes=$($sourceDisk.free_bytes)"
    Write-Host "source_active_rows=$($sourceBaseline.active_rows)"
    Write-Host "source_active_bytes_on_disk=$($sourceBaseline.active_bytes_on_disk)"
    Write-Host "raw_data_drive_root=$([string]$rawData.drive_root)"
    Write-Host "legacy_windows_bind_cutover_authorized=False"
    Write-Host "live_migration_authorized=False"
    Write-Host "vhdx_create_authorized=False"
    Write-Host "us_package_2_authorized=False"
    Write-Host "us_bulk_authorized=False"
    Write-Host "blocker_count=$($blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    foreach ($warning in $warnings) { Write-Host "warning=$warning" }
    Write-Host 'vhdx_create_performed=False'
    Write-Host 'vhdx_resize_performed=False'
    Write-Host 'vhdx_mount_performed=False'
    Write-Host 'vhdx_move_performed=False'
    Write-Host 'wsl_unmount_performed=False'
    Write-Host 'wsl_shutdown_performed=False'
    Write-Host 'docker_restart_performed=False'
    Write-Host 'docker_prune_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'source_copy_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_MULTI_DISK_MIGRATION_READINESS_DONE'

    Assert-ExactMain 'exit'
}
finally { Pop-Location }
