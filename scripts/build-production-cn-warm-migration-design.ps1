[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedProvisioningPreflightReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedProvisioningEngineSha = 'db4cc021cb297c712327037362ba3d5b4ee67479'
$script:AcceptedProvisioningReceiptVersion = 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_V1'
$script:AcceptedEquivalenceReceiptVersion = 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_V1'
$script:DesignReceiptVersion = 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_V1'
$script:DesignArchitectureVersion = 'DEDICATED_WSL2_CROSS_RUNTIME_WARM_MIGRATION_DESIGN_V1_ISSUE_496'
$script:ExpectedWarmManifestSha256 = '716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231'
$script:ExpectedWarmCandidateCount = [int64]4
$script:ExpectedWarmRows = [int64]2430570761
$script:ExpectedWarmBytes = [int64]562600035674
$script:ExpectedWarmCopyRequiredBytes = [int64]618860039242
$script:ExpectedWarmVhdxPath = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ExpectedWarmMountName = 'markorbit_prod_warm_cn'
$script:ExpectedWarmExt4QuotaBytes = [int64]842887331840
$script:ExpectedWarmVhdxMaxBytes = [int64]842887331840
$script:ExpectedETotalBytes = [int64]2048391114752
$script:ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery'
$script:ProductionRuntimeDistro = 'MarkOrbit-ClickHouse'
$script:ProductionRuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse'
$script:ProductionClickHouseVersion = '24.8.14.39'
$script:WarmClickHouseDiskName = 'warm_cn'
$script:WarmStoragePolicyName = 'warm_cn_only'
$script:WarmClickHouseDiskPath = '/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/'
$script:MigrationOrderBasis = 'EVENT_HISTORY_FIRST_THEN_ASCENDING_BYTES_THEN_TABLE'
$script:TransferStrategy = 'TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE'

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
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label file missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Escape-SqlLiteral([string]$Value) {
    return $Value.Replace("'", "''")
}

function Assert-SafeTableName([string]$TableName) {
    if ($TableName -notmatch '^cn_[a-z0-9_]+$') { throw "Unsafe CN table name in accepted receipt: $TableName" }
}

function Get-CandidateManifestHash([object[]]$Candidates) {
    $lines = @()
    foreach ($row in @($Candidates | Sort-Object table)) {
        $lines += @(
            [string]$row.table,
            [string]$row.schema_fingerprint_sha256,
            [string]$row.active_parts,
            [string]$row.rows_from_parts,
            [string]$row.bytes_on_disk,
            (@($row.disk_names) -join ','),
            [string]$row.proposed_tier
        ) -join '|'
    }
    return Get-StringSha256 ($lines -join "`n")
}

function Get-LiveSchemaFingerprint([object]$TableRow) {
    $canonical = @(
        [string]$TableRow.table,
        [string]$TableRow.engine,
        [string]$TableRow.sorting_key,
        [string]$TableRow.primary_key,
        [string]$TableRow.partition_key,
        [string]$TableRow.create_table_query
    ) -join "`n"
    return Get-StringSha256 $canonical
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

function Resolve-AcceptedProvisioningReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedProvisioningPreflightReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted provisioning preflight receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedProvisioningReceiptVersion) { throw 'Unexpected provisioning receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedProvisioningEngineSha) { throw 'Provisioning receipt engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_READY') { throw 'Provisioning receipt is not READY.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed -or [bool]$receipt.provisioning_completed) { throw 'Provisioning receipt safety state changed.' }
    if ([string]$receipt.accepted_equivalence.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmManifestSha256) { throw 'Provisioning receipt Warm manifest changed.' }
    if ([int64]$receipt.accepted_equivalence.warm_candidate_table_count -ne $script:ExpectedWarmCandidateCount) { throw 'Provisioning receipt candidate count changed.' }
    if ([int64]$receipt.accepted_equivalence.warm_candidate_rows -ne $script:ExpectedWarmRows) { throw 'Provisioning receipt row total changed.' }
    if ([int64]$receipt.accepted_equivalence.warm_candidate_bytes -ne $script:ExpectedWarmBytes) { throw 'Provisioning receipt byte total changed.' }
    if ([int64]$receipt.accepted_equivalence.warm_required_physical_bytes_with_copy_safety -ne $script:ExpectedWarmCopyRequiredBytes) { throw 'Provisioning receipt copy-safety requirement changed.' }
    if ([string]$receipt.proposed_provisioning.vhdx_path -ne $script:ExpectedWarmVhdxPath) { throw 'Provisioning VHDX path changed.' }
    if ([string]$receipt.architecture.proposed_warm_mount_name -ne $script:ExpectedWarmMountName) { throw 'Provisioning mount name changed.' }
    if ([int64]$receipt.proposed_provisioning.ext4_quota_bytes -ne $script:ExpectedWarmExt4QuotaBytes) { throw 'Provisioning ext4 quota changed.' }
    if ([int64]$receipt.proposed_provisioning.vhdx_max_bytes -ne $script:ExpectedWarmVhdxMaxBytes) { throw 'Provisioning VHDX max changed.' }
    if (-not [bool]$receipt.capacity.recommended_30_percent_admission) { throw 'Provisioning receipt lost 30 percent admission.' }
    if ([bool]$receipt.proposed_provisioning.path_exists) { throw 'Accepted provisioning receipt says production Warm path already existed.' }
    foreach ($name in @(
        'apply_surface_present','resume_surface_present','vhdx_create_authorized','vhdx_resize_authorized',
        'vhdx_mount_authorized','vhdx_detach_authorized','vhdx_compact_authorized','vhdx_move_authorized',
        'vhdx_delete_authorized','wsl_mutation_authorized','clickhouse_mutation_authorized','cn_warm_move_authorized',
        'docker_restart_authorized','docker_prune_authorized','accepted_volume_mutation_authorized','raw_delete_authorized',
        'cn_replay_authorized','us_bulk_authorized'
    )) {
        if ([bool]$receipt.constraints.$name) { throw "Accepted provisioning receipt unexpectedly authorizes $name." }
    }
    return [ordered]@{ path=$path; sha256=(Get-FileSha256 $path); receipt=$receipt }
}

