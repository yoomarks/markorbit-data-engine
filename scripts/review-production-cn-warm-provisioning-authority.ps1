[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedDesignReceiptPath,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedChecksumReceiptPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedChecksumReceiptSha256,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$ToolingDistro = 'Ubuntu-24.04',
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedDesignEngineSha = '58a719a60997ea09e117b0354394a7c59ba0bc23'
$script:AcceptedChecksumEngineSha = '03eff11aef70b2b55134d4c402424f4aec9e84f0'
$script:AcceptedDesignReceiptVersion = 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_V1'
$script:AcceptedChecksumReceiptVersion = 'PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V2'
$script:ReviewReceiptVersion = 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_V1'
$script:ChecksumDefinition = 'NULL_SAFE_JSON_TUPLE_CITYHASH64_V2'
$script:ExpectedDesignReceiptSha256 = '07a7af0bff5b97379c1a5203059f456746f789914040da8c037a37b755cfd837'
$script:ExpectedWarmManifestSha256 = '716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231'
$script:ExpectedChecksumManifestSha256 = '4aa3ae5f0d9b8c903b6275ea9a341a9b66f20843c19139a4a8355ca07e38d41a'
$script:ExpectedWarmCandidateCount = [int64]4
$script:ExpectedWarmRows = [int64]2430570761
$script:ExpectedWarmBytes = [int64]562600035674
$script:ExpectedWarmVhdxPath = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ExpectedWarmVhdxMaxBytes = [int64]842887331840
$script:ExpectedWarmMountName = 'markorbit_prod_warm_cn'
$script:ExpectedTargetDistro = 'MarkOrbit-ClickHouse'
$script:ExpectedTargetRuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse'
$script:ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery'
$script:ProductionClickHouseVersion = '24.8.14.39'
$script:AllowedReviewFiles = @(
    'scripts/review-production-cn-warm-provisioning-authority.ps1',
    'tests/test_production_cn_warm_provisioning_authority_review_contract.py',
    '.github/workflows/production-cn-warm-provisioning-authority-review-runtime.yml'
)
$script:ExpectedUnits = @(
    [ordered]@{ migration_order=1; table='cn_observed_event'; partition_id='all'; rows=[int64]413031435; bytes=[int64]127856495167; active_parts=[int64]11; checksum_sum='1808985432469329985'; checksum_xor='4542061904064739473'; logical_sql_v2_sha256='482429b856ee737816a7edb8eb60104dccdc9bb1c9c534268a7d0bbbe826728d'; result_sha256='a73f666a756142d3d896ff628dcd65092baa8298f5f4d8cf61e5c5e295dcde95' },
    [ordered]@{ migration_order=2; table='cn_goods_scope_lifecycle_current'; partition_id='all'; rows=[int64]158355910; bytes=[int64]4696234780; active_parts=[int64]11; checksum_sum='2621650965579445306'; checksum_xor='2218616543529267632'; logical_sql_v2_sha256='47e93ec47a23403ece9396286369e3eb7214f5d2ab5865865a4a54604d4ed59b'; result_sha256='4c407a648d6fcf9e2c41df8f2a6201661cdcf4853a65ddf31341eaa0e8f23ab4' },
    [ordered]@{ migration_order=3; table='cn_goods_item_observation'; partition_id='all'; rows=[int64]219463289; bytes=[int64]58772877234; active_parts=[int64]14; checksum_sum='4839797850453995995'; checksum_xor='512498440194446077'; logical_sql_v2_sha256='78a0c0640847aa432a41493275209f3fedc93a2b774f19893617f3d9f66e5a82'; result_sha256='dc7349b10a2e306e96e65ecb735f5f562a090d589e2108e1d1d7fc92db53ea98' },
    [ordered]@{ migration_order=4; table='cn_goods_item_current'; partition_id='all'; rows=[int64]1639720127; bytes=[int64]371274428493; active_parts=[int64]10; checksum_sum='1970976759734100945'; checksum_xor='2436627126989268581'; logical_sql_v2_sha256='83af3e57370b8bcdca6929a0556bb1a676bcbadb5a0becec79c6d58fdffbca8d'; result_sha256='4e8e505aa166d268e4b3e75b47a9198ec4a615aa944a93b65943c565b644550d' }
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
    $Value | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Escape-SqlLiteral([string]$Value) { return $Value.Replace("'", "''") }

function Assert-SafeTableName([string]$TableName) {
    if ($TableName -notmatch '^cn_[a-z0-9_]+$') { throw "Unsafe CN table name: $TableName" }
}

function Normalize-WindowsPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $candidate = $Path.Trim()
    if ($candidate.StartsWith('\\?\')) { $candidate = $candidate.Substring(4) }
    if ($candidate.StartsWith('\??\')) { $candidate = $candidate.Substring(4) }
    if ($candidate -notmatch '^[A-Za-z]:[\\/]') { return '' }
    return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
}

function Assert-ToolingProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedChecksumEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted checksum engine SHA is not an ancestor of exact main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedChecksumEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedReviewFiles })
    $missing = @($script:AllowedReviewFiles | Where-Object { $_ -notin $changed })
    Write-Host "accepted_checksum_to_current_changed_file_count=$($changed.Count)"
    Write-Host "accepted_checksum_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "accepted_checksum_to_current_missing_review_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'Provisioning authority review tooling changed outside the exact 3-file boundary.'
    }
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

function Assert-SnapshotMatchesDesign([object]$Snapshot, [object]$Plan, [object]$Unit) {
    if ([string]$Snapshot.schema_fingerprint_sha256 -ne [string]$Plan.schema_fingerprint_sha256) { throw "SCHEMA_FINGERPRINT_DRIFT:$($Plan.table)" }
    if ([int64]$Snapshot.table_rows -ne [int64]$Plan.rows) { throw "TABLE_ROW_COUNT_DRIFT:$($Plan.table)" }
    if ([int64]$Snapshot.table_bytes -ne [int64]$Plan.bytes_on_disk) { throw "TABLE_BYTE_COUNT_DRIFT:$($Plan.table)" }
    if ([int64]$Snapshot.table_active_parts -ne [int64]$Plan.active_parts) { throw "TABLE_ACTIVE_PART_COUNT_DRIFT:$($Plan.table)" }
    if ([string]$Snapshot.table_part_content_sha256 -ne [string]$Plan.source_part_content_manifest_sha256) { throw "TABLE_PART_CONTENT_DRIFT:$($Plan.table)" }
    if ([string]$Snapshot.table_residency_sha256 -ne [string]$Plan.source_residency_manifest_sha256) { throw "TABLE_RESIDENCY_DRIFT:$($Plan.table)" }
    if (@($Snapshot.table_disks).Count -ne 1 -or [string]$Snapshot.table_disks[0] -ne [string]$Plan.source_disk) { throw "TABLE_SOURCE_DISK_DRIFT:$($Plan.table)" }
    if ([int64]$Snapshot.unit_rows -ne [int64]$Unit.rows) { throw "UNIT_ROW_COUNT_DRIFT:$($Plan.table)" }
    if ([int64]$Snapshot.unit_bytes -ne [int64]$Unit.bytes_on_disk) { throw "UNIT_BYTE_COUNT_DRIFT:$($Plan.table)" }
    if ([int64]$Snapshot.unit_active_parts -ne [int64]$Unit.active_parts) { throw "UNIT_ACTIVE_PART_COUNT_DRIFT:$($Plan.table)" }
    if ([string]$Snapshot.unit_part_content_sha256 -ne [string]$Unit.source_part_content_manifest_sha256) { throw "UNIT_PART_CONTENT_DRIFT:$($Plan.table)" }
    if ([string]$Snapshot.unit_residency_sha256 -ne [string]$Unit.source_residency_manifest_sha256) { throw "UNIT_RESIDENCY_DRIFT:$($Plan.table)" }
}

function Get-UnitResultSha([object]$Result) {
    return Get-StringSha256 (@(
        [string]$Result.table,
        [string]$Result.partition_id,
        [string]$Result.rows,
        [string]$Result.checksum_sum,
        [string]$Result.checksum_xor,
        [string]$Result.design_v1_logical_sql_sha256,
        [string]$Result.logical_sql_v2_sha256,
        [string]$Result.execution_query_sha256,
        [string]$Result.source_identity_sha256
    ) -join '|')
}

function Resolve-AcceptedDesignReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedDesignReceiptPath)
    $sha = Get-FileSha256 $path
    if ($sha -ne $script:ExpectedDesignReceiptSha256) { throw 'Accepted design receipt SHA256 changed.' }
    $receipt = Read-JsonFile $path 'Accepted migration design receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedDesignReceiptVersion) { throw 'Unexpected design receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedDesignEngineSha) { throw 'Accepted design engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_CN_WARM_MIGRATION_DESIGN_READY_FOR_REVIEW') { throw 'Accepted design receipt is not READY.' }
    if (-not [bool]$receipt.design_only -or -not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed) { throw 'Accepted design safety state changed.' }
    if ([string]$receipt.accepted_equivalence.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmManifestSha256) { throw 'Accepted Warm manifest changed in design receipt.' }
    if ([int64]$receipt.accepted_equivalence.candidate_count -ne $script:ExpectedWarmCandidateCount) { throw 'Accepted design candidate count changed.' }
    if ([int64]$receipt.accepted_equivalence.rows -ne $script:ExpectedWarmRows -or [int64]$receipt.accepted_equivalence.bytes -ne $script:ExpectedWarmBytes) { throw 'Accepted design totals changed.' }
    if ([string]$receipt.production_topology.accepted_source_volume -ne $AcceptedVolume) { throw 'Accepted source volume changed in design receipt.' }
    if ([string]$receipt.production_topology.warm_vhdx_path -ne $script:ExpectedWarmVhdxPath) { throw 'Warm VHDX path changed in design receipt.' }
    if ([string]$receipt.production_topology.target_runtime_distro -ne $script:ExpectedTargetDistro) { throw 'Target runtime distro changed in design receipt.' }
    if ([string]$receipt.production_topology.target_runtime_root -ne $script:ExpectedTargetRuntimeRoot) { throw 'Target runtime root changed in design receipt.' }
    foreach ($embedded in @(
        [ordered]@{ label='provisioning'; path=[string]$receipt.accepted_provisioning.receipt_path; sha=[string]$receipt.accepted_provisioning.receipt_sha256 },
        [ordered]@{ label='equivalence'; path=[string]$receipt.accepted_equivalence.receipt_path; sha=[string]$receipt.accepted_equivalence.receipt_sha256 }
    )) {
        $embeddedPath = [System.IO.Path]::GetFullPath([string]$embedded.path)
        if ((Get-FileSha256 $embeddedPath) -ne [string]$embedded.sha) { throw "Embedded $($embedded.label) receipt SHA drifted." }
    }
    $plans = @($receipt.candidates | Sort-Object migration_order)
    if ($plans.Count -ne $script:ExpectedWarmCandidateCount) { throw 'Design candidate plan count changed.' }
    return [ordered]@{ path=$path; sha256=$sha; receipt=$receipt; plans=@($plans) }
}

