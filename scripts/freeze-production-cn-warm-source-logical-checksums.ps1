[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedDesignReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports',
    [string]$ResumeEvidenceDirectory,
    [ValidateRange(1,4)]
    [int]$MaxThreads = 2,
    [ValidateRange(300,43200)]
    [int]$MaxExecutionSeconds = 14400,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedDesignEngineSha = '58a719a60997ea09e117b0354394a7c59ba0bc23'
$script:AcceptedDesignReceiptVersion = 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_V1'
$script:ChecksumReceiptVersion = 'PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V1'
$script:ChecksumJournalVersion = 'PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_JOURNAL_V1'
$script:ExpectedWarmManifestSha256 = '716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231'
$script:ExpectedWarmCandidateCount = [int64]4
$script:ExpectedWarmRows = [int64]2430570761
$script:ExpectedWarmBytes = [int64]562600035674
$script:ExpectedWarmVhdxPath = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery'
$script:ProductionClickHouseVersion = '24.8.14.39'

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
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command failed with exit code ${exitCode}: $($rendered -join [Environment]::NewLine)"
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

function Get-StringSha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-JsonFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label file missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Write-JsonFile([object]$Value, [string]$Path) {
    $Value | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Escape-SqlLiteral([string]$Value) { return $Value.Replace("'", "''") }
function Assert-SafeTableName([string]$TableName) {
    if ($TableName -notmatch '^cn_[a-z0-9_]+$') { throw "Unsafe CN table name: $TableName" }
}

function Get-LiveSchemaFingerprint([object]$TableRow) {
    return Get-StringSha256 (@(
        [string]$TableRow.table,
        [string]$TableRow.engine,
        [string]$TableRow.sorting_key,
        [string]$TableRow.primary_key,
        [string]$TableRow.partition_key,
        [string]$TableRow.create_table_query
    ) -join "`n")
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
    foreach ($part in @($Parts | Sort-Object name)) { $lines += "$([string]$part.name)|$([string]$part.disk_name)" }
    return Get-StringSha256 ($lines -join "`n")
}

function Get-ProductionClickHouseHealth {
    $idProbe = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids = @($idProbe.lines | Where-Object { $_.Trim() })
    if ($idProbe.exit_code -ne 0 -or $ids.Count -ne 1) { return [ordered]@{ ready=$false; health=$null; container_id=$null; version=$null } }
    $containerId = $ids[0].Trim()
    $healthProbe = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $sqlProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $versionProbe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure
    $health = (@($healthProbe.lines) -join '').Trim().ToLowerInvariant()
    $version = if ($versionProbe.exit_code -eq 0) { (@($versionProbe.lines) -join '').Trim() } else { $null }
    $ready = [bool]($healthProbe.exit_code -eq 0 -and $health -eq 'healthy' -and $sqlProbe.exit_code -eq 0 -and ((@($sqlProbe.lines) -join '').Trim() -eq '1'))
    return [ordered]@{ ready=$ready; health=$health; container_id=$containerId; version=$version }
}

function Assert-AcceptedProductionMount([string]$ContainerId) {
    $probe = Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$ContainerId) -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Unable to inspect production ClickHouse mounts.' }
    try { $mounts = ((@($probe.lines) -join "`n") | ConvertFrom-Json) }
    catch { throw "Production ClickHouse mount JSON invalid: $($_.Exception.Message)" }
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    $ready = [bool]($matches.Count -eq 1 -and [string]$matches[0].Type -eq 'volume' -and [string]$matches[0].Name -eq $AcceptedVolume)
    Write-Host "accepted_production_mount_ready=$ready"
    if (-not $ready) { throw 'Production ClickHouse data mount is not the accepted named volume.' }
}

function Assert-RawConsumersStopped {
    $runningTotal = 0
    foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
        $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure
        if ($probe.exit_code -ne 0) { throw "Unable to inspect service $service." }
        $running = 0
        foreach ($containerId in @($probe.lines | Where-Object { $_.Trim() })) {
            $state = Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$containerId.Trim()) -AllowFailure
            if ($state.exit_code -ne 0) { throw "Unable to inspect container for $service." }
            if (((@($state.lines) -join '').Trim().ToLowerInvariant()) -eq 'true') { $running++ }
        }
        $runningTotal += $running
        Write-Host "raw_consumer_service=$service running_count=$running"
    }
    Write-Host "running_raw_consumer_count=$runningTotal"
    if ($runningTotal -ne 0) { throw "All Raw/runtime consumer services must remain absent/stopped; observed $runningTotal." }
}

