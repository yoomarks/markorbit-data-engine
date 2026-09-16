[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$PythonExe = 'python',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:ReceiptVersion = 'US_SECONDARY_FAMILY_MIGRATION_DESIGN_V1'
$script:Database = 'markorbit_facts'
$script:TargetDisk = 'hot_us'
$script:TargetPolicy = 'hot_us_only'
$script:TransferStrategy = 'TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE'
$script:PlacementDecision = 'US_SECONDARY_FAMILY_PLACEMENT_DESIGN_REQUIRED'
$script:PlacementReceiptVersion = 'US_SECONDARY_FAMILY_HOT_PLACEMENT_AUDIT_V1'
$script:ExpectedSortKeys = [ordered]@{
    us_assignment_record_history = 'reel_frame_id, source_rank, source_package_id'
    us_assignment_assignor_history = 'reel_frame_id, party_key, source_rank, source_package_id'
    us_assignment_assignee_history = 'reel_frame_id, party_key, source_rank, source_package_id'
    us_assignment_property_history = 'serial_number, reel_frame_id, property_key, source_rank, source_package_id'
    us_ttab_proceeding_history = 'proceeding_number, source_rank, source_package_id'
    us_ttab_party_history = 'proceeding_number, side, party_key, source_rank, source_package_id'
    us_ttab_property_history = 'serial_number, proceeding_number, property_key, source_rank, source_package_id'
    us_ttab_docket_history = 'proceeding_number, docket_key, source_rank, source_package_id'
}
$script:ExpectedTables = @($script:ExpectedSortKeys.Keys)
$script:RepoFiles = [ordered]@{
    assignment = @('database/clickhouse/init/009_us_assignment_m10.sql')
    ttab = @(
        'database/clickhouse/init/010_us_ttab_m10.sql',
        'database/clickhouse/init/011_us_ttab_m11_real_rawxml.sql',
        'database/clickhouse/init/012_us_ttab_m12_official_bulk.sql'
    )
}

function Invoke-NativeText {
    param([string]$Command, [AllowEmptyString()][AllowEmptyCollection()][string[]]$Arguments, [switch]$AllowFailure)
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

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift during $Phase. expected=$expected head=$head origin_main=$originMain"
    }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}
function Get-StringSha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Convert-JsonLines([string[]]$Lines, [string]$Label) {
    $rows = @()
    foreach ($line in @($Lines)) {
        $text = ([string]$line).Trim()
        if (-not $text) { continue }
        try { $rows += ($text | ConvertFrom-Json) }
        catch { throw "$Label returned non-JSON metadata: $text" }
    }
    return @($rows)
}