function Resolve-AcceptedChecksumReceipt([object]$AcceptedDesign) {
    $path = [System.IO.Path]::GetFullPath($AcceptedChecksumReceiptPath)
    $sha = Get-FileSha256 $path
    if ($sha -ne $ExpectedChecksumReceiptSha256.Trim().ToLowerInvariant()) { throw 'Accepted V2 checksum receipt SHA256 changed.' }
    $receipt = Read-JsonFile $path 'Accepted V2 checksum receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedChecksumReceiptVersion) { throw 'Unexpected V2 checksum receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedChecksumEngineSha) { throw 'Accepted V2 checksum engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_CN_WARM_SOURCE_LOGICAL_CHECKSUM_V2_READY_FOR_REVIEW') { throw 'Accepted V2 checksum receipt is not READY.' }
    if ([string]$receipt.next_gate -ne 'EXPLICIT_OPERATOR_REVIEW_OF_PROVISIONING_AUTHORIZATION') { throw 'Accepted V2 checksum next gate changed.' }
    if ([string]$receipt.checksum_definition -ne $script:ChecksumDefinition) { throw 'Accepted V2 checksum definition changed.' }
    if (-not [bool]$receipt.read_only -or [bool]$receipt.mutation_performed -or -not [bool]$receipt.logical_checksum_execution_performed) { throw 'Accepted V2 checksum safety/execution state changed.' }
    foreach ($name in @('apply_authorized','provisioning_authorized','cn_warm_move_authorized','source_cleanup_authorized')) {
        if ([bool]$receipt.$name) { throw "Accepted V2 checksum unexpectedly authorizes $name." }
    }
    if ([string]$receipt.accepted_design.receipt_sha256 -ne $AcceptedDesign.sha256) { throw 'V2 checksum no longer binds the accepted design receipt SHA.' }
    if ((Normalize-WindowsPath ([string]$receipt.accepted_design.receipt_path)) -ne (Normalize-WindowsPath $AcceptedDesign.path)) { throw 'V2 checksum design receipt path changed.' }
    if ([string]$receipt.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmManifestSha256) { throw 'V2 checksum Warm manifest changed.' }
    if ([string]$receipt.checksum_manifest_sha256 -ne $script:ExpectedChecksumManifestSha256) { throw 'V2 checksum manifest changed.' }
    if ([int64]$receipt.candidate_count -ne $script:ExpectedWarmCandidateCount -or [int64]$receipt.migration_unit_count -ne $script:ExpectedWarmCandidateCount) { throw 'V2 checksum candidate/unit count changed.' }
    if ([int64]$receipt.rows -ne $script:ExpectedWarmRows -or [int64]$receipt.bytes -ne $script:ExpectedWarmBytes) { throw 'V2 checksum totals changed.' }
    $units = @($receipt.units | Sort-Object migration_order)
    if ($units.Count -ne $script:ExpectedWarmCandidateCount) { throw 'V2 checksum unit array count changed.' }
    for ($i = 0; $i -lt $script:ExpectedUnits.Count; $i++) {
        $expected = $script:ExpectedUnits[$i]
        $actual = $units[$i]
        foreach ($field in @('migration_order','table','partition_id','rows','bytes_on_disk','active_parts','checksum_sum','checksum_xor','logical_sql_v2_sha256','result_sha256')) {
            $actualValue = if ($field -eq 'bytes_on_disk') { [string]$actual.bytes_on_disk } else { [string]$actual.$field }
            $expectedValue = if ($field -eq 'bytes_on_disk') { [string]$expected.bytes } else { [string]$expected.$field }
            if ($actualValue -ne $expectedValue) { throw "V2 checksum unit field drift: $($expected.table)/$field" }
        }
        if ([string]$actual.result_sha256 -ne (Get-UnitResultSha $actual)) { throw "V2 checksum unit result hash failed recomputation: $($expected.table)" }
    }
    $manifestLines = @($units | ForEach-Object { "$($_.table)|$($_.partition_id)|$($_.rows)|$($_.checksum_sum)|$($_.checksum_xor)|$($_.design_v1_logical_sql_sha256)|$($_.logical_sql_v2_sha256)|$($_.execution_query_sha256)|$($_.source_identity_sha256)|$($_.result_sha256)|$script:ChecksumDefinition" })
    $recomputedManifest = Get-StringSha256 ($manifestLines -join "`n")
    if ($recomputedManifest -ne $script:ExpectedChecksumManifestSha256) { throw 'V2 checksum manifest failed canonical recomputation.' }
    return [ordered]@{ path=$path; sha256=$sha; receipt=$receipt; units=@($units); recomputed_manifest_sha256=$recomputedManifest }
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
    return @($rows | Sort-Object name)
}