function Resolve-AcceptedEquivalenceReceipt([object]$ProvisioningReceipt) {
    $path = [System.IO.Path]::GetFullPath([string]$ProvisioningReceipt.accepted_equivalence.receipt_path)
    $expectedSha = [string]$ProvisioningReceipt.accepted_equivalence.receipt_sha256
    $actualSha = Get-FileSha256 $path
    if ($actualSha -ne $expectedSha) { throw 'Equivalence receipt SHA no longer matches provisioning provenance.' }
    $receipt = Read-JsonFile $path 'Accepted equivalence receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedEquivalenceReceiptVersion) { throw 'Unexpected equivalence receipt version.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_READY') { throw 'Equivalence receipt is not READY.' }
    if ([bool]$receipt.migration_completed) { throw 'Accepted equivalence receipt unexpectedly claims migration completed.' }
    if ([string]$receipt.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmManifestSha256) { throw 'Equivalence Warm manifest changed.' }
    $candidates = @($receipt.warm_candidates)
    if ($candidates.Count -ne $script:ExpectedWarmCandidateCount) { throw 'Equivalence candidate array count changed.' }
    foreach ($candidate in $candidates) {
        Assert-SafeTableName ([string]$candidate.table)
        if (-not [string]$candidate.source_disk -or @($candidate.disk_names).Count -ne 1) { throw "Candidate source disk is not frozen: $($candidate.table)" }
    }
    $sumRows = [int64](($candidates | Measure-Object -Property rows_from_parts -Sum).Sum)
    $sumBytes = [int64](($candidates | Measure-Object -Property bytes_on_disk -Sum).Sum)
    if ($sumRows -ne $script:ExpectedWarmRows -or $sumBytes -ne $script:ExpectedWarmBytes) { throw 'Equivalence candidate totals changed.' }
    if ((Get-CandidateManifestHash $candidates) -ne $script:ExpectedWarmManifestSha256) { throw 'Equivalence candidate canonical manifest recomputation failed.' }
    return [ordered]@{ path=$path; sha256=$actualSha; receipt=$receipt; candidates=@($candidates) }
}