function Assert-ReadOnlyMetadataSql([string]$Sql, [string]$Label) {
    $normalized = ' ' + (($Sql -replace '\s+', ' ').Trim().ToUpperInvariant()) + ' '
    if (-not $normalized.TrimStart().StartsWith('SELECT ')) { throw "$Label is not a SELECT." }
    if ($normalized -notmatch ' SYSTEM\.(TABLES|PARTS|COLUMNS)') {
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
function Invoke-SourceRows([string]$Sql, [string]$Label) {
    Assert-ReadOnlyMetadataSql $Sql $Label
    $probe = Invoke-NativeText 'docker' @(
        'compose', 'exec', '-T', 'clickhouse', 'clickhouse-client',
        '--query', $Sql, '--format', 'JSONEachRow'
    )
    return @(Convert-JsonLines $probe.lines $Label)
}

function Get-RepoColumnContract {
    $code = "import json; from app.us_assignment.publisher import TABLE_COLUMNS as a; from app.us_ttab.publisher import TABLE_COLUMNS as t; d={}; [d.__setitem__(k.split('.')[-1], list(v)+['observed_at']) for m in (a,t) for k,v in m.items()]; print(json.dumps(d,separators=(',',':')))"
    $probe = Invoke-NativeText $PythonExe @('-c', $code)
    $text = (@($probe.lines) -join '').Trim()
    if (-not $text) { throw 'Repository publisher column contract was empty.' }
    try { return ($text | ConvertFrom-Json) }
    catch { throw "Repository publisher column contract JSON invalid: $($_.Exception.Message)" }
}

function Get-Family([string]$TableName) {
    if ($TableName.StartsWith('us_assignment_')) { return 'ASSIGNMENT' }
    if ($TableName.StartsWith('us_ttab_')) { return 'TTAB' }
    throw "Unknown secondary US family table: $TableName"
}
function Get-RepoSchemaText([string]$Family) {
    $files = if ($Family -eq 'ASSIGNMENT') { $script:RepoFiles.assignment } else { $script:RepoFiles.ttab }
    $chunks = @()
    foreach ($file in @($files)) {
        $path = Join-Path $repoRoot $file
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Repository schema file missing: $file" }
        $chunks += Get-Content -LiteralPath $path -Raw -Encoding UTF8
    }
    return ($chunks -join "`n")
}

function Test-RepoColumnType([string]$RepoText, [string]$Name, [string]$Type) {
    $nameEsc = [regex]::Escape($Name)
    $typeEsc = [regex]::Escape($Type)
    $pattern = "(?im)^\s*(?:ADD COLUMN IF NOT EXISTS\s+)?${nameEsc}\s+${typeEsc}(?:\s|,)"
    return [bool]([regex]::IsMatch($RepoText, $pattern))
}

function Convert-ToTargetDdl([string]$SourceDdl) {
    if ($SourceDdl -notmatch '^CREATE TABLE markorbit_facts\.us_(assignment|ttab)_[a-z0-9_]+') {
        throw 'Source DDL is outside the accepted secondary-family table namespace.'
    }
    if ($SourceDdl -match '(?i)storage_policy\s*=') { throw 'Source DDL unexpectedly already contains storage_policy.' }
    if ($SourceDdl -match '(?i)\sSETTINGS\s') {
        return ($SourceDdl -replace '(?i)\sSETTINGS\s', " SETTINGS storage_policy = '$($script:TargetPolicy)', ")
    }
    return "$SourceDdl SETTINGS storage_policy = '$($script:TargetPolicy)'"
}
function Get-PartContentFingerprint([object[]]$Parts) {
    $lines = @()
    foreach ($part in @($Parts | Sort-Object name)) {
        $lines += @(
            [string]$part.name,
            [string]$part.rows,
            [string]$part.bytes_on_disk,
            [string]$part.hash_of_all_files,
            [string]$part.hash_of_uncompressed_files,
            [string]$part.uncompressed_hash_of_compressed_files
        ) -join '|'
    }
    return Get-StringSha256 ($lines -join "`n")
}

function Get-ResidencyFingerprint([object[]]$Parts) {
    $lines = @()
    foreach ($part in @($Parts | Sort-Object name)) {
        $lines += "$([string]$part.name)|$([string]$part.disk_name)"
    }
    return Get-StringSha256 ($lines -join "`n")
}

function Get-LogicalChecksumSql([string]$TableName) {
    if ($TableName -notmatch '^us_(assignment|ttab)_[a-z0-9_]+$') { throw "Unsafe table name: $TableName" }
    return "SELECT count() AS rows, sum(cityHash64(tuple(*))) AS checksum_sum, groupBitXor(cityHash64(tuple(*))) AS checksum_xor FROM markorbit_facts.$TableName"
}
function Invoke-ContractFixture {
    if ($script:ExpectedTables.Count -ne 8) { throw 'Expected exactly eight secondary-family tables.' }
    if ($script:TransferStrategy -ne 'TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE') {
        throw 'Transfer strategy drifted.'
    }
    $sample = "CREATE TABLE markorbit_facts.us_ttab_proceeding_history (`id` String) ENGINE = MergeTree ORDER BY id SETTINGS index_granularity = 8192"
    $target = Convert-ToTargetDdl $sample
    if ($target -notmatch "storage_policy = 'hot_us_only'") { throw 'Target DDL lacks hot_us_only.' }
    if ($target -notmatch 'index_granularity = 8192') { throw 'Target DDL lost source engine settings.' }
    if ($target -match 'IF NOT EXISTS') { throw 'Target DDL must fail closed on unexpected pre-existing tables.' }
    $checksum = Get-LogicalChecksumSql 'us_assignment_record_history'
    if ($checksum -notmatch '^SELECT count\(\) AS rows') { throw 'Logical checksum SQL changed.' }
    Write-Host 'US_SECONDARY_FAMILY_MIGRATION_DESIGN_CONTRACT_PASS'
    Write-Host "transfer_strategy=$($script:TransferStrategy)"
    Write-Host 'future_apply_authorized=False'
    Write-Host 'future_growth_projection_performed=False'
}

if ($ContractOnly) {
    try { Invoke-ContractFixture }
    finally { Pop-Location }
    return
}

function Resolve-PlacementReceipt([string]$EvidenceDir) {
    $placementRoot = Join-Path $EvidenceDir 'placement'
    $placementScript = Join-Path $repoRoot 'scripts\audit-us-secondary-family-hot-placement.ps1'
    if (-not (Test-Path -LiteralPath $placementScript -PathType Leaf)) { throw 'Placement audit operator is missing.' }
    $probe = Invoke-NativeText 'powershell.exe' @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $placementScript,
        '-ExpectedMainSha', $ExpectedMainSha, '-EvidenceRoot', $placementRoot
    )
    $paths = @(
        $probe.lines |
            Where-Object { $_ -like 'receipt_path=*' } |
            ForEach-Object { $_.Substring('receipt_path='.Length).Trim() }
    )
    if ($paths.Count -ne 1) { throw "Placement audit did not emit exactly one receipt path; observed=$($paths.Count)" }
    $path = [System.IO.Path]::GetFullPath($paths[0])
    $receipt = Read-JsonFile $path 'Placement audit receipt'
    if ([string]$receipt.receipt_version -ne $script:PlacementReceiptVersion) { throw 'Unexpected placement receipt version.' }
    if ([string]$receipt.decision -ne $script:PlacementDecision) { throw "Placement decision is not design-required: $($receipt.decision)" }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Placement receipt safety state changed.' }
    if ([string]$receipt.main_sha -ne $ExpectedMainSha.ToLowerInvariant()) { throw 'Placement receipt main SHA drifted.' }
    if ([int]$receipt.target.absent_table_count -ne 8 -or [int]$receipt.target.unexpected_table_count -ne 0) {
        throw 'Placement receipt target absence contract changed.'
    }
    return [ordered]@{ path=$path; sha256=(Get-FileSha256 $path); receipt=$receipt }
}
function Get-ExpectedColumnNames([object]$Contract, [string]$TableName) {
    $property = $Contract.PSObject.Properties[$TableName]
    if ($null -eq $property) { throw "Repository publisher contract missing table: $TableName" }
    return @($property.Value | ForEach-Object { [string]$_ })
}

try {
    Assert-ExactMain 'design-entry'
    $pythonCommand = Get-Command $PythonExe -ErrorAction Stop
    if (-not $pythonCommand) { throw "Python executable not found: $PythonExe" }

    $root = if ([System.IO.Path]::IsPathRooted($EvidenceRoot)) {
        [System.IO.Path]::GetFullPath($EvidenceRoot)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))
    }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir = Join-Path $root "us_secondary_family_migration_design_$stamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null

    $placement = Resolve-PlacementReceipt $evidenceDir
    $quotedTables = @($script:ExpectedTables | ForEach-Object { "'$_'" }) -join ','
    $tableSql = @"
