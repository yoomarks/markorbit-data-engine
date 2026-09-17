[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ExpectedMainSha,
    [Parameter(Mandatory=$true)][string]$ConnectivityReceiptPath,
    [string]$EvidenceRoot='reports',
    [switch]$ContractOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repoRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot

$script:ReceiptVersion='US_ASSIGNMENT_BOUNDED_COPY_REVIEW_V1'
$script:PlanVersion='US_ASSIGNMENT_BOUNDED_COPY_PLAN_V1'
$script:Database='markorbit_facts'
$script:TargetDistro='MarkOrbit-ClickHouse'
$script:TargetHost='127.0.0.1'
$script:TargetPort='29000'
$script:TargetDisk='hot_us'
$script:TargetPolicy='hot_us_only'
$script:AcceptedConnectivityReceiptSha256='9f8ee8a5bf0980503a1ebd3ab24d0665f9d828aa866e2a8504ef4584a987da8a'
$script:AcceptedSourceIdentitySha256='8f7d9f59bbc5bc77671c27d02e9c9bcacdacf01485cb6db2f3eb427e80438d01'
$script:ChecksumDefinition='NULL_SAFE_JSON_TUPLE_CITYHASH64_V2'
$script:RowHashExpression='cityHash64(toJSONString(tuple(*)))'
$script:TransferStrategy='TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE'
$script:CopyPrimitive='TARGET_WSL_DUAL_CLICKHOUSE_CLIENT_NATIVE_PIPE_V1'
$script:AssignmentTables=@(
    'us_assignment_record_history',
    'us_assignment_assignor_history',
    'us_assignment_assignee_history',
    'us_assignment_property_history'
)
$script:AllSecondaryTables=@(
    'us_assignment_assignee_history','us_assignment_assignor_history',
    'us_assignment_property_history','us_assignment_record_history',
    'us_ttab_docket_history','us_ttab_party_history',
    'us_ttab_proceeding_history','us_ttab_property_history'
)

function Invoke-NativeText {
    param([string]$Command,[string[]]$Arguments,[switch]$AllowFailure)
    $previous=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        $output=@(& $Command @Arguments 2>&1)
        $exitCode=$LASTEXITCODE
    } finally { $ErrorActionPreference=$previous }
    $lines=@($output|ForEach-Object { $_.ToString() })
    if(-not $AllowFailure -and $exitCode -ne 0){ throw "$Command failed: $($lines -join [Environment]::NewLine)" }
    return [ordered]@{exit_code=$exitCode;lines=$lines}
}
function Get-StringSha256([string]$Text){
    $sha=[System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Get-FileSha256([string]$Path){
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){ throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Read-JsonFile([string]$Path,[string]$Label){
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label is invalid JSON: $Path" }
}
function Write-JsonAtomic([object]$Value,[string]$Path){
    $dir=Split-Path -Parent $Path
    if(-not (Test-Path $dir)){ New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $tmp="$Path.tmp.$PID"
    $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($tmp,($Value|ConvertTo-Json -Depth 20),$utf8)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Assert-ExactMain([string]$Phase){
    git fetch origin main | Out-Host
    $head=(git rev-parse HEAD).Trim(); $origin=(git rev-parse origin/main).Trim()
    if($head -ne $ExpectedMainSha -or $origin -ne $ExpectedMainSha){ throw "Exact main drift during ${Phase}: head=$head origin=$origin expected=$ExpectedMainSha" }
    if(git status --porcelain){ throw "Working tree must be clean during $Phase." }
}
function Convert-JsonLines([string[]]$Lines,[string]$Label){
    $rows=@()
    foreach($line in @($Lines|Where-Object { $_.Trim() })){
        try { $rows += ($line|ConvertFrom-Json) }
        catch { throw "$Label returned invalid JSONEachRow: $line" }
    }
    return @($rows)
}
function Assert-ReadOnlySelect([string]$Sql,[string]$Label){
    $normalized=(' '+($Sql -replace '\s+',' ').Trim().ToUpperInvariant()+' ')
    if(-not $normalized.TrimStart().StartsWith('SELECT ')){ throw "$Label must be SELECT-only." }
    foreach($token in @(' INSERT ',' UPDATE ',' DELETE ',' CREATE ',' ALTER ',' DROP ',' TRUNCATE ',' OPTIMIZE ',' MOVE ',' ATTACH ',' DETACH ',' RENAME ',' KILL ')){
        if($normalized.Contains($token)){ throw "$Label contains forbidden mutation token: $token" }
    }
}
function Invoke-SourceRows([string]$Sql,[string]$Label){
    Assert-ReadOnlySelect $Sql $Label
    $probe=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if($probe.exit_code -ne 0){ throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Assert-TargetRuntimeReady {
    $running=Invoke-NativeText 'wsl.exe' @('--list','--running','--quiet') -AllowFailure
    if($running.exit_code -ne 0 -or -not (@($running.lines|ForEach-Object {$_.Trim()}) -contains $script:TargetDistro)){ throw 'Target WSL runtime is not running.' }
}
function Invoke-TargetRows([string]$Sql,[string]$Label){
    Assert-ReadOnlySelect $Sql $Label
    Assert-TargetRuntimeReady
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if($probe.exit_code -ne 0){ throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Get-SourceIdentity {
    $quoted=@($script:AllSecondaryTables|ForEach-Object { "'$_'" }) -join ','
    $tables=@(Invoke-SourceRows "SELECT name, engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database='$($script:Database)' AND name IN ($quoted) ORDER BY name" 'Source secondary schemas')
    $parts=@(Invoke-SourceRows "SELECT table, partition_id, name, rows, bytes_on_disk, disk_name, hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files FROM system.parts WHERE database='$($script:Database)' AND active AND table IN ($quoted) ORDER BY table, partition_id, name" 'Source secondary parts')
    if($tables.Count -ne 8 -or $parts.Count -lt 8){ throw 'Secondary source inventory drifted.' }
    $lines=@()
    foreach($row in @($tables)){ $lines += "T|$($row.name)|$($row.engine)|$($row.sorting_key)|$($row.primary_key)|$($row.partition_key)|$($row.create_table_query)" }
    foreach($row in @($parts)){ $lines += "P|$($row.table)|$($row.partition_id)|$($row.name)|$($row.rows)|$($row.bytes_on_disk)|$($row.disk_name)|$($row.hash_of_all_files)|$($row.hash_of_uncompressed_files)|$($row.uncompressed_hash_of_compressed_files)" }
    return [ordered]@{ sha256=Get-StringSha256 ($lines -join "`n"); tables=$tables; parts=$parts; rows=[int64](($parts|Measure-Object rows -Sum).Sum); bytes=[int64](($parts|Measure-Object bytes_on_disk -Sum).Sum) }
}
function Get-SchemaFingerprint([object]$TableRow,[object[]]$Columns){
    $lines=@(
        "TABLE|$($TableRow.name)|$($TableRow.engine)|$($TableRow.sorting_key)|$($TableRow.primary_key)|$($TableRow.partition_key)"
    )
    foreach($col in @($Columns)){ $lines += "COL|$($col.position)|$($col.name)|$($col.type)|$($col.default_kind)|$($col.default_expression)" }
    return Get-StringSha256 ($lines -join "`n")
}
function Get-PartFingerprint([object[]]$Parts){
    $lines=@()
    foreach($row in @($Parts)){
        $lines += "$($row.partition_id)|$($row.name)|$($row.rows)|$($row.bytes_on_disk)|$($row.disk_name)|$($row.hash_of_all_files)|$($row.hash_of_uncompressed_files)|$($row.uncompressed_hash_of_compressed_files)"
    }
    return Get-StringSha256 ($lines -join "`n")
}
function Get-LogicalChecksum([string]$Table){
    if($Table -notmatch '^us_assignment_[a-z0-9_]+$'){ throw "Unsafe Assignment table: $Table" }
    $sql="SELECT count() AS rows, sum($($script:RowHashExpression)) AS checksum_sum, groupBitXor($($script:RowHashExpression)) AS checksum_xor FROM $($script:Database).$Table SETTINGS max_threads = 4, max_execution_time = 900, max_memory_usage = 4294967296, use_uncompressed_cache = 0"
    $rows=@(Invoke-SourceRows $sql "Logical checksum $Table")
    if($rows.Count -ne 1){ throw "Logical checksum returned unexpected row count for $Table" }
    foreach($field in @('rows','checksum_sum','checksum_xor')){ if([string]$rows[0].$field -notmatch '^\d+$'){ throw "Logical checksum field $field is not unsigned decimal for $Table" } }
    return [ordered]@{definition=$script:ChecksumDefinition;row_hash_expression=$script:RowHashExpression;rows=[string]$rows[0].rows;checksum_sum=[string]$rows[0].checksum_sum;checksum_xor=[string]$rows[0].checksum_xor}
}
function Get-TargetBaseline {
    $quoted=@($script:AllSecondaryTables|ForEach-Object { "'$_'" }) -join ','
    $tables=@(Invoke-TargetRows "SELECT name, engine, sorting_key, primary_key, partition_key, storage_policy FROM system.tables WHERE database='$($script:Database)' AND name IN ($quoted) ORDER BY name" 'Target secondary schemas')
    if($tables.Count -ne 8){ throw "Expected 8 target secondary tables; got $($tables.Count)." }
    if(@($tables|Where-Object { [string]$_.storage_policy -ne $script:TargetPolicy }).Count -ne 0){ throw 'Target storage policy drifted.' }
    $parts=@(Invoke-TargetRows "SELECT count() AS active_parts, coalesce(sum(rows),0) AS rows, coalesce(sum(bytes_on_disk),0) AS bytes FROM system.parts WHERE database='$($script:Database)' AND active AND table IN ($quoted)" 'Target secondary active parts')
    if($parts.Count -ne 1 -or [int64]$parts[0].active_parts -ne 0 -or [int64]$parts[0].rows -ne 0 -or [int64]$parts[0].bytes -ne 0){ throw 'Target secondary tables are not empty before Assignment review.' }
    $disk=@(Invoke-TargetRows "SELECT name, total_space, free_space FROM system.disks WHERE name='$($script:TargetDisk)'" 'Target hot_us disk')
    if($disk.Count -ne 1){ throw 'hot_us disk missing.' }
    return [ordered]@{tables=$tables;active_parts=0;rows=0;bytes=0;total_space=[int64]$disk[0].total_space;free_space=[int64]$disk[0].free_space}
}
function Assert-ConnectivityReceipt([object]$Receipt,[string]$Sha){
    if($Sha -ne $script:AcceptedConnectivityReceiptSha256){ throw "Connectivity receipt SHA drifted: $Sha" }
    if([string]$Receipt.decision -ne 'US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_READY'){ throw 'Connectivity decision is not accepted READY.' }
    if([string]$Receipt.next_gate -ne 'BOUNDED_ASSIGNMENT_TABLE_COPY_REVIEW'){ throw 'Connectivity next gate drifted.' }
    if([bool]$Receipt.copy_authorized){ throw 'Connectivity receipt unexpectedly authorized copy.' }
    if([bool]$Receipt.mutation_performed){ throw 'Connectivity receipt reports mutation.' }
    if([string]$Receipt.source_identity.sha256 -ne $script:AcceptedSourceIdentitySha256){ throw 'Connectivity source identity SHA drifted.' }
    if([int64]$Receipt.target_state.table_count -ne 8 -or [int64]$Receipt.target_state.active_parts -ne 0){ throw 'Connectivity target baseline drifted.' }
    if([bool]$Receipt.credential_contract.values_in_receipt -or [bool]$Receipt.credential_contract.values_printed){ throw 'Connectivity credential safety contract drifted.' }
}
function Get-AssignmentTablePlan([string]$Table,[object]$Source,[object]$Target){
    $sourceTable=@($Source.tables|Where-Object { [string]$_.name -eq $Table })
    $targetTable=@($Target.tables|Where-Object { [string]$_.name -eq $Table })
    if($sourceTable.Count -ne 1 -or $targetTable.Count -ne 1){ throw "Schema row missing for $Table" }
    foreach($field in @('engine','sorting_key','primary_key','partition_key')){
        if([string]$sourceTable[0].$field -ne [string]$targetTable[0].$field){ throw "Source/target $field mismatch for $Table" }
    }
    $sourceCols=@(Invoke-SourceRows "SELECT position, name, type, default_kind, default_expression FROM system.columns WHERE database='$($script:Database)' AND table='$Table' ORDER BY position" "Source columns $Table")
    $targetCols=@(Invoke-TargetRows "SELECT position, name, type, default_kind, default_expression FROM system.columns WHERE database='$($script:Database)' AND table='$Table' ORDER BY position" "Target columns $Table")
    $sourceColLines=@($sourceCols|ForEach-Object { "$($_.position)|$($_.name)|$($_.type)|$($_.default_kind)|$($_.default_expression)" })
    $targetColLines=@($targetCols|ForEach-Object { "$($_.position)|$($_.name)|$($_.type)|$($_.default_kind)|$($_.default_expression)" })
    if(($sourceColLines -join "`n") -ne ($targetColLines -join "`n")){ throw "Source/target column contract mismatch for $Table" }
    $parts=@($Source.parts|Where-Object { [string]$_.table -eq $Table })
    if($parts.Count -lt 1){ throw "Source active parts missing for $Table" }
    $rows=[int64](($parts|Measure-Object rows -Sum).Sum); $bytes=[int64](($parts|Measure-Object bytes_on_disk -Sum).Sum)
    $checksum=Get-LogicalChecksum $Table
    if([int64]$checksum.rows -ne $rows){ throw "Logical checksum row count mismatch for $Table" }
    return [ordered]@{
        table=$Table; source_rows=$rows; source_bytes=$bytes; source_active_parts=$parts.Count
        source_schema_sha256=Get-SchemaFingerprint $sourceTable[0] $sourceCols
        source_part_identity_sha256=Get-PartFingerprint $parts
        logical_checksum=$checksum
        target_policy=$script:TargetPolicy; target_empty_before_copy=$true
    }
}
function Invoke-ContractFixture {
    if(($script:AssignmentTables -join '|') -ne 'us_assignment_record_history|us_assignment_assignor_history|us_assignment_assignee_history|us_assignment_property_history'){ throw 'Assignment copy order drifted.' }
    if($script:ChecksumDefinition -ne 'NULL_SAFE_JSON_TUPLE_CITYHASH64_V2'){ throw 'Checksum definition drifted.' }
    if($script:RowHashExpression -ne 'cityHash64(toJSONString(tuple(*)))'){ throw 'NULL-safe row hash expression drifted.' }
    if($script:TransferStrategy -ne 'TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE'){ throw 'Accepted transfer strategy drifted.' }
    if($script:CopyPrimitive -ne 'TARGET_WSL_DUAL_CLICKHOUSE_CLIENT_NATIVE_PIPE_V1'){ throw 'Copy primitive drifted.' }
    Write-Host 'US_ASSIGNMENT_BOUNDED_COPY_REVIEW_CONTRACT_PASS'
    Write-Host "checksum_definition=$($script:ChecksumDefinition)"
    Write-Host "copy_primitive=$($script:CopyPrimitive)"
    Write-Host 'copy_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host 'copy_primitive_execution_performed=False'
    Write-Host 'credential_values_persisted=False'
}

Write-Host '===== US ASSIGNMENT BOUNDED COPY REVIEW ====='
Write-Host 'review_only=True'
Write-Host 'copy_authorized=False'
Write-Host 'mutation_performed=False'
Write-Host 'copy_primitive_execution_performed=False'
Write-Host 'ttab_copy_authorized=False'
Write-Host 'serving_cutover_authorized=False'
Write-Host 'source_cleanup_authorized=False'
Write-Host 'credential_values_persisted=False'
if($ContractOnly){ Invoke-ContractFixture; Pop-Location; exit 0 }

try {
    Assert-ExactMain 'review-entry'
    $connectivitySha=Get-FileSha256 $ConnectivityReceiptPath
    $connectivity=Read-JsonFile $ConnectivityReceiptPath 'Connectivity receipt'
    Assert-ConnectivityReceipt $connectivity $connectivitySha
    $source=Get-SourceIdentity
    if([string]$source.sha256 -ne $script:AcceptedSourceIdentitySha256){ throw "Current source identity drifted: $($source.sha256)" }
    $target=Get-TargetBaseline
    $tablePlans=@()
    for($i=0;$i -lt $script:AssignmentTables.Count;$i++){
        $table=$script:AssignmentTables[$i]
        $evidence=Get-AssignmentTablePlan $table $source $target
        $tablePlans += [ordered]@{
            migration_order=$i+1
            table=$table
            source_rows=[int64]$evidence.source_rows
            source_bytes=[int64]$evidence.source_bytes
            source_active_parts=[int64]$evidence.source_active_parts
            source_schema_sha256=[string]$evidence.source_schema_sha256
            source_part_identity_sha256=[string]$evidence.source_part_identity_sha256
            logical_checksum=$evidence.logical_checksum
            target_policy=$script:TargetPolicy
            target_empty_before_copy=$true
            source_select_sql="SELECT * FROM $($script:Database).$table FORMAT Native"
            target_insert_sql="INSERT INTO $($script:Database).$table FORMAT Native"
        }
    }
    $byteOrder=@($tablePlans|Sort-Object @{Expression={[int64]$_.source_bytes}}, @{Expression={[string]$_.table}}|ForEach-Object {$_.table})
    if(($byteOrder -join '|') -ne ($script:AssignmentTables -join '|')){ throw "Assignment source-byte order drifted: $($byteOrder -join ',')" }
    $assignmentBytes=[int64](($tablePlans|Measure-Object source_bytes -Sum).Sum)
    $floor=[int64][Math]::Ceiling([double]$target.total_space*0.30)
    $projected=[int64]$target.free_space-$assignmentBytes
    if($projected -lt $floor){ throw 'Assignment equal-byte copy would violate 30% hot_us reserve.' }
    $plan=[ordered]@{
        plan_version=$script:PlanVersion
        main_sha=$ExpectedMainSha
        scope='ASSIGNMENT_ONLY'
        accepted_connectivity=[ordered]@{receipt_sha256=$connectivitySha;source_identity_sha256=$script:AcceptedSourceIdentitySha256}
        checksum_contract=[ordered]@{definition=$script:ChecksumDefinition;row_hash_expression=$script:RowHashExpression;null_safe=$true}
        transfer_strategy=$script:TransferStrategy
        copy_primitive=[ordered]@{
            name=$script:CopyPrimitive
            execution_runtime='TARGET_WSL'
            source_endpoint_derivation='TARGET_WSL_DEFAULT_GATEWAY_AT_EXECUTION'
            source_native_port=9000
            source_credentials_transport='WSLENV_RUNTIME_ONLY'
            source_stream_format='Native'
            target_stream_format='Native'
            stream_contract='SOURCE_CLICKHOUSE_CLIENT_STDOUT_TO_TARGET_CLICKHOUSE_CLIENT_STDIN'
            filesystem_copy_allowed=$false
            credential_values_in_plan=$false
        }
        capacity_guard=[ordered]@{hot_us_total_space=[int64]$target.total_space;hot_us_free_space=[int64]$target.free_space;assignment_source_bytes=$assignmentBytes;projected_free_after_equal_byte_copy=$projected;recommended_30pct_floor=$floor;recommended_30pct_fits=$true}
        tables=@($tablePlans)
        future_authority_token_format='GO #<COPY_EXECUTOR_ISSUE> US Assignment bounded table copy <plan_sha256>'
    }
    $plan.pre_copy_requirements=@(
        'exact_main_and_exact_plan_sha','fresh_connectivity_preflight_ready','source_identity_and_logical_checksum_stable',
        'target_table_empty_and_hot_us_only','hot_us_30pct_reserve_revalidated','credentials_runtime_only'
    )
    $plan.post_copy_acceptance=@(
        'source_and_target_row_count_equal','source_and_target_null_safe_logical_checksum_equal',
        'target_active_parts_only_on_hot_us','source_identity_unchanged','hot_us_30pct_reserve_revalidated',
        'durable_per_table_receipt_required'
    )
    $plan.failure_contract=[ordered]@{
        halt_on_first_table_failure=$true
        continue_to_later_tables=$false
        auto_truncate_allowed=$false
        auto_drop_allowed=$false
        ordinary_retry_allowed=$false
        source_remains_authoritative=$true
        partial_target_state_must_be_preserved=$true
        remediation_review_required=$true
    }
    $plan.constraints=[ordered]@{
        review_only=$true;copy_authorized=$false;future_copy_authorized=$false;copy_primitive_execution_performed=$false;ttab_copy_authorized=$false;serving_cutover_authorized=$false
        source_mutation_authorized=$false;source_cleanup_authorized=$false;replay_authorized=$false
        wsl_lifecycle_mutation_authorized=$false;docker_lifecycle_mutation_authorized=$false;credential_values_persisted=$false
    }

    $canonical=$plan|ConvertTo-Json -Depth 20 -Compress
    $planSha=Get-StringSha256 $canonical
    $sourceFinal=Get-SourceIdentity
    if([string]$sourceFinal.sha256 -ne [string]$source.sha256){ throw 'Source identity changed during review.' }
    $targetFinal=Get-TargetBaseline
    if([int64]$targetFinal.active_parts -ne 0){ throw 'Target state changed during review.' }
    Assert-ExactMain 'pre-receipt'

    $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){$EvidenceRoot}else{Join-Path $repoRoot $EvidenceRoot}
    $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir=Join-Path $root "us_assignment_bounded_copy_review_$stamp"
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
    $planPath=Join-Path $evidenceDir 'copy_plan.json'
    Write-JsonAtomic $plan $planPath
    $writtenPlanSha=Get-StringSha256 ((Read-JsonFile $planPath 'Written copy plan')|ConvertTo-Json -Depth 20 -Compress)
    if($writtenPlanSha -ne $planSha){ throw 'Canonical copy plan SHA changed after write/read round trip.' }

    $receipt=[ordered]@{
        receipt_version=$script:ReceiptVersion
        generated_at=(Get-Date).ToUniversalTime().ToString('o')
        main_sha=$ExpectedMainSha
        decision='US_ASSIGNMENT_BOUNDED_COPY_REVIEW_READY'
        next_gate='BOUNDED_ASSIGNMENT_TABLE_COPY_EXECUTOR_IMPLEMENTATION'
        plan_sha256=$planSha
        plan_path=$planPath
        accepted_connectivity_receipt_sha256=$connectivitySha
        source_identity_sha256=[string]$source.sha256
        assignment_table_count=4
        assignment_rows=[int64](($tablePlans|Measure-Object source_rows -Sum).Sum)
        assignment_bytes=$assignmentBytes
        checksum_definition=$script:ChecksumDefinition
        copy_authorized=$false
        mutation_performed=$false
        credential_values_persisted=$false
        blockers=@()
    }
    $receiptPath=Join-Path $evidenceDir 'review_receipt.json'
    Write-JsonAtomic $receipt $receiptPath
    Assert-ExactMain 'review-exit'
    Write-Host "decision=$($receipt.decision)"
    Write-Host "next_gate=$($receipt.next_gate)"
    Write-Host "plan_sha256=$planSha"
    Write-Host "source_identity_sha256=$($source.sha256)"
    Write-Host "assignment_rows=$($receipt.assignment_rows)"
    Write-Host "assignment_bytes=$assignmentBytes"
    Write-Host "checksum_definition=$($script:ChecksumDefinition)"
    Write-Host 'copy_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host 'credential_values_persisted=False'
    Write-Host "plan_path=$planPath"
    Write-Host "receipt_path=$receiptPath"
    exit 0
}
catch {
    Write-Host "US_ASSIGNMENT_BOUNDED_COPY_REVIEW_FAILED: $($_.Exception.Message)"
    exit 1
}
finally { Pop-Location }