function Resolve-AcceptedDesignReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedDesignReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted migration design receipt'
    $sha256 = Get-FileSha256 $path
    if ([string]$receipt.receipt_version -ne $script:AcceptedDesignReceiptVersion) { throw 'Unexpected design receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedDesignEngineSha) { throw 'Accepted design receipt engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_READY_FOR_REVIEW') { throw 'Design receipt is not READY.' }
    if (-not [bool]$receipt.design_only -or -not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Design receipt safety state changed.' }
    foreach ($name in @('apply_authorized','provisioning_authorized','cn_warm_move_authorized','source_cleanup_authorized')) {
        if ([bool]$receipt.$name) { throw "Design receipt unexpectedly authorizes $name." }
    }
    if ([bool]$receipt.logical_checksum_execution_performed) { throw 'Design receipt unexpectedly claims logical checksums were already executed.' }
    if ([string]$receipt.accepted_equivalence.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmManifestSha256) { throw 'Warm manifest changed in design receipt.' }
    if ([int64]$receipt.accepted_equivalence.candidate_count -ne $script:ExpectedWarmCandidateCount) { throw 'Candidate count changed in design receipt.' }
    if ([int64]$receipt.accepted_equivalence.rows -ne $script:ExpectedWarmRows) { throw 'Row total changed in design receipt.' }
    if ([int64]$receipt.accepted_equivalence.bytes -ne $script:ExpectedWarmBytes) { throw 'Byte total changed in design receipt.' }
    if ([string]$receipt.production_topology.accepted_source_volume -ne $AcceptedVolume) { throw 'Accepted source volume changed in design receipt.' }
    if ([string]$receipt.production_topology.warm_vhdx_path -ne $script:ExpectedWarmVhdxPath) { throw 'Warm VHDX path changed in design receipt.' }
    if (-not [bool]$receipt.production_invariants.accepted_named_volume_mounted) { throw 'Design receipt lost accepted source volume invariant.' }
    if (-not [bool]$receipt.production_invariants.proposed_warm_vhdx_still_absent) { throw 'Design receipt says Warm VHDX was already present.' }
    foreach ($name in @('vhdx_mutation_authorized','wsl_mutation_authorized','docker_mutation_authorized','clickhouse_mutation_authorized','cn_replay_authorized','us_bulk_authorized','accepted_volume_mutation_authorized','raw_delete_authorized','f_recovery_mutation_authorized')) {
        if ([bool]$receipt.constraints.$name) { throw "Design receipt unexpectedly authorizes $name." }
    }
    foreach ($embedded in @(
        [ordered]@{ label='provisioning'; path=[string]$receipt.accepted_provisioning.receipt_path; sha=[string]$receipt.accepted_provisioning.receipt_sha256 },
        [ordered]@{ label='equivalence'; path=[string]$receipt.accepted_equivalence.receipt_path; sha=[string]$receipt.accepted_equivalence.receipt_sha256 }
    )) {
        $embeddedPath = [System.IO.Path]::GetFullPath([string]$embedded.path)
        if ((Get-FileSha256 $embeddedPath) -ne [string]$embedded.sha) { throw "Embedded $($embedded.label) receipt SHA drifted." }
    }
    $plans = @($receipt.candidates | Sort-Object migration_order)
    if ($plans.Count -ne $script:ExpectedWarmCandidateCount) { throw 'Design candidate plan count changed.' }
    if ([int64](($plans | Measure-Object -Property rows -Sum).Sum) -ne $script:ExpectedWarmRows) { throw 'Design plan row total changed.' }
    if ([int64](($plans | Measure-Object -Property bytes_on_disk -Sum).Sum) -ne $script:ExpectedWarmBytes) { throw 'Design plan byte total changed.' }
    for ($i = 0; $i -lt $plans.Count; $i++) {
        if ([int64]$plans[$i].migration_order -ne [int64]($i + 1)) { throw 'Design migration order is not contiguous.' }
        Assert-SafeTableName ([string]$plans[$i].table)
        if (@($plans[$i].partitions).Count -lt 1) { throw "Design plan has no migration units: $($plans[$i].table)" }
    }
    return [ordered]@{ path=$path; sha256=$sha256; receipt=$receipt; plans=@($plans) }
}

function Invoke-ClickHouseJsonRows([string]$Sql, [string]$Label) {
    $probe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql + ' FORMAT JSONEachRow')) -AllowFailure
    if ($probe.exit_code -ne 0) { throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    $rows = @()
    foreach ($line in @($probe.lines | Where-Object { $_.Trim() })) {
        try { $rows += ($line | ConvertFrom-Json) }
        catch { throw "$Label returned invalid JSONEachRow: $line" }
    }
    return @($rows)
}

