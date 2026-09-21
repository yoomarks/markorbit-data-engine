[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:ReceiptVersion = 'GLOBAL_HOT_FOUNDATION_READINESS_V1'
$script:TargetDistro = 'MarkOrbit-ClickHouse'
$script:TargetHost = '127.0.0.1'
$script:TargetPort = '29000'
$script:HotGlobalDisk = 'hot_global'
$script:HotGlobalPolicy = 'hot_global_only'
$script:WarmCnDisk = 'warm_cn'
$script:WarmCnPolicy = 'warm_cn_only'
$script:HotUsDisk = 'hot_us'
$script:HotUsPolicy = 'hot_us_only'
$script:HotGlobalVhdx = 'E:\MarkOrbitData\production\clickhouse\hot_global.vhdx'

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,
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
        throw "$Command failed with exit code $($exitCode): $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code = $exitCode; lines = @($lines) }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift detected during $Phase. expected=$expected head=$head origin_main=$originMain"
    }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}

function Assert-ReadOnlyMetadataSql([string]$Sql, [string]$Label) {
    $normalized = ' ' + (($Sql -replace '\s+', ' ').Trim().ToUpperInvariant()) + ' '
    if (-not $normalized.TrimStart().StartsWith('SELECT ')) { throw "$Label is not a SELECT." }
    if ($normalized -notmatch ' SYSTEM\.(DISKS|STORAGE_POLICIES|PARTS)') {
        throw "$Label may read ClickHouse system metadata only."
    }
    foreach ($token in @(
        ' INSERT ', ' DELETE ', ' UPDATE ', ' CREATE ', ' DROP ', ' TRUNCATE ',
        ' OPTIMIZE ', ' ALTER ', ' MOVE ', ' ATTACH ', ' DETACH ', ' RENAME ', ' KILL '
    )) {
        if ($normalized.Contains($token)) { throw "$Label contains forbidden token: $($token.Trim())" }
    }
}

function Convert-JsonLines([string[]]$Lines, [string]$Label) {
    $rows = @()
    foreach ($line in @($Lines)) {
        $text = ([string]$line).Trim()
        if (-not $text) { continue }
        try { $rows += ($text | ConvertFrom-Json) }
        catch { throw "$Label returned invalid JSONEachRow: $text" }
    }
    return @($rows)
}

function Get-RunningWslNames {
    $probe = Invoke-NativeText 'wsl.exe' @('--list', '--running', '--quiet')
    return @(
        $probe.lines |
            ForEach-Object { ([string]$_).Replace([string][char]0, '').Trim() } |
            Where-Object { $_ }
    )
}

function Assert-TargetRunning {
    $running = @(Get-RunningWslNames)
    if ($running -notcontains $script:TargetDistro) {
        throw "Target distro $($script:TargetDistro) is not already running; refusing to start it."
    }
}

function Invoke-TargetRows([string]$Sql, [string]$Label) {
    Assert-ReadOnlyMetadataSql $Sql $Label
    Assert-TargetRunning
    $probe = Invoke-NativeText 'wsl.exe' @(
        '-d', $script:TargetDistro, '-u', 'root', '--',
        'clickhouse', 'client', '--host', $script:TargetHost, '--port', $script:TargetPort,
        '--query', ($Sql + ' FORMAT JSONEachRow')
    )
    return @(Convert-JsonLines $probe.lines $Label)
}

function Get-TargetVersion {
    Assert-TargetRunning
    $server = Invoke-NativeText 'wsl.exe' @(
        '-d', $script:TargetDistro, '-u', 'root', '--',
        'pgrep', '-f', '[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml'
    )
    $pids = @($server.lines | Where-Object { $_.Trim() -match '^\d+$' })
    if ($pids.Count -ne 1) { throw "Expected one accepted target ClickHouse server; observed=$($pids.Count)" }
    $version = Invoke-NativeText 'wsl.exe' @(
        '-d', $script:TargetDistro, '-u', 'root', '--',
        'clickhouse', 'client', '--host', $script:TargetHost, '--port', $script:TargetPort,
        '--query', 'SELECT version() FORMAT TabSeparatedRaw'
    )
    return (@($version.lines) -join '').Trim()
}