function Invoke-ClickHouseMetadataRows([string]$Sql, [string]$Label) {
    $normalized = ' ' + (($Sql -replace '\s+', ' ').Trim().ToUpperInvariant()) + ' '
    if (-not $normalized.TrimStart().StartsWith('SELECT ')) { throw "$Label is not a SELECT." }
    if ($normalized -notmatch ' FROM SYSTEM\.(TABLES|PARTS) ') { throw "$Label may read ClickHouse system.tables/system.parts only." }
    foreach ($token in @(' INSERT ', ' DELETE ', ' UPDATE ', ' DROP ', ' TRUNCATE ', ' OPTIMIZE ', ' ALTER ', ' MOVE ', ' ATTACH ', ' DETACH ', ' RENAME ')) {
        if ($normalized.Contains($token)) { throw "$Label contains forbidden token: $($token.Trim())" }
    }
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& docker compose exec -T clickhouse clickhouse-client --query $Sql --format JSONEachRow 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode. Output: $($lines -join ' | ')" }
    $rows = @()
    foreach ($line in $lines) {
        $text = ([string]$line).Trim()
        if (-not $text) { continue }
        try { $rows += ($text | ConvertFrom-Json) }
        catch { throw "$Label returned non-JSON metadata: $text" }
    }
    return @($rows)
}