function Get-LiveUnitSnapshot([object]$Plan, [object]$Unit) {
    $table = [string]$Plan.table
    $partition = [string]$Unit.partition_id
    Assert-SafeTableName $table
    $tableLiteral = Escape-SqlLiteral $table
    $tableRows = @(Invoke-ClickHouseJsonRows "SELECT name AS table, engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database = 'markorbit_facts' AND name = '$tableLiteral'" "Live schema $table")
    if ($tableRows.Count -ne 1) { throw "Expected one live system.tables row for $table." }
    $parts = @(Invoke-ClickHouseJsonRows "SELECT table, partition_id, name, rows, bytes_on_disk, disk_name, hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files FROM system.parts WHERE database = 'markorbit_facts' AND active AND table = '$tableLiteral' ORDER BY partition_id, name" "Live parts $table")
    $unitParts = @($parts | Where-Object { [string]$_.partition_id -eq $partition })
    if ($unitParts.Count -lt 1) { throw "No active parts found for $table partition $partition." }
    return [ordered]@{
        schema_fingerprint_sha256=(Get-LiveSchemaFingerprint $tableRows[0])
        table_rows=[int64](($parts | Measure-Object -Property rows -Sum).Sum)
        table_bytes=[int64](($parts | Measure-Object -Property bytes_on_disk -Sum).Sum)
        table_active_parts=[int64]$parts.Count
        table_part_content_sha256=(Get-PartContentFingerprint $parts)
        table_residency_sha256=(Get-ResidencyFingerprint $parts)
        table_disks=@($parts | ForEach-Object { [string]$_.disk_name } | Sort-Object -Unique)
        unit_rows=[int64](($unitParts | Measure-Object -Property rows -Sum).Sum)
        unit_bytes=[int64](($unitParts | Measure-Object -Property bytes_on_disk -Sum).Sum)
        unit_active_parts=[int64]$unitParts.Count
        unit_part_content_sha256=(Get-PartContentFingerprint $unitParts)
        unit_residency_sha256=(Get-ResidencyFingerprint $unitParts)
        unit_disks=@($unitParts | ForEach-Object { [string]$_.disk_name } | Sort-Object -Unique)
    }
}

function New-DriftMessage([string]$Kind, [object]$Plan, [object]$Unit, [string]$Phase) {
    return "$Kind|table=$([string]$Plan.table)|partition=$([string]$Unit.partition_id)|phase=$Phase"
}