function Get-WslMountInventory([string]$DistroName) {
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$DistroName,'-u','root','--','findmnt','-rn','-o','SOURCE,TARGET,FSTYPE') -AllowFailure
    if ($probe.exit_code -ne 0) { throw "Unable to inspect WSL mount inventory through $DistroName." }
    $rows = @()
    foreach ($line in @($probe.lines)) {
        $text = ([string]$line).Trim()
        if (-not $text -or $text -notmatch '\s/mnt/wsl/') { continue }
        $parts = @($text -split '\s+', 3)
        $rows += [ordered]@{ source=$parts[0]; target=$parts[1]; filesystem=if ($parts.Count -ge 3) { $parts[2] } else { '' } }
    }
    return @($rows)
}

function Get-DriveCapacity([string]$DriveLetter) {
    $drive = New-Object System.IO.DriveInfo($DriveLetter)
    if (-not $drive.IsReady) { throw "$DriveLetter drive is not ready." }
    return [ordered]@{ total_bytes=[int64]$drive.TotalSize; free_bytes=[int64]$drive.AvailableFreeSpace }
}

function Invoke-ContractFixture {
    $rows = [int64](($script:ExpectedUnits | Measure-Object -Property rows -Sum).Sum)
    $bytes = [int64](($script:ExpectedUnits | Measure-Object -Property bytes -Sum).Sum)
    if ($rows -ne $script:ExpectedWarmRows) { throw 'Expected unit row contract no longer sums.' }
    if ($bytes -ne $script:ExpectedWarmBytes) { throw 'Expected unit byte contract no longer sums.' }
    $reserve = [int64][math]::Ceiling([double](100GB) * 0.30)
    $margin = [int64](90GB) - [int64](50GB) - $reserve
    if ($margin -ne [int64](10GB)) { throw '30 percent reserve arithmetic contract failed.' }
    if ($script:AllowedReviewFiles.Count -ne 3) { throw 'Review tooling boundary must remain exactly three files.' }
    Write-Host 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_CONTRACT_DIRECT_INVOCATION_OK'
}