function Get-DriveFact([string]$DriveLetter) {
    $volume = Get-Volume -DriveLetter $DriveLetter -ErrorAction Stop
    $partition = Get-Partition -DriveLetter $DriveLetter -ErrorAction Stop
    $disk = $partition | Get-Disk -ErrorAction Stop
    return [ordered]@{
        drive = $DriveLetter
        file_system = [string]$volume.FileSystem
        total_bytes = [int64]$volume.Size
        free_bytes = [int64]$volume.SizeRemaining
        drive_type = [string]$volume.DriveType
        disk_number = [int]$disk.Number
        friendly_name = [string]$disk.FriendlyName
        bus_type = [string]$disk.BusType
        media_type = [string]$disk.MediaType
    }
}

function Get-ReserveState([int64]$Total, [int64]$Free) {
    [int64]$recommended = [math]::Ceiling([double]$Total * 0.30)
    [int64]$hard = [math]::Ceiling([double]$Total * 0.20)
    $state = if ($Free -lt $hard) {
        'BLOCKED_BELOW_HARD_RESERVE'
    }
    elseif ($Free -lt $recommended) {
        'BELOW_RECOMMENDED_RESERVE'
    }
    else {
        'READY'
    }
    return [ordered]@{
        state = $state
        recommended_30pct_floor_bytes = $recommended
        hard_20pct_floor_bytes = $hard
    }
}

function Get-VhdxFact {
    $exists = Test-Path -LiteralPath $script:HotGlobalVhdx -PathType Leaf
    if (-not $exists) {
        return [ordered]@{ path=$script:HotGlobalVhdx; exists=$false; file_length_bytes=0; last_write_utc=$null }
    }
    $item = Get-Item -LiteralPath $script:HotGlobalVhdx -ErrorAction Stop
    return [ordered]@{
        path = $item.FullName
        exists = $true
        file_length_bytes = [int64]$item.Length
        last_write_utc = $item.LastWriteTimeUtc.ToString('o')
    }
}

function Get-Ext4Fact([string]$Path) {
    if (-not $Path) { return [ordered]@{ checked=$false; fstype=$null; source=$null; target=$null } }
    $probe = Invoke-NativeText 'wsl.exe' @(
        '-d', $script:TargetDistro, '-u', 'root', '--',
        'findmnt', '-T', $Path, '-n', '-o', 'FSTYPE,SOURCE,TARGET'
    ) -AllowFailure
    if ($probe.exit_code -ne 0 -or @($probe.lines).Count -eq 0) {
        return [ordered]@{ checked=$true; fstype=$null; source=$null; target=$null }
    }
    $text = ((@($probe.lines) -join ' ').Trim() -replace '\s+', ' ')
    $parts = @($text.Split(' '))
    return [ordered]@{
        checked = $true
        fstype = if ($parts.Count -ge 1) { $parts[0] } else { $null }
        source = if ($parts.Count -ge 2) { $parts[1] } else { $null }
        target = if ($parts.Count -ge 3) { $parts[2] } else { $null }
    }
}