function Assert-SnapshotMatchesDesign([object]$Snapshot, [object]$Plan, [object]$Unit, [string]$Phase) {
    if ([string]$Snapshot.schema_fingerprint_sha256 -ne [string]$Plan.schema_fingerprint_sha256) { throw (New-DriftMessage 'SCHEMA_FINGERPRINT_DRIFT' $Plan $Unit $Phase) }
    if ([int64]$Snapshot.table_rows -ne [int64]$Plan.rows) { throw (New-DriftMessage 'TABLE_ROW_COUNT_DRIFT' $Plan $Unit $Phase) }
    if ([int64]$Snapshot.table_bytes -ne [int64]$Plan.bytes_on_disk) { throw (New-DriftMessage 'TABLE_BYTE_COUNT_DRIFT' $Plan $Unit $Phase) }
    if ([int64]$Snapshot.table_active_parts -ne [int64]$Plan.active_parts) { throw (New-DriftMessage 'TABLE_ACTIVE_PART_COUNT_DRIFT' $Plan $Unit $Phase) }
    if ([string]$Snapshot.table_part_content_sha256 -ne [string]$Plan.source_part_content_manifest_sha256) { throw (New-DriftMessage 'TABLE_PART_CONTENT_DRIFT' $Plan $Unit $Phase) }
    if ([string]$Snapshot.table_residency_sha256 -ne [string]$Plan.source_residency_manifest_sha256) { throw (New-DriftMessage 'TABLE_RESIDENCY_DRIFT' $Plan $Unit $Phase) }
    if (@($Snapshot.table_disks).Count -ne 1 -or [string]$Snapshot.table_disks[0] -ne [string]$Plan.source_disk) { throw (New-DriftMessage 'TABLE_SOURCE_DISK_DRIFT' $Plan $Unit $Phase) }
    if ([int64]$Snapshot.unit_rows -ne [int64]$Unit.rows) { throw (New-DriftMessage 'UNIT_ROW_COUNT_DRIFT' $Plan $Unit $Phase) }
    if ([int64]$Snapshot.unit_bytes -ne [int64]$Unit.bytes_on_disk) { throw (New-DriftMessage 'UNIT_BYTE_COUNT_DRIFT' $Plan $Unit $Phase) }
    if ([int64]$Snapshot.unit_active_parts -ne [int64]$Unit.active_parts) { throw (New-DriftMessage 'UNIT_ACTIVE_PART_COUNT_DRIFT' $Plan $Unit $Phase) }
    if ([string]$Snapshot.unit_part_content_sha256 -ne [string]$Unit.source_part_content_manifest_sha256) { throw (New-DriftMessage 'UNIT_PART_CONTENT_DRIFT' $Plan $Unit $Phase) }
    if ([string]$Snapshot.unit_residency_sha256 -ne [string]$Unit.source_residency_manifest_sha256) { throw (New-DriftMessage 'UNIT_RESIDENCY_DRIFT' $Plan $Unit $Phase) }
    if (@($Snapshot.unit_disks).Count -ne 1 -or [string]$Snapshot.unit_disks[0] -ne [string]$Unit.source_disk) { throw (New-DriftMessage 'UNIT_SOURCE_DISK_DRIFT' $Plan $Unit $Phase) }
}

function Get-SourceIdentitySha([object]$Snapshot, [object]$Plan, [object]$Unit) {
    return Get-StringSha256 (@(
        [string]$Plan.table,
        [string]$Unit.partition_id,
        [string]$Snapshot.schema_fingerprint_sha256,
        [string]$Snapshot.table_rows,
        [string]$Snapshot.table_bytes,
        [string]$Snapshot.table_active_parts,
        [string]$Snapshot.table_part_content_sha256,
        [string]$Snapshot.table_residency_sha256,
        [string]$Snapshot.unit_rows,
        [string]$Snapshot.unit_bytes,
        [string]$Snapshot.unit_active_parts,
        [string]$Snapshot.unit_part_content_sha256,
        [string]$Snapshot.unit_residency_sha256,
        [string]$Plan.source_disk
    ) -join "`n")
}

function Assert-FrozenLogicalSql([string]$Sql, [object]$Plan, [object]$Unit) {
    $tableRegex = [regex]::Escape([string]$Plan.table)
    $partitionRegex = [regex]::Escape((Escape-SqlLiteral ([string]$Unit.partition_id)))
    $pattern = "^SELECT count\(\) AS rows, sum\(cityHash64\(tuple\(\*\)\)\) AS checksum_sum, groupBitXor\(cityHash64\(tuple\(\*\)\)\) AS checksum_xor FROM markorbit_facts\.$tableRegex WHERE _partition_id = '$partitionRegex'$"
    if ($Sql -notmatch $pattern) { throw "Frozen logical checksum SQL shape changed for $($Plan.table)/$($Unit.partition_id)." }
}

function Get-ExecutionQuery([string]$LogicalSql) {
    return "$LogicalSql SETTINGS max_threads = $MaxThreads, max_execution_time = $MaxExecutionSeconds, max_memory_usage = 4294967296, use_uncompressed_cache = 0 FORMAT TabSeparatedRaw"
}