SELECT name AS table, engine, sorting_key, primary_key, partition_key, create_table_query
FROM system.tables
WHERE database = '$($script:Database)' AND name IN ($quotedTables)
ORDER BY name
"@
    $columnSql = @"
SELECT table, name, type, position, default_kind, default_expression
FROM system.columns
WHERE database = '$($script:Database)' AND table IN ($quotedTables)
ORDER BY table, position
"@
    $partSql = @"
SELECT table, partition_id, name, rows, bytes_on_disk, disk_name,
       hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files
FROM system.parts
WHERE database = '$($script:Database)' AND active AND table IN ($quotedTables)
ORDER BY table, partition_id, name
"@
    $sourceTables = @(Invoke-SourceRows $tableSql 'source secondary-family table metadata')
    $sourceColumns = @(Invoke-SourceRows $columnSql 'source secondary-family column metadata')
    $sourceParts = @(Invoke-SourceRows $partSql 'source secondary-family part metadata')
    if ($sourceTables.Count -ne 8) { throw "Expected 8 live source tables; observed=$($sourceTables.Count)" }
    $repoContract = Get-RepoColumnContract
    $repoSchemaText = [ordered]@{
        ASSIGNMENT = Get-RepoSchemaText 'ASSIGNMENT'
        TTAB = Get-RepoSchemaText 'TTAB'
    }
    $repoFileEvidence = @()
    foreach ($familyKey in @('assignment','ttab')) {
        foreach ($file in @($script:RepoFiles[$familyKey])) {
            $path = Join-Path $repoRoot $file
            $repoFileEvidence += [pscustomobject][ordered]@{
                path=$file
                sha256=(Get-FileSha256 $path)
            }
        }
    }

    $blockers = @()
    $plans = @()
    foreach ($tableName in $script:ExpectedTables) {
        $family = Get-Family $tableName
        $tableMeta = @($sourceTables | Where-Object { [string]$_.table -eq $tableName })
        $columns = @($sourceColumns | Where-Object { [string]$_.table -eq $tableName } | Sort-Object { [int]$_.position })
        $parts = @($sourceParts | Where-Object { [string]$_.table -eq $tableName })
        if ($tableMeta.Count -ne 1) { $blockers += "SOURCE_TABLE_METADATA_COUNT:$tableName"; continue }
        if ($columns.Count -eq 0) { $blockers += "SOURCE_COLUMNS_EMPTY:$tableName"; continue }
        if ($parts.Count -eq 0) { $blockers += "SOURCE_ACTIVE_PARTS_EMPTY:$tableName"; continue }
        $expectedColumns = @(Get-ExpectedColumnNames $repoContract $tableName)
        $actualColumns = @($columns | ForEach-Object { [string]$_.name })
        if (($expectedColumns -join '|') -ne ($actualColumns -join '|')) {
            $blockers += "REPO_COLUMN_ORDER_DRIFT:$tableName"
        }
        foreach ($column in $columns) {
            if (-not (Test-RepoColumnType ([string]$repoSchemaText[$family]) ([string]$column.name) ([string]$column.type))) {
                $blockers += "REPO_COLUMN_TYPE_DRIFT:${tableName}:$([string]$column.name)"
            }
        }
        $expectedSort = [string]$script:ExpectedSortKeys[$tableName]
        if ([string]$tableMeta[0].engine -ne 'MergeTree') { $blockers += "SOURCE_ENGINE_DRIFT:$tableName" }
        if ([string]$tableMeta[0].sorting_key -ne $expectedSort) { $blockers += "SOURCE_SORT_KEY_DRIFT:$tableName" }
        if ([string]$tableMeta[0].primary_key -ne $expectedSort) { $blockers += "SOURCE_PRIMARY_KEY_DRIFT:$tableName" }
        if (-not [string]::IsNullOrEmpty([string]$tableMeta[0].partition_key)) { $blockers += "SOURCE_PARTITION_KEY_DRIFT:$tableName" }

        $diskNames = @($parts | ForEach-Object { [string]$_.disk_name } | Sort-Object -Unique)
        if ($diskNames.Count -ne 1 -or $diskNames[0] -ne 'default') { $blockers += "SOURCE_DISK_DRIFT:$tableName" }
        $partitionIds = @($parts | ForEach-Object { [string]$_.partition_id } | Sort-Object -Unique)
        if ($partitionIds.Count -ne 1 -or $partitionIds[0] -ne 'all') { $blockers += "SOURCE_PARTITION_LAYOUT_DRIFT:$tableName" }
        $rows = [int64](($parts | Measure-Object -Property rows -Sum).Sum)
        $bytes = [int64](($parts | Measure-Object -Property bytes_on_disk -Sum).Sum)
        $placementTable = @($placement.receipt.source.tables | Where-Object { [string]$_.table -eq $tableName })
        if ($placementTable.Count -ne 1) { $blockers += "PLACEMENT_TABLE_MISSING:$tableName" }
        else {
            if ($rows -ne [int64]$placementTable[0].rows) { $blockers += "SOURCE_ROW_DRIFT:$tableName" }
            if ($bytes -ne [int64]$placementTable[0].bytes_on_disk) { $blockers += "SOURCE_BYTE_DRIFT:$tableName" }
        }

        $columnCanonical = @(
            $columns | ForEach-Object {
                "$([string]$_.position)|$([string]$_.name)|$([string]$_.type)|$([string]$_.default_kind)|$([string]$_.default_expression)"
            }
        ) -join "`n"
        $sourceDdl = [string]$tableMeta[0].create_table_query
        $schemaFingerprint = Get-StringSha256 ($sourceDdl + "`n" + $columnCanonical)
        $targetDdl = Convert-ToTargetDdl $sourceDdl
        $targetDdlSha = Get-StringSha256 $targetDdl

        $plans += [pscustomobject][ordered]@{
            family=$family
            table=$tableName
            rows=$rows
            bytes_on_disk=$bytes
            active_parts=[int64]$parts.Count
            partition_ids=@($partitionIds)
            source_disk='default'
            target_disk=$script:TargetDisk
            target_storage_policy=$script:TargetPolicy
            engine=[string]$tableMeta[0].engine
            sorting_key=[string]$tableMeta[0].sorting_key
            primary_key=[string]$tableMeta[0].primary_key
            partition_key=[string]$tableMeta[0].partition_key
            source_schema_fingerprint_sha256=$schemaFingerprint
            source_part_content_manifest_sha256=(Get-PartContentFingerprint $parts)
            source_residency_manifest_sha256=(Get-ResidencyFingerprint $parts)
            repo_column_contract_match=(($expectedColumns -join '|') -eq ($actualColumns -join '|'))
            source_create_table_query=$sourceDdl
            target_create_table_query=$targetDdl
            target_create_table_query_sha256=$targetDdlSha
            logical_checksum_sql=(Get-LogicalChecksumSql $tableName)
            logical_checksum_execution_performed=$false
            transfer_unit='WHOLE_TABLE'
            future_transfer_authorized=$false
        }
    }

    $orderedPlans = @(
        $plans | Sort-Object `
            @{Expression={ if ($_.family -eq 'ASSIGNMENT') { 0 } else { 1 } }}, `
            @{Expression={ [int64]$_.bytes_on_disk }}, `
            @{Expression={ [string]$_.table }}
    )
    for ($i = 0; $i -lt $orderedPlans.Count; $i++) {
        $orderedPlans[$i] | Add-Member -NotePropertyName migration_order -NotePropertyValue ($i + 1)
        if ([string]$orderedPlans[$i].target_create_table_query -notmatch "storage_policy = 'hot_us_only'") {
            $blockers += "TARGET_DDL_POLICY_MISSING:$([string]$orderedPlans[$i].table)"
        }
        if ([string]$orderedPlans[$i].target_create_table_query -match '(?i)IF NOT EXISTS') {
            $blockers += "TARGET_DDL_FAIL_CLOSED_DRIFT:$([string]$orderedPlans[$i].table)"
        }
    }

    $assignmentPlans = @($orderedPlans | Where-Object { $_.family -eq 'ASSIGNMENT' })
    $ttabPlans = @($orderedPlans | Where-Object { $_.family -eq 'TTAB' })
    $assignmentRows = [int64](($assignmentPlans | Measure-Object -Property rows -Sum).Sum)
    $assignmentBytes = [int64](($assignmentPlans | Measure-Object -Property bytes_on_disk -Sum).Sum)
    $ttabRows = [int64](($ttabPlans | Measure-Object -Property rows -Sum).Sum)
    $ttabBytes = [int64](($ttabPlans | Measure-Object -Property bytes_on_disk -Sum).Sum)
    if ($assignmentRows -ne [int64]$placement.receipt.source.assignment.rows) { $blockers += 'ASSIGNMENT_ROW_TOTAL_DRIFT' }
    if ($assignmentBytes -ne [int64]$placement.receipt.source.assignment.bytes_on_disk) { $blockers += 'ASSIGNMENT_BYTE_TOTAL_DRIFT' }
    if ($ttabRows -ne [int64]$placement.receipt.source.ttab.rows) { $blockers += 'TTAB_ROW_TOTAL_DRIFT' }
    if ($ttabBytes -ne [int64]$placement.receipt.source.ttab.bytes_on_disk) { $blockers += 'TTAB_BYTE_TOTAL_DRIFT' }

    $blockers = @($blockers | Sort-Object -Unique)
    $decision = if ($blockers.Count -eq 0) { 'US_SECONDARY_FAMILY_MIGRATION_DESIGN_READY' } else { 'US_SECONDARY_FAMILY_MIGRATION_DESIGN_BLOCKED' }
    $nextGate = if ($blockers.Count -eq 0) { 'TARGET_SCHEMA_APPLY_REVIEW' } else { 'RESOLVE_DESIGN_BLOCKERS' }
    $repoDriftBlockers = @(
        $blockers | Where-Object {
            $_ -match '^(REPO_|SOURCE_ENGINE_DRIFT|SOURCE_SORT_KEY_DRIFT|SOURCE_PRIMARY_KEY_DRIFT|SOURCE_PARTITION_KEY_DRIFT)'
        }
    )
    Assert-ExactMain 'pre-receipt'

    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion
        generated_at=(Get-Date).ToUniversalTime().ToString('o')
        main_sha=$ExpectedMainSha.ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        read_only=$true
        mutation_performed=$false
        logical_checksum_execution_performed=$false
        connectivity_probe_performed=$false
        future_growth_projection_performed=$false
        placement_basis=[ordered]@{
            receipt_path=$placement.path
            receipt_sha256=$placement.sha256
            receipt_version=[string]$placement.receipt.receipt_version
            decision=[string]$placement.receipt.decision
        }
        repository_schema=[ordered]@{
            publisher_column_contract='app.us_assignment.publisher.TABLE_COLUMNS + app.us_ttab.publisher.TABLE_COLUMNS + observed_at'
            migration_files=@($repoFileEvidence)
            live_matches_repository_contract=($repoDriftBlockers.Count -eq 0)
            drift_blockers=@($repoDriftBlockers)
        }
        source=[ordered]@{
            runtime='DOCKER_COMPOSE_LEGACY_SOURCE'
            clickhouse_version=[string]$placement.receipt.source.clickhouse_version
            database=$script:Database
            authoritative_and_retained=$true
            assignment=[ordered]@{ tables=4; rows=$assignmentRows; bytes_on_disk=$assignmentBytes }
            ttab=[ordered]@{ tables=4; rows=$ttabRows; bytes_on_disk=$ttabBytes }
            combined_bytes_on_disk=($assignmentBytes + $ttabBytes)
        }
        target_design=[ordered]@{
            runtime=[string]$placement.receipt.target.runtime
            clickhouse_version=[string]$placement.receipt.target.clickhouse_version
            disk=$script:TargetDisk
            storage_policy=$script:TargetPolicy
            current_free_space=[int64]$placement.receipt.target.required_disk.free_space
            current_active_hot_bytes=[int64]$placement.receipt.target.active_hot_usage.bytes
            target_tables_currently_absent=[int]$placement.receipt.target.absent_table_count
            ddl_execution_performed=$false
        }
        capacity_basis=[ordered]@{
            basis='CURRENT_ACCEPTED_SOURCE_BYTES_ONLY_NO_GROWTH_PROJECTION'
            combined_current_bytes=[int64]$placement.receipt.current_footprint_reserve_check.current_combined_bytes
            projected_free_after_equal_byte_placement=[int64]$placement.receipt.current_footprint_reserve_check.projected_free_after_equal_byte_placement
            recommended_30pct_reserve_floor_bytes=[int64]$placement.receipt.current_footprint_reserve_check.recommended_30pct_reserve_floor_bytes
            hard_20pct_reserve_floor_bytes=[int64]$placement.receipt.current_footprint_reserve_check.hard_20pct_reserve_floor_bytes
            recommended_30pct_current_footprint_fits=[bool]$placement.receipt.current_footprint_reserve_check.recommended_30pct_current_footprint_fits
            hard_20pct_current_footprint_fits=[bool]$placement.receipt.current_footprint_reserve_check.hard_20pct_current_footprint_fits
            future_growth_sufficiency_claimed=$false
        }
        transfer_design=[ordered]@{
            strategy=$script:TransferStrategy
            source_and_target_are_distinct_clickhouse_runtimes=$true
            filesystem_copy_between_runtimes_allowed=$false
            blind_replay_allowed=$false
            future_target_to_source_native_connectivity_preflight_required=$true
            source_endpoint_frozen=$false
            credential_material_persisted=$false
            network_pull_execution_performed=$false
            future_copy_authorized=$false
        }
        migration_order_basis='ASSIGNMENT_FIRST_THEN_TTAB_ASCENDING_SOURCE_BYTES_THEN_TABLE_WITHIN_FAMILY'
        tables=@($orderedPlans)
        acceptance_contract=[ordered]@{
            before_schema_apply=@(
                'exact_main_and_placement_receipt_identity',
                'source_schema_repo_contract_match',
                'source_rows_bytes_parts_and_residency_frozen',
                'all_eight_target_tables_absent',
                'hot_us_only_policy_and_reserve_revalidated'
            )
            after_schema_apply=@(
                'all_eight_target_tables_exist_empty',
                'target_schema_matches_source_except_hot_us_storage_policy',
                'target_tables_storage_policy_hot_us_only',
                'target_active_parts_zero_before_copy',
                'source_unchanged'
            )
            before_each_copy=@(
                'source_row_and_part_manifest_stable',
                'source_logical_checksum_recorded',
                'target_to_source_native_connectivity_accepted',
                'target_table_empty_and_hot_us_only'
            )
            after_each_copy=@(
                'row_count_equivalence',
                'logical_checksum_equivalence',
                'target_parts_only_on_hot_us',
                'source_row_and_checksum_unchanged',
                'hot_us_reserve_floor_revalidated'
            )
        }
        rollback_contract=@(
            'source_remains_authoritative_and_retained_until_separate_cutover_acceptance',
            'failed_target_tables_are_never_served',
            'no_source_cleanup_or_archive_mutation',
            'target_discard_or_drop_requires_separate_explicit_rollback_review',
            'serving_stays_on_pre_migration_runtime_until_final_acceptance'
        )
        phases=@(
            [ordered]@{ phase='A'; name='TARGET_SCHEMA_APPLY_REVIEW'; authorized=$false; next_gate=($blockers.Count -eq 0) },
            [ordered]@{ phase='B'; name='TARGET_SCHEMA_APPLY'; authorized=$false; requires='A' },
            [ordered]@{ phase='C'; name='TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT'; authorized=$false; requires='B' },
            [ordered]@{ phase='D'; name='BOUNDED_ASSIGNMENT_TABLE_COPY'; authorized=$false; requires='C'; per_table_receipt_required=$true },
            [ordered]@{ phase='E'; name='ASSIGNMENT_TARGET_ACCEPTANCE'; authorized=$false; requires='D' },
            [ordered]@{ phase='F'; name='BOUNDED_TTAB_TABLE_COPY'; authorized=$false; requires='E'; per_table_receipt_required=$true },
            [ordered]@{ phase='G'; name='TTAB_TARGET_ACCEPTANCE'; authorized=$false; requires='F' },
            [ordered]@{ phase='H'; name='SERVING_CUTOVER_REVIEW'; authorized=$false; requires='G' },
            [ordered]@{ phase='I'; name='SOURCE_CLEANUP_REVIEW'; authorized=$false; requires='H'; separate_future_issue_required=$true }
        )
        blockers=@($blockers)
        constraints=[ordered]@{
            target_schema_apply_authorized=$false
            insert_or_copy_authorized=$false
            connectivity_mutation_authorized=$false
            serving_cutover_authorized=$false
            source_delete_authorized=$false
            source_archive_mutation_authorized=$false
            replay_authorized=$false
            optimize_or_move_authorized=$false
            vhdx_mutation_authorized=$false
            wsl_lifecycle_mutation_authorized=$false
            docker_lifecycle_mutation_authorized=$false
            application_amplification_reuse_authorized=$false
            future_capacity_sufficiency_claimed=$false
        }
    }

    $receiptPath = Join-Path $evidenceDir 'design_receipt.json'
    $json = $receipt | ConvertTo-Json -Depth 16
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $tmpPath = "$receiptPath.tmp"
    [System.IO.File]::WriteAllText($tmpPath, $json, $utf8)
    Move-Item -LiteralPath $tmpPath -Destination $receiptPath -Force

    Write-Host "receipt_version=$($script:ReceiptVersion)"
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "table_count=$($orderedPlans.Count)"
    Write-Host "assignment_rows=$assignmentRows"
    Write-Host "assignment_bytes=$assignmentBytes"
    Write-Host "ttab_rows=$ttabRows"
    Write-Host "ttab_bytes=$ttabBytes"
    Write-Host "transfer_strategy=$($script:TransferStrategy)"
    Write-Host "repo_schema_match=$($repoDriftBlockers.Count -eq 0)"
    Write-Host 'logical_checksum_execution_performed=False'
    Write-Host 'connectivity_probe_performed=False'
    Write-Host 'future_apply_authorized=False'
    Write-Host 'future_growth_projection_performed=False'
    foreach ($plan in $orderedPlans) {
        Write-Host "migration_table=$($plan.migration_order)|$($plan.family)|$($plan.table)|rows=$($plan.rows)|bytes=$($plan.bytes_on_disk)|ddl_sha256=$($plan.target_create_table_query_sha256)"
    }
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host "receipt_path=$receiptPath"
}
finally {
    Pop-Location
}