function Get-Decision(
    [object]$EReserve,
    [object[]]$DiskRows,
    [object[]]$PolicyRows,
    [object]$Vhdx,
    [object]$Ext4
) {
    $diskMap = @{}
    foreach ($row in @($DiskRows)) { $diskMap[[string]$row.name] = $row }
    $policyMap = @{}
    foreach ($row in @($PolicyRows)) { $policyMap[[string]$row.policy_name] = $row }

    $baselineOk = (
        $diskMap.ContainsKey($script:HotUsDisk) -and
        $diskMap.ContainsKey($script:WarmCnDisk) -and
        $policyMap.ContainsKey($script:HotUsPolicy) -and
        $policyMap.ContainsKey($script:WarmCnPolicy)
    )
    if (-not $baselineOk) {
        return [ordered]@{ decision='HOT_GLOBAL_READINESS_BLOCKED'; next_gate='RESTORE_ACCEPTED_TARGET_STORAGE_BASELINE'; reason='ACCEPTED_BASELINE_MISSING' }
    }
    if ([string]$EReserve.state -ne 'READY') {
        return [ordered]@{ decision='HOT_GLOBAL_READINESS_BLOCKED'; next_gate='REVIEW_E_PHYSICAL_CAPACITY'; reason=[string]$EReserve.state }
    }

    $diskPresent = $diskMap.ContainsKey($script:HotGlobalDisk)
    $policyPresent = $policyMap.ContainsKey($script:HotGlobalPolicy)
    if (-not $diskPresent -and -not $policyPresent -and -not [bool]$Vhdx.exists) {
        return [ordered]@{ decision='HOT_GLOBAL_PROVISIONING_PLAN_REQUIRED'; next_gate='FREEZE_MEASURED_HOT_GLOBAL_PROVISIONING_PLAN'; reason='HOT_GLOBAL_ABSENT' }
    }
    if (-not ($diskPresent -and $policyPresent -and [bool]$Vhdx.exists)) {
        return [ordered]@{ decision='HOT_GLOBAL_READINESS_BLOCKED'; next_gate='REVIEW_PARTIAL_HOT_GLOBAL_STATE'; reason='PARTIAL_HOT_GLOBAL_STATE' }
    }

    $hotDisk = $diskMap[$script:HotGlobalDisk]
    $hotPolicy = $policyMap[$script:HotGlobalPolicy]
    $policyDisks = @($hotPolicy.disks)
    $mappingOk = $policyDisks.Count -eq 1 -and [string]$policyDisks[0] -eq $script:HotGlobalDisk
    $pathOk = [string]$hotDisk.path -ne ''
    $ext4Ok = [bool]$Ext4.checked -and [string]$Ext4.fstype -eq 'ext4'
    if (-not ($mappingOk -and $pathOk -and $ext4Ok)) {
        return [ordered]@{ decision='HOT_GLOBAL_READINESS_BLOCKED'; next_gate='REVIEW_HOT_GLOBAL_IDENTITY_DRIFT'; reason='HOT_GLOBAL_IDENTITY_DRIFT' }
    }
    return [ordered]@{ decision='HOT_GLOBAL_FOUNDATION_ACCEPTED'; next_gate='JURISDICTION_CAPACITY_AND_SCHEMA_REVIEW'; reason='HOT_GLOBAL_IDENTITY_ACCEPTED' }
}