function Invoke-LogicalChecksum([string]$ExecutionQuery, [object]$Plan, [object]$Unit) {
    $probe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',$ExecutionQuery) -AllowFailure
    if ($probe.exit_code -ne 0) { throw "Logical checksum query failed for $($Plan.table)/$($Unit.partition_id): $($probe.lines -join [Environment]::NewLine)" }
    $lines = @($probe.lines | Where-Object { $_.Trim() })
    if ($lines.Count -ne 1) { throw "Logical checksum query returned $($lines.Count) rows for $($Plan.table)/$($Unit.partition_id)." }
    $fields = @($lines[0] -split "`t", -1)
    if ($fields.Count -ne 3) { throw "Logical checksum query returned unexpected field count for $($Plan.table)/$($Unit.partition_id)." }
    if ($fields[0] -notmatch '^\d+$' -or $fields[1] -notmatch '^\d+$' -or $fields[2] -notmatch '^\d+$') { throw 'Logical checksum output must be exact unsigned decimal strings.' }
    if ([int64]$fields[0] -ne [int64]$Unit.rows) { throw "Logical checksum row count mismatch for $($Plan.table)/$($Unit.partition_id)." }
    return [ordered]@{ rows=[string]$fields[0]; checksum_sum=[string]$fields[1]; checksum_xor=[string]$fields[2] }
}

function Get-UnitResultSha([object]$Result) {
    return Get-StringSha256 (@(
        [string]$Result.table,
        [string]$Result.partition_id,
        [string]$Result.rows,
        [string]$Result.checksum_sum,
        [string]$Result.checksum_xor,
        [string]$Result.logical_sql_sha256,
        [string]$Result.execution_query_sha256,
        [string]$Result.source_identity_sha256
    ) -join '|')
}

function Invoke-ContractFixture {
    $fixture = [pscustomobject]@{ table='cn_observed_event' }
    $unit = [pscustomobject]@{ partition_id='all'; rows=10 }
    $sql = "SELECT count() AS rows, sum(cityHash64(tuple(*))) AS checksum_sum, groupBitXor(cityHash64(tuple(*))) AS checksum_xor FROM markorbit_facts.cn_observed_event WHERE _partition_id = 'all'"
    Assert-FrozenLogicalSql $sql $fixture $unit
    $execution = Get-ExecutionQuery $sql
    if ($execution -notmatch 'max_threads = 2' -or $execution -notmatch 'FORMAT TabSeparatedRaw') { throw 'Bounded checksum execution query contract failed.' }
    $sample = [ordered]@{ table='cn_observed_event'; partition_id='all'; rows='10'; checksum_sum='18446744073709551615'; checksum_xor='9223372036854775808'; logical_sql_sha256='a'; execution_query_sha256='b'; source_identity_sha256='c' }
    if ((Get-UnitResultSha $sample) -notmatch '^[0-9a-f]{64}$') { throw 'Checksum result canonical hash contract failed.' }
    Write-Host 'PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_CONTRACT_DIRECT_INVOCATION_OK'
}

