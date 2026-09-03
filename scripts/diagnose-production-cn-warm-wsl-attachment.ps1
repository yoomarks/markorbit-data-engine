[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,

    [Parameter(Mandatory = $true)]
    [string]$IncidentEvidenceDirectory,

    [string]$ToolingDistro = 'Ubuntu-24.04',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:DiagnosticIssue = 514
$script:BaseMainSha = 'cf9a2489f057b70b96c28cf35835f796eb6d4c74'
$script:IncidentEngineSha = '111908335714292ae4d42e54b3664156d19d64ca'
$script:IncidentJournalVersion = 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_JOURNAL_V1'
$script:RemediationJournalVersion = 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_JOURNAL_V2'
$script:DiagnosticReceiptVersion = 'PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_V2'
$script:ExpectedIncidentEvidenceDirectory = 'D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_phase_a_provisioning_20260903_072812'
$script:ExpectedWarmVhdxPath = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ExpectedWarmMountName = 'markorbit_prod_warm_cn'
$script:ExpectedWarmMountPath = '/mnt/wsl/markorbit_prod_warm_cn'
$script:ExpectedRuntimeDistro = 'MarkOrbit-ClickHouse'
$script:ExpectedRuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse'
$script:ExpectedETotalBytes = [int64]2048391114752
$script:ExpectedWarmVhdxMaxBytes = [int64]842887331840
$script:ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery'
$script:AllowedDiagnosticFiles = @(
    'scripts/diagnose-production-cn-warm-wsl-attachment.ps1',
    'tests/test_production_cn_warm_wsl_attachment_diagnostic_contract.py',
    '.github/workflows/production-cn-warm-wsl-attachment-diagnostic-runtime.yml'
)
$script:ProtectedPaths = @(
    'D:\MarkOrbitData\spike\hot_cn_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_us_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_global_spike.vhdx',
    'E:\MarkOrbitData\spike\warm_spike.vhdx',
    'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike\ext4.vhdx',
    'E:\MarkOrbitData\wsl-tooling\Ubuntu-24.04\ext4.vhdx',
    $script:ExpectedFRecoveryVhdx
)

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments,
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
        throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}

function Get-StringSha256([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Read-Json([string]$Path,[string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Write-Json([object]$Value,[string]$Path) {
    $Value | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Normalize-WindowsPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $value = $Path.Trim()
    if ($value.StartsWith('\\?\')) { $value = $value.Substring(4) }
    if ($value.StartsWith('\??\')) { $value = $value.Substring(4) }
    if ($value -notmatch '^[A-Za-z]:[\\/]') { return '' }
    return [IO.Path]::GetFullPath($value).TrimEnd('\')
}

function Test-SameWindowsPath([string]$A,[string]$B) {
    return (Normalize-WindowsPath $A).ToLowerInvariant() -eq (Normalize-WindowsPath $B).ToLowerInvariant()
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw "Exact main drift during $Phase." }
    if (git status --porcelain) { throw "Working tree not clean during $Phase." }
}

function Assert-DiagnosticBoundary {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:BaseMainSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Diagnostic base is not ancestor of current main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:BaseMainSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedDiagnosticFiles })
    $missing = @($script:AllowedDiagnosticFiles | Where-Object { $_ -notin $changed })
    Write-Host "diagnostic_changed_file_count=$($changed.Count)"
    Write-Host "diagnostic_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "diagnostic_missing_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) { throw 'Diagnostic changed outside exact 3-file boundary.' }
}

function Get-WslDistros {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $rows = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.DistributionName) { continue }
        $rows += [ordered]@{
            name=[string]$item.DistributionName
            version=if ($null -ne $item.Version) { [int]$item.Version } else { $null }
            base_path=if ($item.BasePath) { Normalize-WindowsPath ([string]$item.BasePath) } else { '' }
        }
    }
    return @($rows)
}

function Assert-RuntimeIdentity {
    $distros = @(Get-WslDistros)
    $runtime = @($distros | Where-Object { $_.name -eq $script:ExpectedRuntimeDistro })
    if ($runtime.Count -ne 1 -or $runtime[0].version -ne 2 -or -not (Test-SameWindowsPath $runtime[0].base_path $script:ExpectedRuntimeRoot)) {
        throw 'Production runtime identity mismatch.'
    }
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$script:ExpectedRuntimeDistro,'-u','root','--','sh','-lc','printf RUNTIME_OK') -AllowFailure
    if ($probe.exit_code -ne 0 -or ((@($probe.lines) -join '').Trim() -ne 'RUNTIME_OK')) { throw 'Production runtime is not startable.' }
    Write-Host 'production_runtime_identity_ready=True'
}

function Assert-RawConsumersStopped {
    $total = 0
    foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
        $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure
        if ($probe.exit_code -ne 0) { throw "Unable to inspect $service." }
        $running = 0
        foreach ($container in @($probe.lines | Where-Object { $_.Trim() })) {
            $state = Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$container.Trim()) -AllowFailure
            if ($state.exit_code -ne 0) { throw "Unable to inspect $service container state." }
            if (((@($state.lines) -join '').Trim().ToLowerInvariant()) -eq 'true') { $running++ }
        }
        $total += $running
        Write-Host "raw_consumer_service=$service running_count=$running"
    }
    Write-Host "running_raw_consumer_count=$total"
    if ($total -ne 0) { throw 'Consumers must remain stopped.' }
}

