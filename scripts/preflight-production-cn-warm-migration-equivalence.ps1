[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AcceptedEReclaimReceiptPath,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery',
    [string]$ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx',
    [ValidateRange(0, 50)]
    [double]$CopySafetyMarginPercent = 10,
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedEReclaimEngineSha = '9231b52d5e5bc14f455df353758e87829c7398ce'
$script:AcceptedEReclaimReceiptVersion = 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_APPLY_V1'
$script:ReceiptVersion = 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_V1'
$script:ArchitectureVersion = 'USER_ARCHITECTURE_SCENARIO_V1_ISSUE_481'
$script:ExpectedReclaimedBytes = [int64]853980217998
$script:AcceptedPlanningWarmPhysicalBytes = [int64]618860039242
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:AllowedToolingFiles = @(
    'scripts/preflight-production-cn-warm-migration-equivalence.ps1',
    'tests/test_production_cn_warm_migration_equivalence_contract.py',
    '.github/workflows/production-cn-warm-migration-equivalence-runtime.yml'
)

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

function Get-UserArchitectureTier([string]$TableName) {
    if ($TableName -eq 'cn_observed_event' -or $TableName.EndsWith('_event', [System.StringComparison]::OrdinalIgnoreCase)) {
        return 'WARM_EVENT_HISTORY'
    }
    if ($TableName.StartsWith('cn_goods_', [System.StringComparison]::OrdinalIgnoreCase)) {
        return 'WARM_GOODS_CATEGORY'
    }
    if ($TableName.StartsWith('cn_', [System.StringComparison]::OrdinalIgnoreCase)) {
        return 'HOT_CURRENT_SERVING_CONSERVATIVE'
    }
    return 'OUT_OF_SCOPE'
}

function Get-LegacyPlacementContract([string]$TableName) {
    switch ($TableName) {
        'cn_goods_item_current' { return 'HOT_REQUIRED' }
        'cn_goods_item_observation' { return 'WARM_CANDIDATE_REQUIRES_SUMMARY_REPLACEMENT' }
        'cn_observed_event' { return 'HOT_WITH_COMPACTABLE_BASELINE' }
        'cn_case_party_current' { return 'HOT_REQUIRED' }
        'cn_case_party_relation_history' { return 'WARM_CANDIDATE_PENDING_VERIFICATION' }
        default { return 'NO_FROZEN_LEGACY_CONTRACT' }
    }
}

function Get-RequiredCapacityBytes([int64]$PayloadBytes, [double]$MarginPercent) {
    if ($PayloadBytes -lt 0) { throw 'PayloadBytes must be non-negative.' }
    return [int64][math]::Ceiling([double]$PayloadBytes * (1.0 + ($MarginPercent / 100.0)))
}

function Get-RecommendedBudget([int64]$TotalBytes, [int64]$FreeBytes) {
    if ($TotalBytes -le 0 -or $FreeBytes -lt 0) { throw 'Invalid E drive capacity.' }
    $reserve = [int64][math]::Ceiling([double]$TotalBytes * 0.30)
    return [int64][math]::Max([int64]0, [int64]($FreeBytes - $reserve))
}

function Get-SourceCategory([string]$RelativePath) {
    $normalized = $RelativePath.Replace('\', '/')
    $name = [System.IO.Path]::GetFileName($normalized).ToLowerInvariant()
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($normalized).ToLowerInvariant()
    if ($normalized -eq 'app/main_core.py' -or $name.EndsWith('_api.py') -or $name.Contains('semantic_api')) { return 'serving_api' }
    if ($stem.Contains('storage') -or $stem.Contains('capacity') -or $stem.Contains('audit') -or $stem.Contains('checkpoint') -or $stem.Contains('preflight') -or $stem.Contains('acceptance') -or $stem.Contains('compaction')) { return 'audit_storage' }
    if ($normalized.StartsWith('app/cn/')) { return 'cn_runtime' }
    return 'runtime_other'
}

function Get-AccessMode([string]$TableName, [string]$Context) {
    $qualified = '(?:markorbit_facts\.)?' + [regex]::Escape($TableName)
    $read = [regex]::IsMatch($Context, '\b(?:FROM|JOIN)\s+' + $qualified + '\b', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    $write = [regex]::IsMatch($Context, '\bINSERT\s+INTO\s+' + $qualified + '\b', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($read -and $write) { return 'read_write' }
    if ($read) { return 'read' }
    if ($write) { return 'write' }
    return 'reference'
}

function Test-PhysicalPlacementCoupling([string]$Context) {
    return [regex]::IsMatch(
        $Context,
        '(?i)(/var/lib/clickhouse|\\var\\lib\\clickhouse|\bdisk_name\b|\bstorage_policy\b|system\.disks|system\.storage_policies|clickhouse[^\r\n]{0,80}\bpath\b)'
    )
}

function Get-TableConsumers([string[]]$TableNames, [string]$Root) {
    $result = @{}
    foreach ($table in $TableNames) { $result[$table] = @() }
    $appRoot = Join-Path $Root 'app'
    if (-not (Test-Path -LiteralPath $appRoot -PathType Container)) { throw 'app source root missing.' }
    foreach ($path in @(Get-ChildItem -LiteralPath $appRoot -Filter '*.py' -File -Recurse)) {
        if ($path.Length -gt 2MB) { continue }
        $relative = $path.FullName.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
        $category = Get-SourceCategory $relative
        $lines = @(Get-Content -LiteralPath $path.FullName -Encoding UTF8)
        for ($index = 0; $index -lt $lines.Count; $index++) {
            $line = [string]$lines[$index]
            foreach ($table in $TableNames) {
                if ($line.IndexOf($table, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
                $start = [math]::Max(0, $index - 5)
                $end = [math]::Min($lines.Count - 1, $index + 5)
                $context = (@($lines[$start..$end]) -join "`n")
                $accessMode = Get-AccessMode $table $context
                $physical = Test-PhysicalPlacementCoupling $context
                $runtimeCategory = $category -in @('serving_api', 'cn_runtime', 'runtime_other')
                $result[$table] += [pscustomobject]@{
                    path = $relative
                    line = [int]($index + 1)
                    category = $category
                    access_mode = $accessMode
                    direct_serving_read = [bool]($category -eq 'serving_api' -and $accessMode -in @('read', 'read_write'))
                    runtime_write = [bool]($runtimeCategory -and $accessMode -in @('write', 'read_write'))
                    physical_placement_coupling = [bool]$physical
                    runtime_physical_placement_blocker = [bool]($runtimeCategory -and $physical)
                    excerpt = $line.Trim().Substring(0, [math]::Min(320, $line.Trim().Length))
                }
            }
        }
    }
    return $result
}

function Import-FunctionDefinitions([string]$Path, [string[]]$Names, [string]$Label) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -ne 0) { throw "$Label helper source no longer parses." }
    $functions = $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Names -contains $node.Name
    }, $true)
    foreach ($name in $Names) {
        $matches = @($functions | Where-Object { $_.Name -eq $name })
        if ($matches.Count -ne 1) { throw "Expected exactly one $Label helper definition: $name" }
        $definitionText = [string]$matches[0].Extent.Text
        $pattern = '^(\s*function\s+)' + [regex]::Escape($name) + '(?=\s*(?:\(|\{))'
        $scriptScopedDefinition = [regex]::Replace($definitionText, $pattern, '${1}script:' + $name, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($scriptScopedDefinition -eq $definitionText) { throw "Unable to scope $Label helper definition: $name" }
        Invoke-Expression $scriptScopedDefinition
    }
}

function Import-AcceptedProductionHelpers {
    Import-FunctionDefinitions (Join-Path $PSScriptRoot 'preflight-production-rebalance-phase2-d-full-sha256.ps1') @(
        'Invoke-NativeText', 'Assert-ExactMain', 'Get-ProductionClickHouseHealth',
        'Assert-AcceptedProductionMount', 'Assert-RawConsumersStopped'
    ) 'Phase2D'
}

function Invoke-ContractFixture {
    if ((Get-UserArchitectureTier 'cn_goods_item_current') -ne 'WARM_GOODS_CATEGORY') { throw 'Goods Warm architecture contract failed.' }
    if ((Get-UserArchitectureTier 'cn_observed_event') -ne 'WARM_EVENT_HISTORY') { throw 'Observed event Warm architecture contract failed.' }
    if ((Get-UserArchitectureTier 'cn_goods_scope_event') -ne 'WARM_EVENT_HISTORY') { throw 'Event precedence contract failed.' }
    if ((Get-UserArchitectureTier 'cn_case_current') -ne 'HOT_CURRENT_SERVING_CONSERVATIVE') { throw 'Conservative Hot architecture contract failed.' }
    if ((Get-LegacyPlacementContract 'cn_goods_item_current') -ne 'HOT_REQUIRED') { throw 'Legacy contract mapping failed.' }
    if ((Get-RequiredCapacityBytes 1000 10) -ne 1100) { throw 'Copy safety arithmetic contract failed.' }
    if ((Get-RecommendedBudget 1000 700) -ne 400) { throw 'Recommended budget arithmetic contract failed.' }

    $fixture = Join-Path ([System.IO.Path]::GetTempPath()) ('markorbit_cn_warm_equivalence_' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'app') | Out-Null
        @"
def summary():
    return query('SELECT count() FROM markorbit_facts.cn_goods_item_current FINAL')
"@ | Set-Content -LiteralPath (Join-Path $fixture 'app\main_core.py') -Encoding UTF8
        $consumers = Get-TableConsumers @('cn_goods_item_current') $fixture
        $rows = @($consumers['cn_goods_item_current'])
        if (@($rows | Where-Object { $_.direct_serving_read }).Count -ne 1) { throw 'Direct serving read fixture was not detected.' }
        if (@($rows | Where-Object { $_.runtime_physical_placement_blocker }).Count -ne 0) { throw 'Logical SQL read was incorrectly treated as physical coupling.' }

        New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'app\cn') | Out-Null
        @"
def misplaced():
    disk_name = 'default'
    return query('SELECT count() FROM markorbit_facts.cn_goods_item_current')
"@ | Set-Content -LiteralPath (Join-Path $fixture 'app\cn\physical_reader.py') -Encoding UTF8
        $consumers2 = Get-TableConsumers @('cn_goods_item_current') $fixture
        if (@($consumers2['cn_goods_item_current'] | Where-Object { $_.runtime_physical_placement_blocker }).Count -lt 1) { throw 'Runtime physical placement blocker fixture was not detected.' }
    }
    finally {
        if (Test-Path -LiteralPath $fixture) { [System.IO.Directory]::Delete($fixture, $true) }
    }
    Write-Host 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_CONTRACT_DIRECT_INVOCATION_OK'
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'CN Warm equivalence preflight requires Administrator PowerShell.'
    }
}