try {
    Write-Host '===== PRODUCTION CN WARM SOURCE LOGICAL CHECKSUM ====='
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'apply_authorized=False'
    Write-Host 'provisioning_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'source_cleanup_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host 'wsl_mutation_authorized=False'
    Write-Host 'docker_mutation_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_bulk_authorized=False'
    Write-Host "max_threads=$MaxThreads"
    Write-Host "max_execution_seconds=$MaxExecutionSeconds"

    if ($ContractOnly) { Invoke-ContractFixture; exit 0 }

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Source logical checksum gate must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $acceptedDesign = Resolve-AcceptedDesignReceipt
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env missing.' }
    $envShaBefore = Get-FileSha256 $envPath
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root unexpectedly exists.' }
    if (-not (Test-Path -LiteralPath $script:ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX missing.' }
    $fInfo = New-Object System.IO.FileInfo($script:ExpectedFRecoveryVhdx)
    if ([int64]$fInfo.Length -ne $script:ExpectedFRecoveryBytes) { throw 'Retained F recovery VHDX length changed.' }
    if (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath) { throw 'Production Warm VHDX exists before explicit provisioning authorization.' }

    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    if (-not [bool]$production.ready) { throw 'Production ClickHouse must be healthy before logical checksum scans.' }
    if ([string]$production.version -ne $script:ProductionClickHouseVersion) { throw 'Production ClickHouse version drifted.' }
    Assert-AcceptedProductionMount $production.container_id

    if ($ResumeEvidenceDirectory) {
        $evidenceDir = [System.IO.Path]::GetFullPath($ResumeEvidenceDirectory)
        if (-not (Test-Path -LiteralPath $evidenceDir -PathType Container)) { throw 'Resume evidence directory does not exist.' }
    }
    else {
        $timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
        $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_cn_warm_source_logical_checksum_$timestamp")
        New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    }
    $journalPath = Join-Path $evidenceDir 'production_cn_warm_source_logical_checksum_journal.json'

    if (Test-Path -LiteralPath $journalPath -PathType Leaf) {
        $journal = Read-JsonFile $journalPath 'Checksum journal'
        if ([string]$journal.receipt_version -ne $script:ChecksumJournalVersion) { throw 'Unexpected checksum journal version.' }
        if ([string]$journal.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Checksum journal main SHA changed.' }
        if ([string]$journal.design_receipt_sha256 -ne $acceptedDesign.sha256) { throw 'Checksum journal design receipt SHA changed.' }
        if ([int]$journal.max_threads -ne $MaxThreads -or [int]$journal.max_execution_seconds -ne $MaxExecutionSeconds) { throw 'Checksum journal execution settings changed.' }
        $results = @($journal.units)
    }
    else {
        $results = @()
        $journal = [ordered]@{
            receipt_version=$script:ChecksumJournalVersion
            engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
            design_receipt_path=$acceptedDesign.path
            design_receipt_sha256=$acceptedDesign.sha256
            warm_candidate_manifest_sha256=$script:ExpectedWarmManifestSha256
            max_threads=$MaxThreads
            max_execution_seconds=$MaxExecutionSeconds
            read_only=$true
            mutation_performed=$false
            units=@()
        }
        Write-JsonFile $journal $journalPath
    }

    $expectedUnitCount = [int64]0
    foreach ($plan in $acceptedDesign.plans) { $expectedUnitCount += [int64]@($plan.partitions).Count }

    foreach ($plan in $acceptedDesign.plans) {
        foreach ($unit in @($plan.partitions)) {
            $table = [string]$plan.table
            $partition = [string]$unit.partition_id
            $unitKey = "$table|$partition"
            $logicalSql = [string]$unit.logical_checksum_sql
            Assert-FrozenLogicalSql $logicalSql $plan $unit
            $logicalSqlSha = Get-StringSha256 $logicalSql
            $executionQuery = Get-ExecutionQuery $logicalSql
            $executionQuerySha = Get-StringSha256 $executionQuery

            Write-Host "checksum_unit_start=$unitKey"
            Assert-ExactMain "pre_checksum_$($plan.migration_order)_$partition"
            Assert-RawConsumersStopped
            $beforeHealth = Get-ProductionClickHouseHealth
            if (-not [bool]$beforeHealth.ready) { throw "Production ClickHouse unhealthy before $unitKey." }
            Assert-AcceptedProductionMount $beforeHealth.container_id
            $before = Get-LiveUnitSnapshot $plan $unit
            Assert-SnapshotMatchesDesign $before $plan $unit 'before'
            $sourceIdentity = Get-SourceIdentitySha $before $plan $unit

            $existing = @($results | Where-Object { [string]$_.table -eq $table -and [string]$_.partition_id -eq $partition })
            if ($existing.Count -gt 1) { throw "Duplicate checksum journal entries for $unitKey." }
            if ($existing.Count -eq 1) {
                if ([string]$existing[0].logical_sql_sha256 -ne $logicalSqlSha -or [string]$existing[0].execution_query_sha256 -ne $executionQuerySha -or [string]$existing[0].source_identity_sha256 -ne $sourceIdentity) { throw "Resume identity drift for $unitKey." }
                if ([string]$existing[0].result_sha256 -ne (Get-UnitResultSha $existing[0])) { throw "Resume result hash drift for $unitKey." }
                Write-Host "checksum_unit_reused=$unitKey|rows=$($existing[0].rows)|result_sha256=$($existing[0].result_sha256)"
                continue
            }

            $started = (Get-Date).ToUniversalTime()
            $checksum = Invoke-LogicalChecksum $executionQuery $plan $unit
            $finished = (Get-Date).ToUniversalTime()

            Assert-RawConsumersStopped
            $afterHealth = Get-ProductionClickHouseHealth
            if (-not [bool]$afterHealth.ready) { throw "Production ClickHouse unhealthy after $unitKey." }
            Assert-AcceptedProductionMount $afterHealth.container_id
            $after = Get-LiveUnitSnapshot $plan $unit
            Assert-SnapshotMatchesDesign $after $plan $unit 'after'
            $afterIdentity = Get-SourceIdentitySha $after $plan $unit
            if ($afterIdentity -ne $sourceIdentity) { throw "Source identity changed during checksum scan for $unitKey." }
            Assert-ExactMain "post_checksum_$($plan.migration_order)_$partition"

            $record = [ordered]@{
                migration_order=[int64]$plan.migration_order
                table=$table
                partition_id=$partition
                rows=[string]$checksum.rows
                bytes_on_disk=[int64]$unit.bytes_on_disk
                active_parts=[int64]$unit.active_parts
                checksum_sum=[string]$checksum.checksum_sum
                checksum_xor=[string]$checksum.checksum_xor
                logical_sql_sha256=$logicalSqlSha
                execution_query_sha256=$executionQuerySha
                source_identity_sha256=$sourceIdentity
                source_schema_sha256=[string]$before.schema_fingerprint_sha256
                source_part_content_sha256=[string]$before.unit_part_content_sha256
                source_residency_sha256=[string]$before.unit_residency_sha256
                started_at_utc=$started.ToString('o')
                finished_at_utc=$finished.ToString('o')
                duration_seconds=[math]::Round(($finished - $started).TotalSeconds, 3)
                reused_from_journal=$false
                result_sha256=$null
            }
            $record.result_sha256 = Get-UnitResultSha $record
            $results += [pscustomobject]$record
            $journal.units = @($results | Sort-Object migration_order, table, partition_id)
            Write-JsonFile $journal $journalPath
            Write-Host "checksum_unit_done=$unitKey|rows=$($record.rows)|duration_seconds=$($record.duration_seconds)|result_sha256=$($record.result_sha256)"
        }
    }

    $results = @($results | Sort-Object migration_order, table, partition_id)
    if ($results.Count -ne $expectedUnitCount) { throw "Checksum result count mismatch: expected $expectedUnitCount observed $($results.Count)." }
    $checksumRows = [int64](($results | ForEach-Object { [int64]$_.rows } | Measure-Object -Sum).Sum)
    if ($checksumRows -ne $script:ExpectedWarmRows) { throw 'Final checksum row total changed.' }
    $manifestLines = @($results | ForEach-Object { "$($_.table)|$($_.partition_id)|$($_.rows)|$($_.checksum_sum)|$($_.checksum_xor)|$($_.logical_sql_sha256)|$($_.execution_query_sha256)|$($_.source_identity_sha256)|$($_.result_sha256)" })
    $checksumManifestSha = Get-StringSha256 ($manifestLines -join "`n")

    Assert-RawConsumersStopped
    $finalHealth = Get-ProductionClickHouseHealth
    if (-not [bool]$finalHealth.ready) { throw 'Production ClickHouse must remain healthy after logical checksum scans.' }
    Assert-AcceptedProductionMount $finalHealth.container_id
    Assert-ExactMain 'final'
    if ((Get-FileSha256 $envPath) -ne $envShaBefore) { throw '.env changed during logical checksum gate.' }
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root reappeared.' }
    if (-not (Test-Path -LiteralPath $script:ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX disappeared.' }
    $finalFInfo = New-Object System.IO.FileInfo($script:ExpectedFRecoveryVhdx)
    if ([int64]$finalFInfo.Length -ne $script:ExpectedFRecoveryBytes) { throw 'Retained F recovery VHDX length changed.' }
    if (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath) { throw 'Production Warm VHDX was created during read-only checksum gate.' }

    $receipt = [ordered]@{
        receipt_version=$script:ChecksumReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_READY_FOR_REVIEW'
        next_gate='EXPLICIT_OPERATOR_REVIEW_OF_PROVISIONING_AUTHORIZATION'
        read_only=$true
        mutation_performed=$false
        logical_checksum_execution_performed=$true
        apply_authorized=$false
        provisioning_authorized=$false
        cn_warm_move_authorized=$false
        source_cleanup_authorized=$false
        accepted_design=[ordered]@{
            engine_sha=$script:AcceptedDesignEngineSha
            receipt_path=$acceptedDesign.path
            receipt_sha256=$acceptedDesign.sha256
            decision='PRODUCTION_CN_WARM_MIGRATION_DESIGN_READY_FOR_REVIEW'
        }
        warm_candidate_manifest_sha256=$script:ExpectedWarmManifestSha256
        candidate_count=$script:ExpectedWarmCandidateCount
        migration_unit_count=$expectedUnitCount
        rows=$script:ExpectedWarmRows
        bytes=$script:ExpectedWarmBytes
        checksum_manifest_sha256=$checksumManifestSha
        checksum_execution=[ordered]@{
            max_threads=$MaxThreads
            max_execution_seconds=$MaxExecutionSeconds
            max_memory_usage_bytes=[int64]4294967296
            use_uncompressed_cache=$false
            sequential_units=$true
            exact_uint64_decimal_strings=$true
            journal_path=$journalPath
        }
        units=@($results)
        production_invariants=[ordered]@{
            exact_clean_main=$true
            raw_consumers_stopped=$true
            production_clickhouse_ready=$true
            production_clickhouse_version=$finalHealth.version
            accepted_named_volume_mounted=$true
            env_unchanged=$true
            e_backup_root_absent=$true
            f_recovery_preserved=$true
            proposed_warm_vhdx_still_absent=$true
        }
        constraints=[ordered]@{
            vhdx_mutation_authorized=$false
            wsl_mutation_authorized=$false
            docker_mutation_authorized=$false
            clickhouse_mutation_authorized=$false
            cn_replay_authorized=$false
            us_bulk_authorized=$false
            accepted_volume_mutation_authorized=$false
            raw_delete_authorized=$false
            f_recovery_mutation_authorized=$false
            production_warm_provisioning_authorized=$false
            cross_runtime_transfer_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_cn_warm_source_logical_checksum.json'
    Write-JsonFile $receipt $receiptPath
    Assert-ExactMain 'exit'

    Write-Host '===== PRODUCTION CN WARM SOURCE LOGICAL CHECKSUM RESULT ====='
    Write-Host 'decision=PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_READY_FOR_REVIEW'
    Write-Host 'next_gate=EXPLICIT_OPERATOR_REVIEW_OF_PROVISIONING_AUTHORIZATION'
    Write-Host "accepted_design_receipt_sha256=$($acceptedDesign.sha256)"
    Write-Host "warm_candidate_manifest_sha256=$script:ExpectedWarmManifestSha256"
    Write-Host "candidate_count=$script:ExpectedWarmCandidateCount"
    Write-Host "migration_unit_count=$expectedUnitCount"
    Write-Host "rows=$script:ExpectedWarmRows"
    Write-Host "bytes=$script:ExpectedWarmBytes"
    Write-Host "checksum_manifest_sha256=$checksumManifestSha"
    foreach ($result in $results) { Write-Host "checksum_unit=$($result.table)|partition=$($result.partition_id)|rows=$($result.rows)|sum=$($result.checksum_sum)|xor=$($result.checksum_xor)|result_sha256=$($result.result_sha256)" }
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'logical_checksum_execution_performed=True'
    Write-Host 'apply_authorized=False'
    Write-Host 'provisioning_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'source_cleanup_authorized=False'
    Write-Host "journal_path=$journalPath"
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_DONE'
    exit 0
}
catch {
    Write-Host "PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_FAILED: $($_.Exception.Message)"
    exit 2
}
finally { Pop-Location }