function Assert-SourceMount {
    $idProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($idProbe.lines) -join '').Trim()
    if (-not $containerId) { throw 'Source ClickHouse container missing.' }
    $health = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $one = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $version = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure
    if ($health.exit_code -ne 0 -or ((@($health.lines) -join '').Trim() -ne 'healthy') -or $one.exit_code -ne 0 -or ((@($one.lines) -join '').Trim() -ne '1') -or $version.exit_code -ne 0 -or ((@($version.lines) -join '').Trim() -ne '24.8.14.39')) {
        throw 'Source ClickHouse is not exact-version healthy.'
    }
    $mountProbe = Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$containerId) -AllowFailure
    if ($mountProbe.exit_code -ne 0) { throw 'Unable to inspect source mount.' }
    $mounts = ((@($mountProbe.lines) -join "`n") | ConvertFrom-Json)
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    $ready = [bool]($matches.Count -eq 1 -and [string]$matches[0].Type -eq 'volume' -and [string]$matches[0].Name -eq $AcceptedVolume)
    Write-Host "accepted_production_mount_ready=$ready"
    if (-not $ready) { throw 'Accepted source volume identity changed.' }
}

function Assert-ProtectedAndCapacity {
    foreach ($path in $script:ProtectedPaths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Protected path missing: $path" }
    }
    $recovery = New-Object IO.FileInfo($script:ExpectedFRecoveryVhdx)
    if ([int64]$recovery.Length -ne $script:ExpectedFRecoveryBytes) { throw 'F recovery VHDX changed.' }
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root reappeared.' }
    $warm = New-Object IO.FileInfo($script:ExpectedWarmVhdxPath)
    Write-Host "warm_vhdx_length_bytes=$($warm.Length)"
    $drive = New-Object IO.DriveInfo('E:\')
    if (-not $drive.IsReady -or [int64]$drive.TotalSize -ne $script:ExpectedETotalBytes) { throw 'E capacity identity drift.' }
    $reserve = [int64][math]::Ceiling([double]$drive.TotalSize * 0.30)
    $margin = [int64]($drive.AvailableFreeSpace - $script:ExpectedWarmVhdxMaxBytes - $reserve)
    Write-Host "e_total_bytes=$($drive.TotalSize)"
    Write-Host "e_free_bytes=$($drive.AvailableFreeSpace)"
    Write-Host "e_margin_after_proposed_max_bytes=$margin"
    if ($margin -lt 0) { throw 'E_30_PERCENT_RESERVE_ADMISSION_FAILED' }
}

function Resolve-IncidentState {
    $directory = [IO.Path]::GetFullPath($IncidentEvidenceDirectory)
    if (-not (Test-SameWindowsPath $directory $script:ExpectedIncidentEvidenceDirectory)) { throw 'Incident evidence directory mismatch.' }
    $incidentPath = Join-Path $directory 'production_cn_warm_phase_a_provisioning_journal.json'
    $incident = Read-Json $incidentPath 'Incident journal'
    if ([string]$incident.receipt_version -ne $script:IncidentJournalVersion -or [string]$incident.engine_sha -ne $script:IncidentEngineSha) { throw 'Incident journal identity drift.' }
    $uuid = ([string]$incident.ext4_uuid).Trim().ToLowerInvariant()
    if ($uuid -notmatch '^[0-9a-f-]{36}$') { throw 'Incident ext4 UUID invalid.' }

    $remediationPath = Join-Path $directory 'production_cn_warm_phase_a_mount_remediation_journal.json'
    $remediation = Read-Json $remediationPath 'Remediation journal'
    if ([string]$remediation.receipt_version -ne $script:RemediationJournalVersion -or [string]$remediation.engine_sha -ne $script:BaseMainSha) { throw 'Remediation journal identity drift.' }
    if ([string]$remediation.stage -ne 'blocked') { throw 'Remediation journal is not blocked.' }
    if ([string]$remediation.initial_mount_state -ne 'MOUNT_ALREADY_DETACHED') { throw 'Remediation journal did not record already-detached state.' }
    if ([bool]$remediation.exact_path_unmount_performed -or -not [bool]$remediation.exact_path_unmount_skipped_already_detached -or [bool]$remediation.named_remount_performed) { throw 'Unexpected remediation mount mutation state.' }
    $lastError = [string]$remediation.last_error
    if ([string]::IsNullOrWhiteSpace($lastError)) { throw 'Remediation journal blocked without last_error evidence.' }
    $lastErrorSha = Get-StringSha256 $lastError
    $knownMarkerPresent = [bool]($lastError -match 'WSL_E_DISK_ALREADY_MOUNTED')
    foreach ($name in @('no_arg_wsl_unmount_performed','wsl_shutdown_performed','runtime_distro_unregister_performed','target_vhdx_delete_performed','vhdx_create_performed','vhdx_format_performed','runtime_import_performed','docker_mutation_performed','accepted_volume_mutation_performed','source_clickhouse_mutation_performed','cn_data_transfer_performed','cross_runtime_transfer_performed','cn_warm_move_performed','source_cleanup_performed','cn_replay_performed','us_bulk_performed')) {
        if ([bool]($remediation.$name)) { throw "Forbidden remediation flag true: $name" }
    }
    Write-Host "incident_ext4_uuid=$uuid"
    Write-Host "remediation_last_error_sha256=$lastErrorSha"
    Write-Host "remediation_known_already_mounted_marker_present=$knownMarkerPresent"
    Write-Host 'remediation_structured_blocked_state_bound=True'
    return [ordered]@{
        dir=$directory
        uuid=$uuid
        incident_path=$incidentPath
        remediation_path=$remediationPath
        remediation_last_error_sha256=$lastErrorSha
        remediation_known_already_mounted_marker_present=$knownMarkerPresent
    }
}

function Get-UuidNamespaceProbe([string]$Distro,[string]$Uuid) {
    $scriptText = @"
set -eu
uuid='$Uuid'
echo '--- UUID_DEVICE ---'
blkid -t UUID="`$uuid" -o device 2>/dev/null || true
echo '--- LSBLK ---'
lsblk -b -P -o NAME,PATH,TYPE,FSTYPE,UUID,SIZE,MOUNTPOINTS 2>/dev/null || true
echo '--- UUID_FINDMNT ---'
for dev in `$(blkid -t UUID="`$uuid" -o device 2>/dev/null || true); do findmnt -rn -S "`$dev" -o SOURCE,TARGET,FSTYPE,OPTIONS 2>/dev/null || true; done
echo '--- EXPECTED_TARGET_FINDMNT ---'
findmnt -rn -T '$($script:ExpectedWarmMountPath)' -o SOURCE,TARGET,FSTYPE,OPTIONS 2>/dev/null || true
echo '--- MNT_WSL_FINDMNT ---'
findmnt -rn -R /mnt/wsl -o SOURCE,TARGET,FSTYPE,OPTIONS 2>/dev/null || true
echo '--- MNT_WSL_LS ---'
ls -la /mnt/wsl 2>/dev/null || true
"@
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','sh','-lc',$scriptText) -AllowFailure
    if ($probe.exit_code -ne 0) { throw "Namespace probe failed for $Distro." }
    $lines = @($probe.lines)
    $section = ''
    $devices = @()
    $uuidFindmnt = @()
    $targetFindmnt = @()
    $lsblk = @()
    $mntWslFindmnt = @()
    $mntWslLs = @()
    foreach ($line in $lines) {
        if ($line -eq '--- UUID_DEVICE ---') { $section='device'; continue }
        if ($line -eq '--- LSBLK ---') { $section='lsblk'; continue }
        if ($line -eq '--- UUID_FINDMNT ---') { $section='uuid_findmnt'; continue }
        if ($line -eq '--- EXPECTED_TARGET_FINDMNT ---') { $section='target_findmnt'; continue }
        if ($line -eq '--- MNT_WSL_FINDMNT ---') { $section='mnt_wsl_findmnt'; continue }
        if ($line -eq '--- MNT_WSL_LS ---') { $section='mnt_wsl_ls'; continue }
        switch ($section) {
            'device' { if ($line.Trim()) { $devices += $line.Trim() } }
            'lsblk' { if ($line.Trim()) { $lsblk += $line } }
            'uuid_findmnt' { if ($line.Trim()) { $uuidFindmnt += $line } }
            'target_findmnt' { if ($line.Trim()) { $targetFindmnt += $line } }
            'mnt_wsl_findmnt' { if ($line.Trim()) { $mntWslFindmnt += $line } }
            'mnt_wsl_ls' { if ($line.Trim()) { $mntWslLs += $line } }
        }
    }
    Write-Host "namespace=$Distro|uuid_device_count=$($devices.Count)|uuid_findmnt_count=$($uuidFindmnt.Count)|expected_target_findmnt_count=$($targetFindmnt.Count)"
    foreach ($device in $devices) { Write-Host "uuid_device=$Distro|$device" }
    foreach ($row in $uuidFindmnt) { Write-Host "uuid_findmnt=$Distro|$row" }
    foreach ($row in $targetFindmnt) { Write-Host "expected_target_findmnt=$Distro|$row" }
    return [ordered]@{
        distro=$Distro
        devices=@($devices)
        uuid_findmnt=@($uuidFindmnt)
        expected_target_findmnt=@($targetFindmnt)
        lsblk=@($lsblk)
        mnt_wsl_findmnt=@($mntWslFindmnt)
        mnt_wsl_ls=@($mntWslLs)
    }
}

function Classify-Attachment([object]$Tooling,[object]$Runtime) {
    $allDevices = @($Tooling.devices + $Runtime.devices | Sort-Object -Unique)
    $allMounts = @($Tooling.uuid_findmnt + $Runtime.uuid_findmnt | Sort-Object -Unique)
    $expectedTargets = @($Tooling.expected_target_findmnt + $Runtime.expected_target_findmnt | Sort-Object -Unique)
    if ($allDevices.Count -eq 0) { return 'UUID_DEVICE_ABSENT' }
    if ($expectedTargets.Count -gt 0) { return 'UUID_DEVICE_NAMED_MOUNT_VISIBLE' }
    if ($allMounts.Count -gt 0) { return 'UUID_DEVICE_MOUNTED_ELSEWHERE' }
    if ($Tooling.devices.Count -gt 0 -and $Runtime.devices.Count -gt 0) { return 'UUID_DEVICE_ATTACHED_UNMOUNTED_VISIBLE_BOTH' }
    return 'UUID_DEVICE_NAMESPACE_DIVERGENCE'
}

function Invoke-ContractFixture {
    if ($script:DiagnosticIssue -ne 514) { throw 'Diagnostic issue drift.' }
    if ($script:BaseMainSha -ne 'cf9a2489f057b70b96c28cf35835f796eb6d4c74') { throw 'Diagnostic base drift.' }
    if ($script:DiagnosticReceiptVersion -ne 'PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_V2') { throw 'Diagnostic receipt version drift.' }
    if ($script:AllowedDiagnosticFiles.Count -ne 3) { throw 'Diagnostic file boundary drift.' }
    Write-Host 'PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_CONTRACT_OK'
}

try {
    Write-Host '===== PRODUCTION CN WARM WSL ATTACHMENT DIAGNOSTIC ====='
    Write-Host "diagnostic_issue=$script:DiagnosticIssue"
    Write-Host 'diagnostic_only=True'
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    foreach ($marker in @(
        'wsl_mount_authorized=False',
        'wsl_unmount_authorized=False',
        'wsl_shutdown_authorized=False',
        'runtime_import_authorized=False',
        'runtime_unregister_authorized=False',
        'vhdx_mutation_authorized=False',
        'docker_mutation_authorized=False',
        'source_clickhouse_mutation_authorized=False',
        'cn_data_transfer_authorized=False',
        'cn_warm_move_authorized=False',
        'source_cleanup_authorized=False'
    )) { Write-Host $marker }

    if ($ContractOnly) { Invoke-ContractFixture; exit 0 }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Diagnostic must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-DiagnosticBoundary
    Assert-RawConsumersStopped
    Assert-SourceMount
    Assert-ProtectedAndCapacity
    Assert-RuntimeIdentity
    $state = Resolve-IncidentState

    $tooling = Get-UuidNamespaceProbe $ToolingDistro $state.uuid
    $runtime = Get-UuidNamespaceProbe $script:ExpectedRuntimeDistro $state.uuid
    $classification = Classify-Attachment $tooling $runtime
    Write-Host "attachment_classification=$classification"

    $evidenceDirectory = Join-Path $state.dir ("production_cn_warm_wsl_attachment_diagnostic_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    New-Item -ItemType Directory -Path $evidenceDirectory -Force | Out-Null
    $receiptPath = Join-Path $evidenceDirectory 'production_cn_warm_wsl_attachment_diagnostic.json'
    $receipt = [ordered]@{
        receipt_version=$script:DiagnosticReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_COMPLETE'
        next_gate='OPERATOR_REVIEW_OF_WSL_ATTACHMENT_DIAGNOSTIC'
        diagnostic_only=$true
        read_only=$true
        mutation_performed=$false
        incident_ext4_uuid=$state.uuid
        warm_vhdx_path=$script:ExpectedWarmVhdxPath
        expected_mount_path=$script:ExpectedWarmMountPath
        attachment_classification=$classification
        tooling=$tooling
        runtime=$runtime
        incident_journal_path=$state.incident_path
        remediation_journal_path=$state.remediation_path
        remediation_last_error_sha256=$state.remediation_last_error_sha256
        remediation_known_already_mounted_marker_present=[bool]$state.remediation_known_already_mounted_marker_present
        remediation_structured_blocked_state_bound=$true
        wsl_mount_performed=$false
        wsl_unmount_performed=$false
        wsl_shutdown_performed=$false
        runtime_import_performed=$false
        runtime_unregister_performed=$false
        vhdx_mutation_performed=$false
        docker_mutation_performed=$false
        source_clickhouse_mutation_performed=$false
        cn_data_transfer_performed=$false
        cn_warm_move_performed=$false
        source_cleanup_performed=$false
    }
    Write-Json $receipt $receiptPath

    Assert-RawConsumersStopped
    Assert-SourceMount
    Assert-ProtectedAndCapacity
    Assert-ExactMain 'final'
    Assert-ExactMain 'exit'

    Write-Host '===== PRODUCTION CN WARM WSL ATTACHMENT DIAGNOSTIC RESULT ====='
    Write-Host 'decision=PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_COMPLETE'
    Write-Host 'next_gate=OPERATOR_REVIEW_OF_WSL_ATTACHMENT_DIAGNOSTIC'
    Write-Host "attachment_classification=$classification"
    Write-Host "remediation_last_error_sha256=$($state.remediation_last_error_sha256)"
    Write-Host "remediation_known_already_mounted_marker_present=$([bool]$state.remediation_known_already_mounted_marker_present)"
    Write-Host 'remediation_structured_blocked_state_bound=True'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDirectory"
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'wsl_mount_performed=False'
    Write-Host 'wsl_unmount_performed=False'
    Write-Host 'vhdx_mutation_performed=False'
    Write-Host 'cn_data_transfer_performed=False'
    Write-Host 'cn_warm_move_performed=False'
    Write-Host 'source_cleanup_performed=False'
    Write-Host 'PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_DONE'
    exit 0
}
catch {
    Write-Host "PRODUCTION_CN_WARM_WSL_ATTACHMENT_DIAGNOSTIC_FAILED: $($_.Exception.Message)"
    exit 2
}
finally {
    Pop-Location
}