function Assert-ToolingProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base', '--is-ancestor', $script:AcceptedEReclaimEngineSha, $ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted E reclaim SHA is not an ancestor of exact main.' }
    $diff = Invoke-NativeText 'git' @('diff', '--name-only', "$($script:AcceptedEReclaimEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\', '/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedToolingFiles })
    $missing = @($script:AllowedToolingFiles | Where-Object { $_ -notin $changed })
    Write-Host "accepted_e_reclaim_to_current_changed_file_count=$($changed.Count)"
    Write-Host "accepted_e_reclaim_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "accepted_e_reclaim_to_current_missing_tooling_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'CN Warm equivalence tooling changed outside the exact 3-file boundary.'
    }
}

function Resolve-AcceptedEReclaimReceipt {
    $path = [System.IO.Path]::GetFullPath($AcceptedEReclaimReceiptPath)
    $receipt = Read-JsonFile $path 'Accepted E reclaim receipt'
    if ([string]$receipt.receipt_version -ne $script:AcceptedEReclaimReceiptVersion) { throw 'Unexpected E reclaim receipt version.' }
    if ([string]$receipt.engine_sha -ne $script:AcceptedEReclaimEngineSha) { throw 'Accepted E reclaim receipt engine SHA changed.' }
    if ([string]$receipt.decision -ne 'PRODUCTION_E_BACKUP_GUARDED_RECLAIM_GO') { throw 'Accepted E reclaim decision changed.' }
    if ([string]$receipt.next_gate -ne 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT') { throw 'Accepted E reclaim next gate changed.' }
    if (-not [bool]$receipt.data_mutation_performed) { throw 'Accepted E reclaim receipt is not the successful Apply receipt.' }
    if ([int64]$receipt.deleted_file_count -ne 4 -or [int64]$receipt.deleted_bytes -ne $script:ExpectedReclaimedBytes) { throw 'Accepted E reclaim frozen delete boundary changed.' }
    if (-not [bool]$receipt.e_backup_root_removed -or -not [bool]$receipt.recommended_30_percent_admission) { throw 'Accepted E reclaim did not complete required E admission.' }
    if (-not [bool]$receipt.production_invariant_preserved -or -not [bool]$receipt.env_unchanged) { throw 'Accepted E reclaim production/env invariant changed.' }
    if ([int64]$receipt.warm_physical_required_bytes -ne $script:AcceptedPlanningWarmPhysicalBytes) { throw 'Accepted E reclaim planning Warm byte baseline changed.' }
    if ([bool]$receipt.constraints.cn_warm_move_authorized -or [bool]$receipt.constraints.vhdx_mutation_authorized -or [bool]$receipt.constraints.clickhouse_mutation_authorized) { throw 'Accepted E reclaim unexpectedly granted later mutation authority.' }
    return [ordered]@{ path=$path; sha256=(Get-FileSha256 $path); receipt=$receipt }
}

function Invoke-ClickHouseMetadataRows([string]$Sql, [string]$Label) {
    $normalized = ' ' + (($Sql -replace '\s+', ' ').Trim().ToUpperInvariant()) + ' '
    if (-not $normalized.TrimStart().StartsWith('SELECT ')) { throw "$Label is not a SELECT metadata query." }
    if ($normalized -notmatch ' FROM SYSTEM\.(TABLES|PARTS|DISKS) ') { throw "$Label may read ClickHouse system metadata only." }
    foreach ($token in @(' INSERT ', ' DELETE ', ' UPDATE ', ' DROP ', ' TRUNCATE ', ' OPTIMIZE ', ' MOVE PART ', ' ATTACH ', ' DETACH ', ' RENAME ')) {
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

function Get-CnMetadataManifest {
    $tableSql = @"
SELECT
    name AS table,
    engine,
    sorting_key,
    primary_key,
    partition_key,
    create_table_query
FROM system.tables
WHERE database = 'markorbit_facts'
  AND name LIKE 'cn_%'
ORDER BY name
"@
    $partsSql = @"
SELECT
    table,
    count() AS active_parts,
    coalesce(sum(rows), 0) AS rows_from_parts,
    coalesce(sum(bytes_on_disk), 0) AS bytes_on_disk,
    arraySort(groupUniqArray(disk_name)) AS disk_names
FROM system.parts
WHERE database = 'markorbit_facts'
  AND active
  AND table LIKE 'cn_%'
GROUP BY table
ORDER BY table
"@
    $tables = @(Invoke-ClickHouseMetadataRows $tableSql 'CN table metadata')
    $parts = @(Invoke-ClickHouseMetadataRows $partsSql 'CN active-part metadata')
    $partIndex = @{}
    foreach ($row in $parts) { $partIndex[[string]$row.table] = $row }
    $manifest = @()
    foreach ($row in $tables) {
        $name = [string]$row.table
        $part = $partIndex[$name]
        $activeParts = if ($null -eq $part) { [int64]0 } else { [int64]$part.active_parts }
        $rows = if ($null -eq $part) { [int64]0 } else { [int64]$part.rows_from_parts }
        $bytes = if ($null -eq $part) { [int64]0 } else { [int64]$part.bytes_on_disk }
        $diskNames = if ($null -eq $part) { @() } else { @($part.disk_names | ForEach-Object { [string]$_ } | Sort-Object -Unique) }
        $tier = Get-UserArchitectureTier $name
        $schemaCanonical = @(
            $name,
            [string]$row.engine,
            [string]$row.sorting_key,
            [string]$row.primary_key,
            [string]$row.partition_key,
            [string]$row.create_table_query
        ) -join "`n"
        $manifest += [pscustomobject]@{
            table=$name
            engine=[string]$row.engine
            sorting_key=[string]$row.sorting_key
            primary_key=[string]$row.primary_key
            partition_key=[string]$row.partition_key
            schema_fingerprint_sha256=(Get-StringSha256 $schemaCanonical)
            active_parts=$activeParts
            rows_from_parts=$rows
            bytes_on_disk=$bytes
            disk_names=@($diskNames)
            proposed_tier=$tier
            legacy_placement_contract=(Get-LegacyPlacementContract $name)
            operator_override_basis='ISSUE_481_USER_ARCHITECTURE_SCENARIO'
        }
    }
    return @($manifest | Sort-Object table)
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

try {
    Write-Host '===== PRODUCTION CN WARM MIGRATION EQUIVALENCE PREFLIGHT ====='
    Write-Host 'read_only=True'
    Write-Host 'apply_surface_present=False'
    Write-Host 'clickhouse_mutation_authorized=False'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host 'wsl_mutation_authorized=False'
    Write-Host 'docker_restart_authorized=False'
    Write-Host 'docker_prune_authorized=False'
    Write-Host 'accepted_volume_mutation_authorized=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'cn_replay_authorized=False'
    Write-Host 'us_bulk_authorized=False'

    if ($ContractOnly) {
        Invoke-ContractFixture
        exit 0
    }

    Import-AcceptedProductionHelpers
    Assert-Administrator
    if ((git branch --show-current).Trim() -ne 'main') { throw 'CN Warm equivalence preflight must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-ToolingProvenance
    $accepted = Resolve-AcceptedEReclaimReceipt
    Write-Host "accepted_e_reclaim_receipt=$($accepted.path)"
    Write-Host "accepted_e_reclaim_receipt_sha256=$($accepted.sha256)"

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env missing.' }
    $envShaBefore = Get-FileSha256 $envPath
    if (Test-Path -LiteralPath $EBackupRoot) { throw 'Superseded E backup root unexpectedly exists after accepted reclaim.' }
    if (-not (Test-Path -LiteralPath $ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX missing.' }
    $fInfo = New-Object System.IO.FileInfo($ExpectedFRecoveryVhdx)
    if ([int64]$fInfo.Length -ne $script:ExpectedFRecoveryBytes) { throw 'Retained F recovery VHDX length changed.' }
    if (($fInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Retained F recovery VHDX became a reparse point.' }
    Write-Host 'e_backup_root_absent=True'
    Write-Host "f_recovery_preserved_bytes=$([int64]$fInfo.Length)"

    Assert-ExactMain 'metadata_before'
    Assert-RawConsumersStopped
    $productionBefore = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_metadata_before=$([bool]$productionBefore.ready)"
    if (-not [bool]$productionBefore.ready) { throw 'Production ClickHouse must be healthy before metadata audit.' }
    Assert-AcceptedProductionMount $productionBefore.container_id

    $manifest = @(Get-CnMetadataManifest)
    if ($manifest.Count -eq 0) { throw 'No deployed CN tables found.' }
    $candidates = @($manifest | Where-Object { $_.proposed_tier -in @('WARM_GOODS_CATEGORY', 'WARM_EVENT_HISTORY') })
    $activeCandidates = @($candidates | Where-Object { [int64]$_.active_parts -gt 0 -or [int64]$_.bytes_on_disk -gt 0 })
    if ($candidates.Count -eq 0 -or $activeCandidates.Count -eq 0) { throw 'Operator architecture produced no active Warm candidates.' }

    $metadataBlockers = @()
    foreach ($candidate in $activeCandidates) {
        if (-not ([string]$candidate.engine).Contains('MergeTree')) { $metadataBlockers += "NON_MERGETREE:$($candidate.table)" }
        if (@($candidate.disk_names).Count -ne 1) { $metadataBlockers += "AMBIGUOUS_SOURCE_DISK:$($candidate.table)" }
    }

    $candidateNames = @($candidates | ForEach-Object { [string]$_.table })
    $consumerIndex = Get-TableConsumers $candidateNames $repoRoot
    $consumerReports = @()
    $runtimePhysicalBlockers = @()
    $directServingTables = @()
    $writerTables = @()
    foreach ($candidate in $candidates) {
        $name = [string]$candidate.table
        $consumers = @($consumerIndex[$name])
        $direct = @($consumers | Where-Object { $_.direct_serving_read })
        $writers = @($consumers | Where-Object { $_.runtime_write })
        $physical = @($consumers | Where-Object { $_.runtime_physical_placement_blocker })
        if ($direct.Count -gt 0) { $directServingTables += $name }
        if ($writers.Count -gt 0) { $writerTables += $name }
        foreach ($row in $physical) { $runtimePhysicalBlockers += "$name@$($row.path):$($row.line)" }
        $consumerReports += [pscustomobject]@{
            table=$name
            consumer_count=[int64]$consumers.Count
            direct_serving_read_count=[int64]$direct.Count
            runtime_write_count=[int64]$writers.Count
            runtime_physical_placement_blocker_count=[int64]$physical.Count
            performance_sensitive_post_move_acceptance_required=[bool]($direct.Count -gt 0)
            consumers=@($consumers)
        }
    }

    $warmBytes = [int64](($activeCandidates | Measure-Object -Property bytes_on_disk -Sum).Sum)
    $warmRows = [int64](($activeCandidates | Measure-Object -Property rows_from_parts -Sum).Sum)
    $requiredPhysical = Get-RequiredCapacityBytes $warmBytes $CopySafetyMarginPercent
    $drive = New-Object System.IO.DriveInfo('E')
    $eTotal = [int64]$drive.TotalSize
    $eFree = [int64]$drive.AvailableFreeSpace
    $recommendedBudget = Get-RecommendedBudget $eTotal $eFree
    $recommendedMargin = [int64]($recommendedBudget - $requiredPhysical)
    $recommendedAdmission = [bool]($recommendedMargin -ge 0)
    $planningDelta = [int64]($requiredPhysical - $script:AcceptedPlanningWarmPhysicalBytes)

    Write-Host "cn_table_count=$($manifest.Count)"
    Write-Host "warm_candidate_table_count=$($candidates.Count)"
    Write-Host "warm_active_candidate_table_count=$($activeCandidates.Count)"
    Write-Host "warm_candidate_rows=$warmRows"
    Write-Host "warm_candidate_bytes=$warmBytes"
    Write-Host "warm_required_physical_bytes_with_safety=$requiredPhysical"
    Write-Host "direct_serving_warm_table_count=$(@($directServingTables | Sort-Object -Unique).Count)"
    Write-Host "runtime_writer_warm_table_count=$(@($writerTables | Sort-Object -Unique).Count)"
    Write-Host "runtime_physical_placement_blocker_count=$($runtimePhysicalBlockers.Count)"
    Write-Host "metadata_blocker_count=$($metadataBlockers.Count)"
    Write-Host "e_total_bytes=$eTotal"
    Write-Host "e_free_bytes=$eFree"
    Write-Host "e_recommended_budget_bytes=$recommendedBudget"
    Write-Host "e_recommended_margin_after_warm_copy_bytes=$recommendedMargin"
    Write-Host "recommended_30_percent_admission=$recommendedAdmission"

    $strategyReady = [bool](
        $metadataBlockers.Count -eq 0 -and
        $runtimePhysicalBlockers.Count -eq 0 -and
        $recommendedAdmission
    )
    $decision = if ($strategyReady) { 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_READY' } else { 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_BLOCKED' }
    $nextGate = if ($strategyReady) { 'PRODUCTION_CN_WARM_EXT4_PROVISIONING_PREFLIGHT' } else { 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_REVIEW' }

    Assert-RawConsumersStopped
    $productionAfter = Get-ProductionClickHouseHealth
    Write-Host "production_clickhouse_ready_final=$([bool]$productionAfter.ready)"
    if (-not [bool]$productionAfter.ready) { throw 'Production ClickHouse must remain healthy after metadata audit.' }
    Assert-AcceptedProductionMount $productionAfter.container_id
    Assert-ExactMain 'final'
    $envShaAfter = Get-FileSha256 $envPath
    if ($envShaAfter -ne $envShaBefore) { throw '.env changed during CN Warm equivalence preflight.' }
    if (Test-Path -LiteralPath $EBackupRoot) { throw 'Superseded E backup root reappeared.' }
    if (-not (Test-Path -LiteralPath $ExpectedFRecoveryVhdx -PathType Leaf)) { throw 'Retained F recovery VHDX disappeared.' }

    $timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "production_cn_warm_migration_equivalence_preflight_$timestamp")
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $candidateManifestSha = Get-CandidateManifestHash $candidates
    $receipt = [ordered]@{
        receipt_version=$script:ReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        architecture_version=$script:ArchitectureVersion
        operator_override_basis='ISSUE_481_USER_ARCHITECTURE_SCENARIO'
        decision=$decision
        next_gate=$nextGate
        read_only=$true
        data_mutation_performed=$false
        migration_equivalence_strategy_ready=$strategyReady
        migration_completed=$false
        accepted_e_reclaim_receipt_path=$accepted.path
        accepted_e_reclaim_receipt_sha256=$accepted.sha256
        env_sha256=$envShaBefore
        e_backup_root_absent=$true
        f_recovery_preserved=$true
        f_recovery_bytes=[int64]$fInfo.Length
        cn_table_count=[int64]$manifest.Count
        cn_tables=@($manifest)
        warm_candidate_table_count=[int64]$candidates.Count
        warm_active_candidate_table_count=[int64]$activeCandidates.Count
        warm_candidate_rows=$warmRows
        warm_candidate_bytes=$warmBytes
        warm_candidate_manifest_sha256=$candidateManifestSha
        warm_candidates=@($candidates | ForEach-Object {
            $sourceDisk = if (@($_.disk_names).Count -eq 1) { [string]@($_.disk_names)[0] } else { $null }
            [ordered]@{
                table=$_.table
                proposed_tier=$_.proposed_tier
                legacy_placement_contract=$_.legacy_placement_contract
                schema_fingerprint_sha256=$_.schema_fingerprint_sha256
                active_parts=$_.active_parts
                rows_from_parts=$_.rows_from_parts
                bytes_on_disk=$_.bytes_on_disk
                source_disk=$sourceDisk
                disk_names=@($_.disk_names)
                rollback_target_source_disk=$sourceDisk
            }
        })
        consumer_equivalence=[ordered]@{
            static_source_scan_read_only=$true
            reports=@($consumerReports)
            direct_serving_tables=@($directServingTables | Sort-Object -Unique)
            runtime_writer_tables=@($writerTables | Sort-Object -Unique)
            runtime_physical_placement_blockers=@($runtimePhysicalBlockers)
            logical_table_identity_preserved_by_strategy=$true
            direct_logical_reads_are_not_blockers_by_themselves=$true
            post_move_query_and_latency_acceptance_required=[bool]($directServingTables.Count -gt 0)
        }
        metadata_blockers=@($metadataBlockers)
        capacity=[ordered]@{
            copy_safety_margin_percent=$CopySafetyMarginPercent
            accepted_planning_warm_physical_bytes=$script:AcceptedPlanningWarmPhysicalBytes
            fresh_warm_required_physical_bytes=$requiredPhysical
            planning_to_fresh_required_delta_bytes=$planningDelta
            e_total_bytes=$eTotal
            e_free_bytes=$eFree
            recommended_30_percent_reserve_bytes=[int64][math]::Ceiling([double]$eTotal * 0.30)
            recommended_allocation_budget_bytes=$recommendedBudget
            recommended_margin_after_warm_copy_bytes=$recommendedMargin
            recommended_30_percent_admission=$recommendedAdmission
        }
        migration_strategy=[ordered]@{
            preserve_database_and_table_names=$true
            preserve_schema_fingerprints=$true
            preserve_query_semantics=$true
            source_disk_frozen_per_nonempty_candidate=$true
            rollback_to_frozen_source_disk_required=$true
            post_move_metadata_equivalence_required=$true
            post_move_row_count_equivalence_required=$true
            post_move_target_disk_residency_required=$true
            post_move_summary_and_case_api_acceptance_required=$true
            post_move_writer_placement_acceptance_required=$true
            post_move_latency_acceptance_required=$true
            source_policy_cleanup_authorized=$false
        }
        production_invariant_preserved=$true
        env_unchanged=$true
        constraints=[ordered]@{
            clickhouse_mutation_authorized=$false
            cn_warm_move_authorized=$false
            vhdx_mutation_authorized=$false
            wsl_mutation_authorized=$false
            docker_restart_authorized=$false
            docker_prune_authorized=$false
            accepted_volume_mutation_authorized=$false
            raw_delete_authorized=$false
            cn_replay_authorized=$false
            us_bulk_authorized=$false
        }
    }
    $receiptPath = Join-Path $evidenceDir 'production_cn_warm_migration_equivalence_preflight.json'
    $receipt | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    Assert-ExactMain 'exit'

    Write-Host '===== PRODUCTION CN WARM MIGRATION EQUIVALENCE PREFLIGHT RESULT ====='
    Write-Host "decision=$decision"
    Write-Host "next_gate=$nextGate"
    Write-Host "migration_equivalence_strategy_ready=$strategyReady"
    Write-Host 'migration_completed=False'
    Write-Host 'post_move_query_and_latency_acceptance_required=True'
    Write-Host 'cn_warm_move_authorized=False'
    Write-Host 'vhdx_mutation_authorized=False'
    Write-Host "warm_candidate_manifest_sha256=$candidateManifestSha"
    Write-Host "receipt_path=$receiptPath"
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_DONE'
    if (-not $strategyReady) { exit 4 }
    exit 0
}
catch {
    Write-Host "PRODUCTION_CN_WARM_MIGRATION_EQUIVALENCE_PREFLIGHT_FAILED: $($_.Exception.Message)"
    exit 2
}
finally {
    Pop-Location
}
