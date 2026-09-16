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

$script:ReceiptVersion = 'US_SECONDARY_FAMILY_HOT_PLACEMENT_AUDIT_V1'
$script:Database = 'markorbit_facts'
$script:TargetDistro = 'MarkOrbit-ClickHouse'
$script:TargetHost = '127.0.0.1'
$script:TargetPort = '29000'
$script:TargetDisk = 'hot_us'
$script:TargetPolicy = 'hot_us_only'
$script:ExpectedTables = @(
    'us_assignment_assignee_history',
    'us_assignment_assignor_history',
    'us_assignment_property_history',
    'us_assignment_record_history',
    'us_ttab_docket_history',
    'us_ttab_party_history',
    'us_ttab_proceeding_history',
    'us_ttab_property_history'
)

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
        throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)"
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
    if ($normalized -notmatch ' SYSTEM\.(TABLES|PARTS|DISKS|STORAGE_POLICIES)') {
        throw "$Label may read ClickHouse system metadata only."
    }
    foreach ($token in @(
        ' INSERT ', ' DELETE ', ' UPDATE ', ' CREATE ', ' DROP ', ' TRUNCATE ',
        ' OPTIMIZE ', ' ALTER ', ' MOVE ', ' ATTACH ', ' DETACH ', ' RENAME ',
        ' SYSTEM ', ' KILL '
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

function Invoke-SourceRows([string]$Sql, [string]$Label) {
    Assert-ReadOnlyMetadataSql $Sql $Label
    $probe = Invoke-NativeText 'docker' @(
        'compose', 'exec', '-T', 'clickhouse', 'clickhouse-client',
        '--query', ($Sql + ' FORMAT JSONEachRow')
    )
    return @(Convert-JsonLines $probe.lines $Label)
}

function Get-RunningWslNames {
    $probe = Invoke-NativeText 'wsl.exe' @('--list', '--running', '--quiet')
    return @(
        $probe.lines |
            ForEach-Object { ([string]$_).Replace([string][char]0, '').Trim() } |
            Where-Object { $_ }
    )
}

function Invoke-TargetRows([string]$Sql, [string]$Label) {
    Assert-ReadOnlyMetadataSql $Sql $Label
    $running = @(Get-RunningWslNames)
    if ($running -notcontains $script:TargetDistro) {
        throw "Target distro $($script:TargetDistro) is not already running; refusing to start it."
    }
    $probe = Invoke-NativeText 'wsl.exe' @(
        '-d', $script:TargetDistro, '-u', 'root', '--',
        'clickhouse', 'client', '--host', $script:TargetHost, '--port', $script:TargetPort,
        '--query', ($Sql + ' FORMAT JSONEachRow')
    )
    return @(Convert-JsonLines $probe.lines $Label)
}

function Get-SourceHealth {
    $ids = Invoke-NativeText 'docker' @('compose', 'ps', '--status', 'running', '-q', 'clickhouse')
    $containerIds = @($ids.lines | Where-Object { $_.Trim() })
    if ($containerIds.Count -ne 1) { throw "Expected one running source ClickHouse container; observed=$($containerIds.Count)" }
    $health = Invoke-NativeText 'docker' @('inspect', '--format', '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}', $containerIds[0])
    if (((@($health.lines) -join '').Trim().ToLowerInvariant()) -ne 'healthy') { throw 'Source ClickHouse is not healthy.' }
    $version = Invoke-NativeText 'docker' @('compose', 'exec', '-T', 'clickhouse', 'clickhouse-client', '--query', 'SELECT version() FORMAT TabSeparatedRaw')
    return (@($version.lines) -join '').Trim()
}

function Get-TargetHealth {
    $running = @(Get-RunningWslNames)
    if ($running -notcontains $script:TargetDistro) {
        throw "Target distro $($script:TargetDistro) is not already running; refusing to start it."
    }
    $server = Invoke-NativeText 'wsl.exe' @(
        '-d', $script:TargetDistro, '-u', 'root', '--',
        'pgrep', '-f', '[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml'
    )
    $pids = @($server.lines | Where-Object { $_.Trim() -match '^\d+$' })
    if ($pids.Count -ne 1) { throw "Expected exactly one accepted target ClickHouse server; observed=$($pids.Count)" }
    $version = Invoke-NativeText 'wsl.exe' @(
        '-d', $script:TargetDistro, '-u', 'root', '--',
        'clickhouse', 'client', '--host', $script:TargetHost, '--port', $script:TargetPort,
        '--query', 'SELECT version() FORMAT TabSeparatedRaw'
    )
    return (@($version.lines) -join '').Trim()
}

function Get-RowMap([object[]]$Rows, [string]$KeyName) {
    $map = @{}
    foreach ($row in @($Rows)) { $map[[string]$row.$KeyName] = $row }
    return $map
}

function Get-Sum([object[]]$Rows, [string]$Property) {
    [int64]$sum = 0
    foreach ($row in @($Rows)) { $sum += [int64]$row.$Property }
    return $sum
}

function Get-PlacementDecision([object[]]$TargetStates) {
    $unexpected = @($TargetStates | Where-Object { $_.state -eq 'UNEXPECTED_PLACEMENT_OR_EMPTY' })
    $absent = @($TargetStates | Where-Object { $_.state -eq 'ABSENT' })
    if ($unexpected.Count -gt 0) {
        return [ordered]@{ decision='US_SECONDARY_FAMILY_PLACEMENT_REVIEW_REQUIRED'; next_gate='REVIEW_TARGET_PLACEMENT_DRIFT' }
    }
    if ($absent.Count -gt 0) {
        return [ordered]@{ decision='US_SECONDARY_FAMILY_PLACEMENT_DESIGN_REQUIRED'; next_gate='DESIGN_TARGET_SCHEMA_AND_MIGRATION_PLAN' }
    }
    return [ordered]@{ decision='US_SECONDARY_FAMILY_TARGET_PRESENT'; next_gate='TARGET_FAMILY_ACCEPTANCE_REVIEW' }
}

function Get-ReserveCheck([int64]$Total, [int64]$Free, [int64]$AdditionalBytes) {
    [int64]$projectedFree = $Free - $AdditionalBytes
    [int64]$recommendedFloor = [math]::Ceiling([double]$Total * 0.30)
    [int64]$hardFloor = [math]::Ceiling([double]$Total * 0.20)
    return [ordered]@{
        projected_free = $projectedFree
        recommended_floor = $recommendedFloor
        hard_floor = $hardFloor
        recommended_fits = [bool]($projectedFree -ge $recommendedFloor)
        hard_fits = [bool]($projectedFree -ge $hardFloor)
    }
}

try {
    if ($ContractOnly) {
        if ($script:ExpectedTables.Count -ne 8 -or @($script:ExpectedTables | Select-Object -Unique).Count -ne 8) {
            throw 'Expected family table contract must contain exactly eight unique tables.'
        }
        if (@($script:ExpectedTables | Where-Object { $_.StartsWith('us_assignment_') }).Count -ne 4) { throw 'Assignment table contract drifted.' }
        if (@($script:ExpectedTables | Where-Object { $_.StartsWith('us_ttab_') }).Count -ne 4) { throw 'TTAB table contract drifted.' }
        Assert-ReadOnlyMetadataSql 'SELECT name FROM system.tables' 'contract read-only metadata query'
        $blocked = $false
        try { Assert-ReadOnlyMetadataSql (('AL' + 'TER TABLE markorbit_facts.x MO' + 'VE PARTITION 1 TO DISK hot_us')) 'contract forbidden query' }
        catch { $blocked = $true }
        if (-not $blocked) { throw 'Read-only SQL guard failed to reject mutation.' }
        $design = Get-PlacementDecision @([pscustomobject]@{ state='ABSENT' })
        $review = Get-PlacementDecision @([pscustomobject]@{ state='UNEXPECTED_PLACEMENT_OR_EMPTY' })
        $present = Get-PlacementDecision @([pscustomobject]@{ state='HOT_US' })
        if ($design.decision -ne 'US_SECONDARY_FAMILY_PLACEMENT_DESIGN_REQUIRED') { throw 'ABSENT decision contract drifted.' }
        if ($review.decision -ne 'US_SECONDARY_FAMILY_PLACEMENT_REVIEW_REQUIRED') { throw 'unexpected-placement decision contract drifted.' }
        if ($present.decision -ne 'US_SECONDARY_FAMILY_TARGET_PRESENT') { throw 'present decision contract drifted.' }
        $reserve = Get-ReserveCheck 1000 600 100
        if (-not $reserve.recommended_fits -or -not $reserve.hard_fits -or $reserve.projected_free -ne 500) { throw 'Reserve arithmetic contract drifted.' }
        Write-Host 'US_SECONDARY_FAMILY_HOT_PLACEMENT_CONTRACT_PASS'
        return
    }
    Assert-ExactMain 'entry'
    $sourceVersion = Get-SourceHealth
    $targetVersion = Get-TargetHealth
    $quotedTables = @($script:ExpectedTables | ForEach-Object { "'$_'" }) -join ','

    $tableSql = @"
SELECT name AS table, storage_policy, engine
FROM system.tables
WHERE database = '$($script:Database)' AND name IN ($quotedTables)
ORDER BY name
"@
    $partSql = @"
SELECT table, groupUniqArray(disk_name) AS disks, sum(rows) AS rows, sum(bytes_on_disk) AS bytes
FROM system.parts
WHERE active AND database = '$($script:Database)' AND table IN ($quotedTables)
GROUP BY table
ORDER BY table
"@
    $sourceTables = @(Invoke-SourceRows $tableSql 'source expected-table metadata')
    $sourceParts = @(Invoke-SourceRows $partSql 'source expected-table active parts')
    $targetTables = @(Invoke-TargetRows $tableSql 'target expected-table metadata')
    $targetParts = @(Invoke-TargetRows $partSql 'target expected-table active parts')

    $sourceDiskRows = @(Invoke-SourceRows @"
SELECT name, path, total_space, free_space
FROM system.disks
WHERE name = 'default'
"@ 'source default disk')
    if ($sourceDiskRows.Count -ne 1) { throw 'Source default disk metadata is not exact-one.' }

    $targetDiskRows = @(Invoke-TargetRows @"
SELECT name, path, total_space, free_space
FROM system.disks
WHERE name = '$($script:TargetDisk)'
"@ 'target hot_us disk')
    if ($targetDiskRows.Count -ne 1) { throw 'Target hot_us disk metadata is not exact-one.' }

    $targetPolicyRows = @(Invoke-TargetRows @"
SELECT policy_name, volume_name, volume_priority, disks
FROM system.storage_policies
WHERE policy_name = '$($script:TargetPolicy)'
ORDER BY volume_priority
"@ 'target hot_us_only policy')
    if ($targetPolicyRows.Count -ne 1) { throw 'Target hot_us_only policy metadata is not exact-one.' }
    if (@($targetPolicyRows[0].disks).Count -ne 1 -or [string]$targetPolicyRows[0].disks[0] -ne $script:TargetDisk) {
        throw 'Target hot_us_only policy no longer maps only to hot_us.'
    }

    $targetHotUsageRows = @(Invoke-TargetRows @"
SELECT countDistinct(table) AS tables, sum(rows) AS rows, sum(bytes_on_disk) AS bytes
FROM system.parts
WHERE active AND disk_name = '$($script:TargetDisk)'
"@ 'target hot_us active usage')
    if ($targetHotUsageRows.Count -ne 1) { throw 'Target hot_us usage metadata is not exact-one.' }

    $sourceTableMap = Get-RowMap $sourceTables 'table'
    $sourcePartMap = Get-RowMap $sourceParts 'table'
    $targetTableMap = Get-RowMap $targetTables 'table'
    $targetPartMap = Get-RowMap $targetParts 'table'

    $missingSource = @()
    foreach ($table in $script:ExpectedTables) {
        if (-not $sourceTableMap.ContainsKey($table) -or -not $sourcePartMap.ContainsKey($table)) {
            $missingSource += $table
        }
    }
    if ($missingSource.Count -gt 0) { throw "Accepted source family tables/parts are missing: $($missingSource -join ', ')" }

    $sourceDetails = @()
    $targetStates = @()
    foreach ($table in $script:ExpectedTables) {
        $sourceTable = $sourceTableMap[$table]
        $sourcePart = $sourcePartMap[$table]
        $sourceDetails += [ordered]@{
            table = $table
            family = if ($table.StartsWith('us_assignment_')) { 'ASSIGNMENT' } else { 'TTAB' }
            storage_policy = [string]$sourceTable.storage_policy
            disks = @($sourcePart.disks)
            rows = [int64]$sourcePart.rows
            bytes_on_disk = [int64]$sourcePart.bytes
        }

        if (-not $targetTableMap.ContainsKey($table)) {
            $targetStates += [ordered]@{ table=$table; state='ABSENT'; storage_policy=$null; disks=@(); rows=0; bytes_on_disk=0 }
            continue
        }
        $targetTable = $targetTableMap[$table]
        $targetPart = if ($targetPartMap.ContainsKey($table)) { $targetPartMap[$table] } else { $null }
        $disks = if ($null -ne $targetPart) { @($targetPart.disks) } else { @() }
        $rows = if ($null -ne $targetPart) { [int64]$targetPart.rows } else { [int64]0 }
        $bytes = if ($null -ne $targetPart) { [int64]$targetPart.bytes } else { [int64]0 }
        $correct = [bool](
            [string]$targetTable.storage_policy -eq $script:TargetPolicy -and
            $disks.Count -eq 1 -and [string]$disks[0] -eq $script:TargetDisk -and
            $rows -gt 0
        )
        $targetStates += [ordered]@{
            table=$table; state=if ($correct) { 'HOT_US' } else { 'UNEXPECTED_PLACEMENT_OR_EMPTY' }
            storage_policy=[string]$targetTable.storage_policy; disks=@($disks); rows=$rows; bytes_on_disk=$bytes
        }
    }

    $assignmentSource = @($sourceDetails | Where-Object { $_.family -eq 'ASSIGNMENT' })
    $ttabSource = @($sourceDetails | Where-Object { $_.family -eq 'TTAB' })
    $assignmentBytes = Get-Sum $assignmentSource 'bytes_on_disk'
    $assignmentRows = Get-Sum $assignmentSource 'rows'
    $ttabBytes = Get-Sum $ttabSource 'bytes_on_disk'
    $ttabRows = Get-Sum $ttabSource 'rows'
    [int64]$combinedBytes = $assignmentBytes + $ttabBytes

    $targetDisk = $targetDiskRows[0]
    [int64]$targetTotal = $targetDisk.total_space
    [int64]$targetFree = $targetDisk.free_space
    $reserve = Get-ReserveCheck $targetTotal $targetFree $combinedBytes
    [int64]$projectedFree = $reserve.projected_free
    [int64]$recommendedFloor = $reserve.recommended_floor
    [int64]$hardFloor = $reserve.hard_floor
    $recommendedFits = [bool]$reserve.recommended_fits
    $hardFits = [bool]$reserve.hard_fits

    $absent = @($targetStates | Where-Object { $_.state -eq 'ABSENT' })
    $unexpected = @($targetStates | Where-Object { $_.state -eq 'UNEXPECTED_PLACEMENT_OR_EMPTY' })
    $placementDecision = Get-PlacementDecision $targetStates
    $decision = [string]$placementDecision.decision
    $nextGate = [string]$placementDecision.next_gate

    Assert-ExactMain 'post-query'
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $receipt = [ordered]@{
        receipt_version = $script:ReceiptVersion
        generated_at = (Get-Date).ToUniversalTime().ToString('o')
        main_sha = $head
        decision = $decision
        next_gate = $nextGate
        read_only = $true
        mutation_performed = $false
        future_growth_projection_performed = $false
        source = [ordered]@{
            runtime = 'DOCKER_COMPOSE_LEGACY_SOURCE'
            clickhouse_version = $sourceVersion
            database = $script:Database
            default_disk = $sourceDiskRows[0]
            tables = @($sourceDetails)
            assignment = [ordered]@{ tables=$assignmentSource.Count; rows=$assignmentRows; bytes_on_disk=$assignmentBytes }
            ttab = [ordered]@{ tables=$ttabSource.Count; rows=$ttabRows; bytes_on_disk=$ttabBytes }
            combined_bytes_on_disk = $combinedBytes
        }
        target = [ordered]@{
            runtime = $script:TargetDistro
            clickhouse_version = $targetVersion
            database = $script:Database
            required_disk = $script:TargetDisk
            required_policy = $script:TargetPolicy
            disk = $targetDisk
            policy = $targetPolicyRows[0]
            active_hot_usage = $targetHotUsageRows[0]
            table_states = @($targetStates)
            absent_table_count = $absent.Count
            unexpected_table_count = $unexpected.Count
        }
        current_footprint_reserve_check = [ordered]@{
            basis = 'CURRENT_ACCEPTED_SOURCE_BYTES_ONLY_NO_GROWTH_PROJECTION'
            current_assignment_bytes = $assignmentBytes
            current_ttab_bytes = $ttabBytes
            current_combined_bytes = $combinedBytes
            target_total_space = $targetTotal
            target_free_space_before = $targetFree
            projected_free_after_equal_byte_placement = $projectedFree
            recommended_30pct_reserve_floor_bytes = $recommendedFloor
            hard_20pct_reserve_floor_bytes = $hardFloor
            recommended_30pct_current_footprint_fits = $recommendedFits
            hard_20pct_current_footprint_fits = $hardFits
            future_growth_sufficiency_claimed = $false
        }
        constraints = [ordered]@{
            create_table_authorized = $false
            alter_authorized = $false
            move_authorized = $false
            optimize_authorized = $false
            insert_or_copy_authorized = $false
            replay_authorized = $false
            source_delete_authorized = $false
            vhdx_mutation_authorized = $false
            wsl_lifecycle_mutation_authorized = $false
            docker_mutation_authorized = $false
            application_amplification_reuse_authorized = $false
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
    $receiptPath = Join-Path $root "us_secondary_family_hot_placement_$stamp.json"
    $json = $receipt | ConvertTo-Json -Depth 12
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $tmpPath = "$receiptPath.tmp"
    [System.IO.File]::WriteAllText($tmpPath, $json, $utf8)
    Move-Item -LiteralPath $tmpPath -Destination $receiptPath -Force

    Write-Host "receipt_version=$($script:ReceiptVersion)"
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "source_assignment_rows=$assignmentRows"
    Write-Host "source_assignment_bytes=$assignmentBytes"
    Write-Host "source_ttab_rows=$ttabRows"
    Write-Host "source_ttab_bytes=$ttabBytes"
    Write-Host "target_absent_table_count=$($absent.Count)"
    Write-Host "target_unexpected_table_count=$($unexpected.Count)"
    Write-Host "recommended_30pct_current_footprint_fits=$recommendedFits"
    Write-Host "hard_20pct_current_footprint_fits=$hardFits"
    Write-Host "future_growth_projection_performed=False"
    Write-Host "receipt_path=$receiptPath"
}
finally {
    Pop-Location
}