try {
    Write-Host '===== PRODUCTION CN WARM PROVISIONING AUTHORITY REVIEW ====='
    Write-Host 'review_only=True'
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
    Write-Host 'cross_runtime_transfer_authorized=False'
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ($ContractOnly) { Invoke-ContractFixture; exit 0 }

    if ((git branch --show-current).Trim() -ne 'main') { throw 'Provisioning authority review must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-ToolingProvenance

    $acceptedDesign = Resolve-AcceptedDesignReceipt
    $acceptedChecksum = Resolve-AcceptedChecksumReceipt $acceptedDesign

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env missing.' }
    $envShaBefore = Get-FileSha256 $envPath
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root unexpectedly exists.' }
    if (-not (Test-Path -LiteralPath $script:ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX missing.' }
    $fInfo = New-Object System.IO.FileInfo($script:ExpectedFRecoveryVhdx)
    if ([int64]$fInfo.Length -ne $script:ExpectedFRecoveryBytes) { throw 'Retained F recovery VHDX length changed.' }

    Assert-RawConsumersStopped
    $production = Get-ProductionClickHouseHealth
    if (-not [bool]$production.ready) { throw 'Production ClickHouse must be healthy for provisioning authority review.' }
    if ([string]$production.version -ne $script:ProductionClickHouseVersion) { throw 'Production ClickHouse version drifted.' }
    Assert-AcceptedProductionMount $production.container_id

    $blockers = @()
    $liveUnits = @()
    foreach ($plan in $acceptedDesign.plans) {
        Assert-SafeTableName ([string]$plan.table)
        $designUnits = @($plan.partitions)
        if ($designUnits.Count -ne 1) { $blockers += "UNEXPECTED_DESIGN_UNIT_COUNT:$($plan.table)"; continue }
        $unit = $designUnits[0]
        $expectedChecksumUnit = @($acceptedChecksum.units | Where-Object { [string]$_.table -eq [string]$plan.table -and [string]$_.partition_id -eq [string]$unit.partition_id })
        if ($expectedChecksumUnit.Count -ne 1) { $blockers += "CHECKSUM_UNIT_IDENTITY_MISSING:$($plan.table)"; continue }
        try {
            $snapshot = Get-LiveUnitSnapshot $plan $unit
            Assert-SnapshotMatchesDesign $snapshot $plan $unit
            $sourceIdentity = Get-SourceIdentitySha $snapshot $plan $unit
            if ($sourceIdentity -ne [string]$expectedChecksumUnit[0].source_identity_sha256) { throw "SOURCE_IDENTITY_DRIFT:$($plan.table)" }
            if ([string]$snapshot.schema_fingerprint_sha256 -ne [string]$expectedChecksumUnit[0].source_schema_sha256) { throw "CHECKSUM_SCHEMA_IDENTITY_DRIFT:$($plan.table)" }
            if ([string]$snapshot.unit_part_content_sha256 -ne [string]$expectedChecksumUnit[0].source_part_content_sha256) { throw "CHECKSUM_PART_CONTENT_IDENTITY_DRIFT:$($plan.table)" }
            if ([string]$snapshot.unit_residency_sha256 -ne [string]$expectedChecksumUnit[0].source_residency_sha256) { throw "CHECKSUM_RESIDENCY_IDENTITY_DRIFT:$($plan.table)" }
            $liveUnits += [ordered]@{ table=[string]$plan.table; partition_id=[string]$unit.partition_id; source_identity_sha256=$sourceIdentity; schema_sha256=[string]$snapshot.schema_fingerprint_sha256; part_content_sha256=[string]$snapshot.unit_part_content_sha256; residency_sha256=[string]$snapshot.unit_residency_sha256; rows=[int64]$snapshot.unit_rows; bytes=[int64]$snapshot.unit_bytes; active_parts=[int64]$snapshot.unit_active_parts }
            Write-Host "source_identity_ready=$($plan.table)|$($unit.partition_id)|rows=$($snapshot.unit_rows)|parts=$($snapshot.unit_active_parts)|source_identity_sha256=$sourceIdentity"
        }
        catch { $blockers += $_.Exception.Message }
    }

    $capacity = Get-DriveCapacity 'E:\'
    $reserve = [int64][math]::Ceiling([double]$capacity.total_bytes * 0.30)
    $marginAfterMax = [int64]($capacity.free_bytes - $script:ExpectedWarmVhdxMaxBytes - $reserve)
    $admission = [bool]($marginAfterMax -ge 0)
    Write-Host "e_total_bytes=$($capacity.total_bytes)"
    Write-Host "e_free_bytes=$($capacity.free_bytes)"
    Write-Host "e_recommended_30_percent_reserve_bytes=$reserve"
    Write-Host "e_margin_after_proposed_max_bytes=$marginAfterMax"
    Write-Host "recommended_30_percent_admission=$admission"
    if (-not $admission) { $blockers += 'E_30_PERCENT_RESERVE_ADMISSION_FAILED' }

    if (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath) { $blockers += 'PRODUCTION_WARM_VHDX_ALREADY_EXISTS' }
    if (Test-Path -LiteralPath $script:ExpectedTargetRuntimeRoot) { $blockers += 'TARGET_RUNTIME_ROOT_ALREADY_EXISTS' }

    $distros = @(Get-WslDistros)
    if (@($distros | Where-Object { [string]$_.name -eq $script:ExpectedTargetDistro }).Count -ne 0) { $blockers += 'TARGET_WSL_DISTRO_ALREADY_REGISTERED' }
    if (@($distros | Where-Object { [string]$_.name -eq $ToolingDistro }).Count -ne 1) { $blockers += 'TOOLING_WSL_DISTRO_NOT_UNIQUE' }
    else {
        try {
            $mounts = @(Get-WslMountInventory $ToolingDistro)
            if (@($mounts | Where-Object { [string]$_.target -eq "/mnt/wsl/$($script:ExpectedWarmMountName)" }).Count -ne 0) { $blockers += 'TARGET_WSL_MOUNT_NAME_COLLISION' }
        }
        catch { $blockers += 'WSL_MOUNT_INVENTORY_UNAVAILABLE' }
    }

    $blockers = @($blockers | Where-Object { $_ } | Sort-Object -Unique)
    $ready = [bool]($blockers.Count -eq 0)
    $decision = if ($ready) { 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_READY_FOR_OPERATOR_GO' } else { 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_BLOCKED' }
    $nextGate = if ($ready) { 'EXPLICIT_OPERATOR_GO_REQUIRED_BEFORE_PRODUCTION_PROVISIONING_APPLY' } else { 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_REMEDIATION' }

    Assert-RawConsumersStopped
    $finalHealth = Get-ProductionClickHouseHealth
    if (-not [bool]$finalHealth.ready -or [string]$finalHealth.version -ne $script:ProductionClickHouseVersion) { throw 'Production ClickHouse health/version changed during review.' }
    Assert-AcceptedProductionMount $finalHealth.container_id
    Assert-ExactMain 'final'
    if ((Get-FileSha256 $envPath) -ne $envShaBefore) { throw '.env changed during provisioning authority review.' }
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root reappeared.' }
    if (-not (Test-Path -LiteralPath $script:ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX disappeared.' }
    if ([int64](New-Object System.IO.FileInfo($script:ExpectedFRecoveryVhdx)).Length -ne $script:ExpectedFRecoveryBytes) { throw 'Retained F recovery VHDX length changed during review.' }
    if (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath) { throw 'Production Warm VHDX appeared during read-only review.' }

    $timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_cn_warm_provisioning_authority_review_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $receiptPath = Join-Path $evidenceDir 'production_cn_warm_provisioning_authority_review.json'
    $receipt = [ordered]@{
        receipt_version=$script:ReviewReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision=$decision
        next_gate=$nextGate
        review_only=$true
        read_only=$true
        mutation_performed=$false
        apply_authorized=$false
        provisioning_authorized=$false
        cn_warm_move_authorized=$false
        source_cleanup_authorized=$false
        accepted_design=[ordered]@{ engine_sha=$script:AcceptedDesignEngineSha; receipt_path=$acceptedDesign.path; receipt_sha256=$acceptedDesign.sha256; warm_candidate_manifest_sha256=$script:ExpectedWarmManifestSha256 }
        accepted_checksum=[ordered]@{ engine_sha=$script:AcceptedChecksumEngineSha; receipt_path=$acceptedChecksum.path; receipt_sha256=$acceptedChecksum.sha256; checksum_definition=$script:ChecksumDefinition; checksum_manifest_sha256=$acceptedChecksum.recomputed_manifest_sha256 }
        frozen_workload=[ordered]@{ candidate_count=$script:ExpectedWarmCandidateCount; migration_unit_count=$script:ExpectedWarmCandidateCount; rows=$script:ExpectedWarmRows; bytes=$script:ExpectedWarmBytes; units=@($script:ExpectedUnits) }
        live_source_identity=@($liveUnits)
        capacity=[ordered]@{ e_total_bytes=$capacity.total_bytes; e_free_bytes=$capacity.free_bytes; recommended_30_percent_reserve_bytes=$reserve; proposed_warm_vhdx_max_bytes=$script:ExpectedWarmVhdxMaxBytes; margin_after_proposed_max_bytes=$marginAfterMax; recommended_30_percent_admission=$admission }
        target_collision_review=[ordered]@{ warm_vhdx_path=$script:ExpectedWarmVhdxPath; warm_vhdx_exists=[bool](Test-Path -LiteralPath $script:ExpectedWarmVhdxPath); target_runtime_distro=$script:ExpectedTargetDistro; target_runtime_distro_registered=[bool](@($distros | Where-Object { [string]$_.name -eq $script:ExpectedTargetDistro }).Count -ne 0); target_runtime_root=$script:ExpectedTargetRuntimeRoot; target_runtime_root_exists=[bool](Test-Path -LiteralPath $script:ExpectedTargetRuntimeRoot); proposed_mount_name=$script:ExpectedWarmMountName }
        production_invariants=[ordered]@{ exact_clean_main=$true; raw_consumers_stopped=$true; production_clickhouse_ready=$true; production_clickhouse_version=$finalHealth.version; accepted_named_volume_mounted=$true; env_unchanged=$true; e_backup_root_absent=$true; f_recovery_preserved=$true; proposed_warm_vhdx_still_absent=$true }
        blockers=@($blockers)
        constraints=[ordered]@{ vhdx_mutation_authorized=$false; wsl_mutation_authorized=$false; docker_mutation_authorized=$false; clickhouse_mutation_authorized=$false; cross_runtime_transfer_authorized=$false; cn_replay_authorized=$false; us_bulk_authorized=$false; accepted_volume_mutation_authorized=$false; raw_delete_authorized=$false; f_recovery_mutation_authorized=$false }
    }
    Write-JsonFile $receipt $receiptPath
    Assert-ExactMain 'exit'

    Write-Host '===== PRODUCTION CN WARM PROVISIONING AUTHORITY REVIEW RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "accepted_design_receipt_sha256=$($acceptedDesign.sha256)"
    Write-Host "accepted_checksum_receipt_sha256=$($acceptedChecksum.sha256)"
    Write-Host "warm_candidate_manifest_sha256=$script:ExpectedWarmManifestSha256"
    Write-Host "checksum_manifest_sha256=$script:ExpectedChecksumManifestSha256"
    Write-Host "candidate_count=$script:ExpectedWarmCandidateCount"
    Write-Host "migration_unit_count=$script:ExpectedWarmCandidateCount"
    Write-Host "rows=$script:ExpectedWarmRows"
    Write-Host "bytes=$script:ExpectedWarmBytes"
    Write-Host "blocker_count=$($blockers.Count)"
    foreach ($blocker in $blockers) { Write-Host "blocker=$blocker" }
    Write-Host 'review_only=True'
    Write-Host 'read_only=True'
    Write-Host 'mutation_performed=False'
    Write-Host 'apply_authorized=False'
    Write-Host 'provisioning_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'source_cleanup_authorized=False'
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_DONE'
    if (-not $ready) { exit 4 }
    exit 0
}
catch {
    Write-Host "PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_FAILED: $($_.Exception.Message)"
    exit 2
}
finally { Pop-Location }