try {
    if ($ContractOnly) {
        Assert-ReadOnlyMetadataSql 'SELECT name FROM system.disks' 'contract disk query'
        Assert-ReadOnlyMetadataSql 'SELECT policy_name FROM system.storage_policies' 'contract policy query'
        Assert-ReadOnlyMetadataSql 'SELECT disk_name FROM system.parts' 'contract parts query'
        $reserve = Get-ReserveState 1000 400
        if ($reserve.state -ne 'READY') { throw 'Reserve arithmetic contract drifted.' }
        $disks = @([pscustomobject]@{ name='hot_us'; path='/hot-us' }, [pscustomobject]@{ name='warm_cn'; path='/warm-cn' })
        $policies = @([pscustomobject]@{ policy_name='hot_us_only'; disks=@('hot_us') }, [pscustomobject]@{ policy_name='warm_cn_only'; disks=@('warm_cn') })
        $absent = Get-Decision ([pscustomobject]@{ state='READY' }) $disks $policies ([pscustomobject]@{ exists=$false }) ([pscustomobject]@{ checked=$false; fstype=$null })
        if ($absent.decision -ne 'HOT_GLOBAL_PROVISIONING_PLAN_REQUIRED') { throw 'Absent-state decision drifted.' }
        Write-Host 'GLOBAL_HOT_FOUNDATION_READINESS_CONTRACT_PASS'
        return
    }

    Assert-ExactMain 'entry'
    $targetVersion = Get-TargetVersion
    $eDrive = Get-DriveFact 'E'
    $eReserve = Get-ReserveState $eDrive.total_bytes $eDrive.free_bytes
    $vhdx = Get-VhdxFact

    $diskRows = @(Invoke-TargetRows @"
SELECT name, path, total_space, free_space
FROM system.disks
WHERE name IN ('hot_us','warm_cn','hot_global')
ORDER BY name
"@ 'target governed disks')
    $policyRows = @(Invoke-TargetRows @"
SELECT policy_name, volume_name, volume_priority, disks
FROM system.storage_policies
WHERE policy_name IN ('hot_us_only','warm_cn_only','hot_global_only')
ORDER BY policy_name, volume_priority
"@ 'target governed policies')
    $partRows = @(Invoke-TargetRows @"
SELECT disk_name, countDistinct(table) AS tables, sum(rows) AS rows, sum(bytes_on_disk) AS bytes
FROM system.parts
WHERE active AND disk_name = 'hot_global'
GROUP BY disk_name
"@ 'hot_global active parts')

    $hotGlobalDisk = @($diskRows | Where-Object { [string]$_.name -eq $script:HotGlobalDisk })
    $ext4 = if ($hotGlobalDisk.Count -eq 1) {
        Get-Ext4Fact ([string]$hotGlobalDisk[0].path)
    }
    else {
        [ordered]@{ checked=$false; fstype=$null; source=$null; target=$null }
    }

    $decision = Get-Decision $eReserve $diskRows $policyRows $vhdx $ext4
    Assert-ExactMain 'post-query'
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()

    $receipt = [ordered]@{
        receipt_version = $script:ReceiptVersion
        generated_at = (Get-Date).ToUniversalTime().ToString('o')
        main_sha = $head
        decision = [string]$decision.decision
        next_gate = [string]$decision.next_gate
        reason = [string]$decision.reason
        read_only = $true
        mutation_performed = $false
        target = [ordered]@{
            distro = $script:TargetDistro
            clickhouse_version = $targetVersion
            disks = @($diskRows)
            policies = @($policyRows)
            hot_global_active_parts = @($partRows)
            hot_global_ext4 = $ext4
        }
        host = [ordered]@{
            e_drive = $eDrive
            e_reserve = $eReserve
            expected_hot_global_vhdx = $vhdx
        }
        governance = [ordered]@{
            physical_role = 'E_GLOBAL_HOT_AND_ALL_WARM_GROWTH'
            default_other_hot_placement = 'hot_global'
            allocation_basis = 'MEASURED_APPROVED_BYTES_ONLY'
            guessed_equal_partitioning = $false
            us_application_amplification_reused = $false
            jurisdiction_capacity_claimed = $false
        }
        constraints = [ordered]@{
            vhdx_change_authorized = $false
            wsl_lifecycle_change_authorized = $false
            clickhouse_schema_or_data_change_authorized = $false
            docker_lifecycle_change_authorized = $false
            source_copy_replay_delete_authorized = $false
        }
    }

    $root = if ([System.IO.Path]::IsPathRooted($EvidenceRoot)) {
        [System.IO.Path]::GetFullPath($EvidenceRoot)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))
    }
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $receiptPath = Join-Path $root "global_hot_foundation_readiness_$stamp.json"
    $json = $receipt | ConvertTo-Json -Depth 12
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $tmpPath = "$receiptPath.tmp"
    [System.IO.File]::WriteAllText($tmpPath, $json, $utf8)
    Move-Item -LiteralPath $tmpPath -Destination $receiptPath -Force

    Write-Host "receipt_version=$($script:ReceiptVersion)"
    Write-Host "decision=$($decision.decision)"
    Write-Host "next_gate=$($decision.next_gate)"
    Write-Host "reason=$($decision.reason)"
    Write-Host "e_total_bytes=$($eDrive.total_bytes)"
    Write-Host "e_free_bytes=$($eDrive.free_bytes)"
    Write-Host "e_reserve_state=$($eReserve.state)"
    Write-Host "hot_global_vhdx_exists=$($vhdx.exists)"
    Write-Host "receipt_path=$receiptPath"
}
finally {
    Pop-Location
}