function Get-LiveCandidateSnapshot([object[]]$Candidates) {
    $quoted = @($Candidates | ForEach-Object { "'$(Escape-SqlLiteral ([string]$_.table))'" }) -join ','
    $tableSql = @"
SELECT name AS table, engine, sorting_key, primary_key, partition_key, create_table_query
FROM system.tables
WHERE database = 'markorbit_facts' AND name IN ($quoted)
ORDER BY name
"@
    $partSql = @"
SELECT table, partition_id, name, rows, bytes_on_disk, disk_name,
       hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files
FROM system.parts
WHERE database = 'markorbit_facts' AND active AND table IN ($quoted)
ORDER BY table, partition_id, name
"@
    return [ordered]@{
        tables=@(Invoke-ClickHouseMetadataRows $tableSql 'CN Warm live table metadata')
        parts=@(Invoke-ClickHouseMetadataRows $partSql 'CN Warm live part metadata')
    }
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

function Get-LogicalChecksumSql([string]$TableName, [string]$PartitionId) {
    Assert-SafeTableName $TableName
    $partitionLiteral = Escape-SqlLiteral $PartitionId
    return "SELECT count() AS rows, sum(cityHash64(tuple(*))) AS checksum_sum, groupBitXor(cityHash64(tuple(*))) AS checksum_xor FROM markorbit_facts.$TableName WHERE _partition_id = '$partitionLiteral'"
}

function Get-MigrationOrderRank([string]$Tier) {
    if ($Tier -eq 'WARM_EVENT_HISTORY') { return 0 }
    if ($Tier -eq 'WARM_GOODS_CATEGORY') { return 1 }
    return 9
}

function Invoke-ContractFixture {
    $parts = @(
        [pscustomobject]@{ name='p_1_1_0'; rows=10; bytes_on_disk=100; disk_name='default'; hash_of_all_files='aa'; hash_of_uncompressed_files='bb'; uncompressed_hash_of_compressed_files='cc' },
        [pscustomobject]@{ name='p_2_2_0'; rows=20; bytes_on_disk=200; disk_name='default'; hash_of_all_files='dd'; hash_of_uncompressed_files='ee'; uncompressed_hash_of_compressed_files='ff' }
    )
    $a = Get-PartContentFingerprint $parts
    $b = Get-PartContentFingerprint @($parts[1],$parts[0])
    if ($a -ne $b) { throw 'Part-content fingerprint must be order-independent after canonical sort.' }
    $schemaFixture = [pscustomobject]@{ table='cn_observed_event'; engine='MergeTree'; sorting_key='id'; primary_key='id'; partition_key='toYYYYMM(observed_at)'; create_table_query='CREATE TABLE markorbit_facts.cn_observed_event (...) ENGINE = MergeTree ORDER BY id' }
    $schemaA = Get-LiveSchemaFingerprint $schemaFixture
    $schemaB = Get-LiveSchemaFingerprint $schemaFixture
    if ($schemaA -ne $schemaB) { throw 'Live schema fingerprint canonicalization is unstable.' }
    $sql = Get-LogicalChecksumSql 'cn_observed_event' '202601'
    if ($sql -notmatch 'sum\(cityHash64\(tuple\(\*\)\)\)' -or $sql -notmatch 'groupBitXor') { throw 'Logical checksum SQL contract failed.' }
    if ((Get-MigrationOrderRank 'WARM_EVENT_HISTORY') -ge (Get-MigrationOrderRank 'WARM_GOODS_CATEGORY')) { throw 'History-first migration order contract failed.' }
    Write-Host 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_CONTRACT_DIRECT_INVOCATION_OK'
}

try {
    Write-Host '===== PRODUCTION CN WARM MIGRATION DESIGN BUILDER ====='
    Write-Host 'design_only=True'
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'apply_authorized=False'
    Write-Host 'provisioning_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'source_cleanup_authorized=False'
    Write-Host 'logical_checksum_execution_performed=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host 'wsl_mutation_authorized=False'
    Write-Host 'docker_mutation_authorized=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ($ContractOnly) { Invoke-ContractFixture; exit 0 }

    if ((git branch --show-current).Trim() -ne 'main') { throw 'CN Warm migration design builder must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $acceptedProvisioning = Resolve-AcceptedProvisioningReceipt
    $acceptedEquivalence = Resolve-AcceptedEquivalenceReceipt $acceptedProvisioning.receipt

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env missing.' }
    $envShaBefore = Get-FileSha256 $envPath
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root unexpectedly exists.' }
    if (-not (Test-Path -LiteralPath $script:ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX missing.' }
    $fInfo = New-Object System.IO.FileInfo($script:ExpectedFRecoveryVhdx)
    if ([int64]$fInfo.Length -ne $script:ExpectedFRecoveryBytes) { throw 'Retained F recovery VHDX length changed.' }
    if (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath) { throw 'Production Warm VHDX path exists before an explicit Apply authorization.' }

    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before migration design snapshot.' }
    Assert-AcceptedProductionMount $productionBefore.container_id
    if ([string]$productionBefore.version -ne $script:ProductionClickHouseVersion) { throw 'Production ClickHouse version drifted from the frozen design version.' }
    Assert-ExactMain 'metadata_before'

    $live = Get-LiveCandidateSnapshot $acceptedEquivalence.candidates
    if (@($live.tables).Count -ne $script:ExpectedWarmCandidateCount) { throw 'Live candidate table metadata count changed.' }

    $consumerReports = @($acceptedEquivalence.receipt.consumer_equivalence.reports)
    $tablePlans = @()
    $blockers = @()

    foreach ($candidate in $acceptedEquivalence.candidates) {
        $tableName = [string]$candidate.table
        $tableMeta = @($live.tables | Where-Object { [string]$_.table -eq $tableName })
        $consumer = @($consumerReports | Where-Object { [string]$_.table -eq $tableName })
        if ($tableMeta.Count -ne 1) { $blockers += "LIVE_TABLE_METADATA_COUNT:$tableName"; continue }
        if ($consumer.Count -ne 1) { $blockers += "CONSUMER_REPORT_COUNT:$tableName"; continue }

        $liveSchemaFingerprint = Get-LiveSchemaFingerprint $tableMeta[0]
        if ($liveSchemaFingerprint -ne [string]$candidate.schema_fingerprint_sha256) { $blockers += "SCHEMA_FINGERPRINT_DRIFT:$tableName" }

        $parts = @($live.parts | Where-Object { [string]$_.table -eq $tableName })
        $liveRows = [int64](($parts | Measure-Object -Property rows -Sum).Sum)
        $liveBytes = [int64](($parts | Measure-Object -Property bytes_on_disk -Sum).Sum)
        if ($parts.Count -ne [int64]$candidate.active_parts) { $blockers += "ACTIVE_PART_COUNT_DRIFT:$tableName" }
        if ($liveRows -ne [int64]$candidate.rows_from_parts) { $blockers += "ROW_COUNT_DRIFT:$tableName" }
        if ($liveBytes -ne [int64]$candidate.bytes_on_disk) { $blockers += "BYTE_COUNT_DRIFT:$tableName" }
        $diskNames = @($parts | ForEach-Object { [string]$_.disk_name } | Sort-Object -Unique)
        if ($diskNames.Count -ne 1 -or $diskNames[0] -ne [string]$candidate.source_disk) { $blockers += "SOURCE_DISK_DRIFT:$tableName" }

        $partitionPlans = @()
        foreach ($partitionGroup in @($parts | Group-Object partition_id | Sort-Object Name)) {
            $partitionParts = @($partitionGroup.Group)
            $partitionRows = [int64](($partitionParts | Measure-Object -Property rows -Sum).Sum)
            $partitionBytes = [int64](($partitionParts | Measure-Object -Property bytes_on_disk -Sum).Sum)
            $partitionPlans += [ordered]@{
                partition_id=[string]$partitionGroup.Name
                active_parts=[int64]$partitionParts.Count
                rows=$partitionRows
                bytes_on_disk=$partitionBytes
                source_disk=[string]$candidate.source_disk
                source_part_content_manifest_sha256=(Get-PartContentFingerprint $partitionParts)
                source_residency_manifest_sha256=(Get-ResidencyFingerprint $partitionParts)
                logical_checksum_sql=(Get-LogicalChecksumSql $tableName ([string]$partitionGroup.Name))
                logical_checksum_execution_required_before_future_transfer=$true
                logical_checksum_execution_required_after_future_transfer=$true
                future_transfer_unit='PARTITION_OR_SINGLE_ALL_PARTITION'
                future_apply_authorized=$false
            }
        }

        $tablePlans += [pscustomobject]@{
            table=$tableName
            proposed_tier=[string]$candidate.proposed_tier
            order_rank=(Get-MigrationOrderRank ([string]$candidate.proposed_tier))
            bytes_on_disk=[int64]$candidate.bytes_on_disk
            rows=[int64]$candidate.rows_from_parts
            active_parts=[int64]$candidate.active_parts
            source_disk=[string]$candidate.source_disk
            rollback_target_source_disk=[string]$candidate.rollback_target_source_disk
            target_disk=$script:WarmClickHouseDiskName
            target_storage_policy=$script:WarmStoragePolicyName
            schema_fingerprint_sha256=$liveSchemaFingerprint
            engine=[string]$tableMeta[0].engine
            sorting_key=[string]$tableMeta[0].sorting_key
            primary_key=[string]$tableMeta[0].primary_key
            partition_key=[string]$tableMeta[0].partition_key
            source_part_content_manifest_sha256=(Get-PartContentFingerprint $parts)
            source_residency_manifest_sha256=(Get-ResidencyFingerprint $parts)
            direct_serving_read_count=[int64]$consumer[0].direct_serving_read_count
            runtime_write_count=[int64]$consumer[0].runtime_write_count
            performance_sensitive_post_move_acceptance_required=[bool]$consumer[0].performance_sensitive_post_move_acceptance_required
            partitions=@($partitionPlans)
        }
    }

    $orderedPlans = @($tablePlans | Sort-Object order_rank, bytes_on_disk, table)
    for ($i = 0; $i -lt $orderedPlans.Count; $i++) { $orderedPlans[$i] | Add-Member -NotePropertyName migration_order -NotePropertyValue ([int64]($i + 1)) }

    $eDrive = New-Object System.IO.DriveInfo('E')
    $eTotal = [int64]$eDrive.TotalSize
    $eFree = [int64]$eDrive.AvailableFreeSpace
    if ($eTotal -ne $script:ExpectedETotalBytes) { $blockers += 'E_TOTAL_BYTES_DRIFT' }
    $reserve = [int64][math]::Ceiling([double]$eTotal * 0.30)
    $budget = [int64][math]::Max([int64]0, [int64]($eFree - $reserve))
    $marginAfterMax = [int64]($budget - $script:ExpectedWarmVhdxMaxBytes)
    if ($marginAfterMax -lt 0) { $blockers += 'E_30_PERCENT_ADMISSION_LOST' }
    $blockers = @($blockers | Sort-Object -Unique)

    Assert-RawConsumersStopped
    $productionAfter = Get-ProductionClickHouseHealth
    if (-not [bool]$productionAfter.ready) { throw 'Production ClickHouse must remain healthy after migration design snapshot.' }
    Assert-AcceptedProductionMount $productionAfter.container_id
    Assert-ExactMain 'final'
    if ((Get-FileSha256 $envPath) -ne $envShaBefore) { throw '.env changed during migration design builder.' }
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root reappeared.' }
    if (-not (Test-Path -LiteralPath $script:ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX disappeared.' }
    if (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath) { throw 'Production Warm VHDX was created during design-only gate.' }

    $ready = [bool]($blockers.Count -eq 0 -and $orderedPlans.Count -eq $script:ExpectedWarmCandidateCount)
    $decision = if ($ready) { 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_READY_FOR_REVIEW' } else { 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_BLOCKED' }
    $nextGate = if ($ready) { 'EXPLICIT_OPERATOR_REVIEW_BEFORE_ANY_PRODUCTION_APPLY' } else { 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_REVIEW' }

    $timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_cn_warm_migration_design_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receipt = [ordered]@{
        receipt_version=$script:DesignReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        architecture_version=$script:DesignArchitectureVersion
        decision=$decision
        next_gate=$nextGate
        design_only=$true
        read_only=$true
        mutation_performed=$false
        apply_authorized=$false
        provisioning_authorized=$false
        cn_warm_move_authorized=$false
        source_cleanup_authorized=$false
        logical_checksum_execution_performed=$false
        accepted_provisioning=[ordered]@{
            engine_sha=$script:AcceptedProvisioningEngineSha
            receipt_path=$acceptedProvisioning.path
            receipt_sha256=$acceptedProvisioning.sha256
            decision='PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT_READY'
        }
        accepted_equivalence=[ordered]@{
            receipt_path=$acceptedEquivalence.path
            receipt_sha256=$acceptedEquivalence.sha256
            warm_candidate_manifest_sha256=$script:ExpectedWarmManifestSha256
            candidate_count=$script:ExpectedWarmCandidateCount
            rows=$script:ExpectedWarmRows
            bytes=$script:ExpectedWarmBytes
            copy_required_bytes=$script:ExpectedWarmCopyRequiredBytes
        }
        production_topology=[ordered]@{
            current_source_runtime='DOCKER_DESKTOP_CLICKHOUSE_ACCEPTED_NAMED_VOLUME'
            accepted_source_volume=$AcceptedVolume
            target_runtime='DEDICATED_ORDINARY_WSL2_CLICKHOUSE'
            target_runtime_distro=$script:ProductionRuntimeDistro
            target_runtime_root=$script:ProductionRuntimeRoot
            pinned_clickhouse_version=$script:ProductionClickHouseVersion
            warm_vhdx_path=$script:ExpectedWarmVhdxPath
            warm_mount_name=$script:ExpectedWarmMountName
            warm_filesystem='ext4'
            warm_ext4_quota_bytes=$script:ExpectedWarmExt4QuotaBytes
            warm_vhdx_max_bytes=$script:ExpectedWarmVhdxMaxBytes
            warm_clickhouse_disk_name=$script:WarmClickHouseDiskName
            warm_storage_policy_name=$script:WarmStoragePolicyName
            warm_clickhouse_disk_path=$script:WarmClickHouseDiskPath
            docker_desktop_external_mnt_wsl_bind_retry_allowed=$false
        }
        transfer_design=[ordered]@{
            strategy=$script:TransferStrategy
            source_and_target_are_distinct_clickhouse_runtimes=$true
            filesystem_level_move_between_runtimes_assumed=$false
            blind_full_cn_recopy_allowed=$false
            only_frozen_warm_candidates_in_scope=$true
            migration_order_basis=$script:MigrationOrderBasis
            future_target_to_source_native_connectivity_preflight_required=$true
            future_empty_target_disk_acceptance_required=$true
            future_per_partition_logical_checksum_required=$true
        }
        candidates=@($orderedPlans)
        acceptance_contract=[ordered]@{
            pre_unit=@('exact_main_and_receipt_identity','source_schema_fingerprint','source_rows','source_active_parts','source_part_content_manifest','source_disk_residency','logical_checksum_source','writer_quiesce_or_placement_contract','fresh_e_30_percent_headroom')
            post_unit=@('schema_equivalence','row_count_equivalence','logical_checksum_equivalence','target_disk_residency','writer_placement_acceptance','direct_serving_query_acceptance','summary_and_case_api_acceptance','latency_regression_acceptance','zero_rename_commit_permission_error_class')
            rollback=@('rollback_target_source_disk_frozen','rollback_before_source_cleanup','schema_rows_checksum_residency_reverified','writer_query_api_acceptance_reverified')
        }
        phases=@(
            [ordered]@{ phase='A'; name='PROVISIONING_APPLY'; authorized=$false; requires_explicit_operator_go=$true },
            [ordered]@{ phase='B'; name='EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE'; authorized=$false; requires_phase='A' },
            [ordered]@{ phase='C'; name='BOUNDED_CN_WARM_MIGRATION_UNITS'; authorized=$false; requires_phase='B'; per_unit_receipt_required=$true },
            [ordered]@{ phase='D'; name='FINAL_CROSS_TABLE_ACCEPTANCE'; authorized=$false; requires_phase='C' },
            [ordered]@{ phase='E'; name='SOURCE_POLICY_CLEANUP'; authorized=$false; separate_future_issue_required=$true }
        )
        global_abort_rules=@(
            'MAIN_OR_RECEIPT_OR_MANIFEST_DRIFT','PRODUCTION_CLICKHOUSE_UNHEALTHY','ACCEPTED_SOURCE_VOLUME_IDENTITY_DRIFT',
            'RAW_OR_RUNTIME_CONSUMER_SAFETY_DRIFT','E_30_PERCENT_RESERVE_OR_HEADROOM_VIOLATION','UNEXPECTED_VHDX_WSL_PATH_OR_MOUNT_COLLISION',
            'SCHEMA_ROW_OR_CHECKSUM_MISMATCH','WRITER_QUERY_API_OR_LATENCY_ACCEPTANCE_FAILURE','RENAME_COMMIT_OR_PERMISSION_ERROR_CLASS'
        )
        capacity=[ordered]@{
            e_total_bytes=$eTotal
            e_free_bytes=$eFree
            recommended_30_percent_reserve_bytes=$reserve
            recommended_allocation_budget_bytes=$budget
            proposed_warm_vhdx_max_bytes=$script:ExpectedWarmVhdxMaxBytes
            recommended_margin_after_proposed_max_bytes=$marginAfterMax
            recommended_30_percent_admission=[bool]($marginAfterMax -ge 0)
        }
        production_invariants=[ordered]@{
            exact_clean_main=$true
            raw_consumers_stopped=$true
            production_clickhouse_ready=$true
            production_clickhouse_version=$productionBefore.version
            accepted_named_volume_mounted=$true
            env_unchanged=$true
            e_backup_root_absent=$true
            f_recovery_preserved=$true
            f_recovery_bytes=[int64]$fInfo.Length
            proposed_warm_vhdx_still_absent=$true
        }
        blockers=@($blockers)
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
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_cn_warm_migration_design.json'
    $receipt | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    Assert-ExactMain 'exit'

    Write-Host '===== PRODUCTION CN WARM MIGRATION DESIGN RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "accepted_provisioning_receipt_sha256=$($acceptedProvisioning.sha256)"
    Write-Host "accepted_equivalence_receipt_sha256=$($acceptedEquivalence.sha256)"
    Write-Host "warm_candidate_manifest_sha256=$script:ExpectedWarmManifestSha256"
    Write-Host "candidate_count=$($orderedPlans.Count)"
    foreach ($plan in $orderedPlans) {
        Write-Host "candidate=$($plan.migration_order)|$($plan.table)|tier=$($plan.proposed_tier)|rows=$($plan.rows)|bytes=$($plan.bytes_on_disk)|parts=$($plan.active_parts)|source=$($plan.source_disk)|schema_sha256=$($plan.schema_fingerprint_sha256)|part_content_sha256=$($plan.source_part_content_manifest_sha256)"
        foreach ($partition in @($plan.partitions)) {
            Write-Host "migration_unit=$($plan.table)|partition=$($partition.partition_id)|rows=$($partition.rows)|bytes=$($partition.bytes_on_disk)|parts=$($partition.active_parts)|part_content_sha256=$($partition.source_part_content_manifest_sha256)"
        }
    }
    Write-Host "target_runtime_distro=$script:ProductionRuntimeDistro"
    Write-Host "target_runtime_root=$script:ProductionRuntimeRoot"
    Write-Host "target_clickhouse_version=$script:ProductionClickHouseVersion"
    Write-Host "warm_vhdx_path=$script:ExpectedWarmVhdxPath"
    Write-Host "warm_ext4_quota_bytes=$script:ExpectedWarmExt4QuotaBytes"
    Write-Host "warm_vhdx_max_bytes=$script:ExpectedWarmVhdxMaxBytes"
    Write-Host "warm_clickhouse_disk_name=$script:WarmClickHouseDiskName"
    Write-Host "warm_storage_policy_name=$script:WarmStoragePolicyName"
    Write-Host "transfer_strategy=$script:TransferStrategy"
    Write-Host "e_free_bytes=$eFree"
    Write-Host "e_recommended_margin_after_proposed_max_bytes=$marginAfterMax"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'design_only=True'
    Write-Host 'apply_authorized=False'
    Write-Host 'provisioning_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'source_cleanup_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_DONE'
    if (-not $ready) { exit 4 }
    exit 0
}
catch {
    Write-Host "PRODUCTION_CN_WARM_MIGRATION_DESIGN_FAILED: $($_.Exception.Message)"
    exit 2
}
finally { Pop-Location }
