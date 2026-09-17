[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedMainSha,
    [string]$ReviewReceiptPath,
    [string]$AuthorityToken='',
    [string]$EvidenceRoot='reports',
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$ContractOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$repoRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot

$script:Issue=710
$script:ReceiptVersion='US_TTAB_BOUNDED_COPY_EXECUTOR_V1'
$script:JournalVersion='US_TTAB_BOUNDED_COPY_JOURNAL_V1'
$script:ReviewVersion='US_ASSIGNMENT_TARGET_TTAB_COPY_REVIEW_V1'
$script:PlanVersion='US_TTAB_BOUNDED_COPY_PLAN_V1'
$script:ChecksumDefinition='NULL_SAFE_JSON_TUPLE_CITYHASH64_V2'
$script:RowHashExpression='cityHash64(toJSONString(tuple(*)))'
$script:CopyPrimitive='TARGET_WSL_DUAL_CLICKHOUSE_CLIENT_NATIVE_PIPE_V1'
$script:Database='markorbit_facts'
$script:TargetDistro='MarkOrbit-ClickHouse'
$script:TargetHost='127.0.0.1'
$script:TargetPort='29000'
$script:SourceNativePort='9000'
$script:TargetDisk='hot_us'
$script:TargetPolicy='hot_us_only'
$script:AcceptedAssignmentSuccessReceiptSha256='01e8e777696ce79aa3db71eac4837f6e6c2114265f9c2662c998688ec62a38b8'
$script:SupersededPlanSha='95a07ea0d67f280974f6e4cceabbacd423695069083a60de5cd382cd92db2ef6'
$script:TtabTables=@(
    'us_ttab_proceeding_history',
    'us_ttab_party_history',
    'us_ttab_property_history',
    'us_ttab_docket_history'
)
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
    param([string]$Command,[AllowEmptyString()][AllowEmptyCollection()][string[]]$Arguments,[switch]$AllowFailure)
    $previous=$ErrorActionPreference
    try { $ErrorActionPreference='Continue'; $output=@(& $Command @Arguments 2>&1); $exitCode=$LASTEXITCODE }
    finally { $ErrorActionPreference=$previous }
    $lines=@($output|ForEach-Object {$_.ToString()})
    if(-not $AllowFailure -and $exitCode -ne 0){ throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)" }
    return [ordered]@{exit_code=$exitCode;lines=@($lines)}
}
function Assert-ExactMain([string]$Phase){
    & git fetch origin main | Out-Host
    if($LASTEXITCODE -ne 0){ throw "Unable to fetch origin/main during ${Phase}." }
    $expected=$ExpectedMainSha.Trim().ToLowerInvariant()
    $branch=(git branch --show-current).Trim(); $head=(git rev-parse HEAD).Trim().ToLowerInvariant(); $origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
    if($branch -ne 'main' -or $head -ne $expected -or $origin -ne $expected){ throw "Exact main drift during ${Phase}. branch=$branch expected=$expected head=$head origin_main=$origin" }
    if(git status --porcelain){ throw "Working tree must be clean during $Phase." }
}
function Get-StringSha256([string]$Text){
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Get-FileSha256([string]$Path){
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){ throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Read-JsonFile([string]$Path,[string]$Label){
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)){ throw "$Label missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}
function Write-JsonAtomic([object]$Value,[string]$Path){
    $dir=Split-Path -Parent $Path
    if($dir -and -not (Test-Path $dir)){ New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $tmp="$Path.tmp.$PID"; $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($tmp,($Value|ConvertTo-Json -Depth 30),$utf8)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Get-CanonicalJson([object]$Value){ return ($Value|ConvertTo-Json -Depth 20 -Compress) }
function Assert-SafeTtabTable([string]$Table){
    if($Table -notmatch '^us_ttab_[a-z0-9_]+$' -or $script:TtabTables -notcontains $Table){ throw "Unsafe TTAB table: $Table" }
}
function Resolve-ReviewPlan {
    if([string]::IsNullOrWhiteSpace($ReviewReceiptPath)){ throw 'ReviewReceiptPath is required outside ContractOnly.' }
    $reviewPath=[IO.Path]::GetFullPath($ReviewReceiptPath)
    $review=Read-JsonFile $reviewPath 'TTAB copy review receipt'
    if([string]$review.receipt_version -ne $script:ReviewVersion){ throw 'Review receipt version mismatch.' }
    if([string]$review.decision -ne 'US_ASSIGNMENT_TARGET_TTAB_COPY_REVIEW_READY'){ throw 'Review receipt is not READY.' }
    if([string]$review.next_gate -ne 'BOUNDED_TTAB_TABLE_COPY_EXECUTOR_IMPLEMENTATION'){ throw 'Review next gate drifted.' }
    if([bool]$review.ttab_copy_authorized -or [bool]$review.mutation_performed -or [bool]$review.credential_values_persisted){ throw 'Review safety state drifted.' }
    if([string]$review.accepted_assignment_success_receipt_sha256 -ne $script:AcceptedAssignmentSuccessReceiptSha256){ throw 'Accepted Assignment success receipt SHA drifted.' }
    if(@($review.blockers).Count -ne 0){ throw 'Review receipt contains blockers.' }
    if([string]$review.main_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()){ throw 'Review main SHA is not exact execution main.' }
    $planPath=[IO.Path]::GetFullPath([string]$review.plan_path)
    $plan=Read-JsonFile $planPath 'TTAB copy plan'
    $planSha=Get-StringSha256 (Get-CanonicalJson $plan)
    if($planSha -ne ([string]$review.plan_sha256).ToLowerInvariant()){ throw 'Review plan SHA failed canonical recomputation.' }
    if($planSha -eq $script:SupersededPlanSha){ throw 'Historical pre-executor TTAB copy plan is superseded; rerun #708 on fresh main.' }
    return [ordered]@{review_path=$reviewPath;review_sha256=(Get-FileSha256 $reviewPath);review=$review;plan_path=$planPath;plan_sha256=$planSha;plan=$plan}
}
function Assert-ReviewedPlan([object]$Resolved){
    $p=$Resolved.plan
    if([string]$p.plan_version -ne $script:PlanVersion -or [string]$p.main_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()){ throw 'Plan version/main drifted.' }
    if([string]$p.scope -ne 'TTAB_ONLY_AFTER_ASSIGNMENT_ACCEPTANCE' -or [string]$p.checksum_contract.definition -ne $script:ChecksumDefinition){ throw 'Plan scope/checksum drifted.' }
    if([string]$p.accepted_assignment.receipt_sha256 -ne $script:AcceptedAssignmentSuccessReceiptSha256){ throw 'Plan accepted Assignment receipt drifted.' }
    if([string]$p.checksum_contract.row_hash_expression -ne $script:RowHashExpression -or -not [bool]$p.checksum_contract.null_safe){ throw 'Plan row-hash contract drifted.' }
    if([string]$p.copy_primitive.name -ne $script:CopyPrimitive -or [string]$p.copy_primitive.source_credentials_transport -ne 'WSLENV_RUNTIME_ONLY'){ throw 'Copy primitive contract drifted.' }
    if([bool]$p.copy_primitive.credential_values_in_plan -or [bool]$p.copy_primitive.filesystem_copy_allowed){ throw 'Copy primitive safety contract drifted.' }
    if([string]$p.connectivity.source_version -ne [string]$p.connectivity.target_version -or [bool]$p.connectivity.credential_values_persisted){ throw 'Reviewed connectivity contract drifted.' }
    if(@($p.assignment_acceptance).Count -ne 4){ throw 'Reviewed Assignment acceptance set drifted.' }
    $tables=@($p.tables|Sort-Object {[int]$_.migration_order})
    if($tables.Count -ne 4){ throw 'Reviewed TTAB table count drifted.' }
    $names=@($tables|ForEach-Object {[string]$_.table})
    if(($names -join '|') -ne ($script:TtabTables -join '|')){ throw 'Reviewed TTAB order drifted.' }
    for($i=0;$i -lt $tables.Count;$i++){
        $t=$tables[$i]; $name=[string]$t.table; Assert-SafeTtabTable $name
        if([int]$t.migration_order -ne ($i+1)){ throw "Migration order drifted: $name" }
        if([string]$t.target_policy -ne $script:TargetPolicy -or -not [bool]$t.target_empty_before_copy){ throw "Target contract drifted: $name" }
        if([string]$t.logical_checksum.definition -ne $script:ChecksumDefinition -or [string]$t.logical_checksum.row_hash_expression -ne $script:RowHashExpression){ throw "Checksum contract drifted: $name" }
        $expectedSelect="SELECT * FROM $($script:Database).$name FORMAT Native"
        $expectedInsert="INSERT INTO $($script:Database).$name FORMAT Native"
        if([string]$t.source_select_sql -ne $expectedSelect -or [string]$t.target_insert_sql -ne $expectedInsert){ throw "Reviewed Native SQL drifted: $name" }
    }
    if(-not [bool]$p.failure_contract.halt_on_first_table_failure -or [bool]$p.failure_contract.continue_to_later_tables){ throw 'Failure halt contract drifted.' }
    if([bool]$p.failure_contract.auto_truncate_allowed -or [bool]$p.failure_contract.auto_drop_allowed -or [bool]$p.failure_contract.ordinary_retry_allowed){ throw 'Failure mutation/retry contract drifted.' }
    if(-not [bool]$p.failure_contract.source_remains_authoritative -or -not [bool]$p.failure_contract.partial_target_state_must_be_preserved){ throw 'Failure source/partial-state contract drifted.' }
    if([bool]$p.constraints.ttab_copy_authorized -or [bool]$p.constraints.future_ttab_copy_authorized -or [bool]$p.constraints.assignment_mutation_authorized){ throw 'Plan unexpectedly authorizes copy/mutation.' }
    if(-not [bool]$p.constraints.review_only -or [bool]$p.constraints.copy_primitive_execution_performed){ throw 'Plan review-only state drifted.' }
    return @($tables)
}
function Get-RequiredAuthorityToken([string]$PlanSha){ return "GO #710 US TTAB bounded table copy $PlanSha" }
function Get-AuthorityTokenSha([string]$Token){ return Get-StringSha256 $Token }
function Assert-ReadOnlySelect([string]$Sql,[string]$Label){
    $normalized=' '+(($Sql -replace '\s+',' ').Trim().ToUpperInvariant())+' '
    if(-not $normalized.TrimStart().StartsWith('SELECT ')){ throw "$Label is not SELECT-only." }
    foreach($token in @(' INSERT ',' UPDATE ',' DELETE ',' CREATE ',' ALTER ',' DROP ',' TRUNCATE ',' OPTIMIZE ',' MOVE ',' ATTACH ',' DETACH ',' RENAME ',' KILL ')){
        if($normalized.Contains($token)){ throw "$Label contains forbidden mutation token: $($token.Trim())" }
    }
}
function Convert-JsonLines([string[]]$Lines,[string]$Label){
    $rows=@()
    foreach($line in @($Lines|Where-Object {$_.Trim()})){
        try { $rows += ($line|ConvertFrom-Json) } catch { throw "$Label returned invalid JSONEachRow: $line" }
    }
    return @($rows)
}
function Invoke-SourceRows([string]$Sql,[string]$Label){
    Assert-ReadOnlySelect $Sql $Label
    $probe=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if($probe.exit_code -ne 0){ throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Get-RunningWslNames {
    $probe=Invoke-NativeText 'wsl.exe' @('--list','--running','--quiet') -AllowFailure
    if($probe.exit_code -ne 0){ throw 'Unable to inspect running WSL distributions.' }
    return @($probe.lines|ForEach-Object {$_.Trim([char]0).Trim()}|Where-Object {$_})
}
function Assert-TargetRuntimeReady {
    if(@(Get-RunningWslNames) -notcontains $script:TargetDistro){ throw "Target distro $($script:TargetDistro) is not already running; refusing lifecycle mutation." }
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','pgrep','-f','[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml') -AllowFailure
    $pids=@($probe.lines|Where-Object {$_.Trim() -match '^\d+$'})
    if($probe.exit_code -ne 0 -or $pids.Count -ne 1){ throw 'Accepted target ClickHouse server is not uniquely running.' }
}
function Invoke-TargetRows([string]$Sql,[string]$Label){
    Assert-ReadOnlySelect $Sql $Label; Assert-TargetRuntimeReady
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if($probe.exit_code -ne 0){ throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Get-DotEnvValue([string]$Name){
    $path=Join-Path $repoRoot '.env'
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)){ throw '.env missing.' }
    $matches=@(Get-Content -LiteralPath $path | Where-Object {$_ -match ('^'+[regex]::Escape($Name)+'=(.*)$')})
    if($matches.Count -ne 1){ throw "Expected exactly one $Name entry in .env." }
    $value=($matches[0] -split '=',2)[1].Trim().Trim('"').Trim("'")
    if([string]::IsNullOrWhiteSpace($value)){ throw "$Name is empty." }
    return $value
}
function Get-SourceIdentity {
    $quoted=@($script:AllSecondaryTables|ForEach-Object {"'$_'"}) -join ','
    $tables=@(Invoke-SourceRows "SELECT name, engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database='$($script:Database)' AND name IN ($quoted) ORDER BY name" 'Source secondary schemas')
    $parts=@(Invoke-SourceRows "SELECT table, partition_id, name, rows, bytes_on_disk, disk_name, hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files FROM system.parts WHERE database='$($script:Database)' AND active AND table IN ($quoted) ORDER BY table, partition_id, name" 'Source secondary parts')
    if($tables.Count -ne 8 -or $parts.Count -lt 8){ throw 'Secondary source inventory drifted.' }
    $lines=@()
    foreach($row in @($tables)){ $lines += "T|$($row.name)|$($row.engine)|$($row.sorting_key)|$($row.primary_key)|$($row.partition_key)|$($row.create_table_query)" }
    foreach($row in @($parts)){ $lines += "P|$($row.table)|$($row.partition_id)|$($row.name)|$($row.rows)|$($row.bytes_on_disk)|$($row.disk_name)|$($row.hash_of_all_files)|$($row.hash_of_uncompressed_files)|$($row.uncompressed_hash_of_compressed_files)" }
    [int64]$rows=0; [int64]$bytes=0
    foreach($part in @($parts)){ $rows += [int64]$part.rows; $bytes += [int64]$part.bytes_on_disk }
    return [ordered]@{sha256=Get-StringSha256 ($lines -join "`n");tables=$tables;parts=$parts;rows=$rows;bytes=$bytes;active_part_count=$parts.Count}
}
function Get-SchemaFingerprint([object]$TableRow,[object[]]$Columns){
    $lines=@("TABLE|$($TableRow.name)|$($TableRow.engine)|$($TableRow.sorting_key)|$($TableRow.primary_key)|$($TableRow.partition_key)")
    foreach($col in @($Columns)){ $lines += "COL|$($col.position)|$($col.name)|$($col.type)|$($col.default_kind)|$($col.default_expression)" }
    return Get-StringSha256 ($lines -join "`n")
}
function Get-PartFingerprint([object[]]$Parts){
    $lines=@()
    foreach($row in @($Parts)){ $lines += "$($row.partition_id)|$($row.name)|$($row.rows)|$($row.bytes_on_disk)|$($row.disk_name)|$($row.hash_of_all_files)|$($row.hash_of_uncompressed_files)|$($row.uncompressed_hash_of_compressed_files)" }
    return Get-StringSha256 ($lines -join "`n")
}
function Get-SourceLogicalChecksum([string]$Table){
    Assert-SafeTtabTable $Table
    $sql="SELECT count() AS rows, sum($($script:RowHashExpression)) AS checksum_sum, groupBitXor($($script:RowHashExpression)) AS checksum_xor FROM $($script:Database).$Table SETTINGS max_threads = 4, max_execution_time = 900, max_memory_usage = 4294967296, use_uncompressed_cache = 0"
    $result=@(Invoke-SourceRows $sql "Source logical checksum $Table")
    if($result.Count -ne 1){ throw "Source checksum returned unexpected rows: $Table" }
    foreach($field in @('rows','checksum_sum','checksum_xor')){ if([string]$result[0].$field -notmatch '^\d+$'){ throw "Source checksum field $field invalid: $Table" } }
    return [ordered]@{rows=[string]$result[0].rows;checksum_sum=[string]$result[0].checksum_sum;checksum_xor=[string]$result[0].checksum_xor}
}
function Get-TargetLogicalChecksum([string]$Table){
    Assert-SafeTtabTable $Table
    $sql="SELECT count() AS rows, sum($($script:RowHashExpression)) AS checksum_sum, groupBitXor($($script:RowHashExpression)) AS checksum_xor FROM $($script:Database).$Table SETTINGS max_threads = 4, max_execution_time = 900, max_memory_usage = 4294967296, use_uncompressed_cache = 0"
    $result=@(Invoke-TargetRows $sql "Target logical checksum $Table")
    if($result.Count -ne 1){ throw "Target checksum returned unexpected rows: $Table" }
    foreach($field in @('rows','checksum_sum','checksum_xor')){ if([string]$result[0].$field -notmatch '^\d+$'){ throw "Target checksum field $field invalid: $Table" } }
    return [ordered]@{rows=[string]$result[0].rows;checksum_sum=[string]$result[0].checksum_sum;checksum_xor=[string]$result[0].checksum_xor}
}
function Get-SourceTableSnapshot([object]$PlanTable,[object]$Source){
    $table=[string]$PlanTable.table; Assert-SafeTtabTable $table
    $schema=@($Source.tables|Where-Object {[string]$_.name -eq $table})
    $parts=@($Source.parts|Where-Object {[string]$_.table -eq $table})
    if($schema.Count -ne 1 -or $parts.Count -lt 1){ throw "Source snapshot incomplete: $table" }
    $cols=@(Invoke-SourceRows "SELECT position, name, type, default_kind, default_expression FROM system.columns WHERE database='$($script:Database)' AND table='$table' ORDER BY position" "Source columns $table")
    [int64]$rows=0; [int64]$bytes=0
    foreach($part in @($parts)){ $rows += [int64]$part.rows; $bytes += [int64]$part.bytes_on_disk }
    $checksum=Get-SourceLogicalChecksum $table
    $snapshot=[ordered]@{
        table=$table;rows=$rows;bytes=$bytes;active_parts=$parts.Count
        schema_sha256=Get-SchemaFingerprint $schema[0] $cols
        part_identity_sha256=Get-PartFingerprint $parts
        checksum=$checksum
    }
    if($rows -ne [int64]$PlanTable.source_rows -or $bytes -ne [int64]$PlanTable.source_bytes){ throw "Source rows/bytes drifted: $table" }
    if($snapshot.schema_sha256 -ne [string]$PlanTable.source_schema_sha256){ throw "Source schema fingerprint drifted: $table" }
    if($snapshot.part_identity_sha256 -ne [string]$PlanTable.source_part_identity_sha256){ throw "Source physical-part identity drifted: $table" }
    if([string]$checksum.rows -ne [string]$PlanTable.logical_checksum.rows -or [string]$checksum.checksum_sum -ne [string]$PlanTable.logical_checksum.checksum_sum -or [string]$checksum.checksum_xor -ne [string]$PlanTable.logical_checksum.checksum_xor){ throw "Source V2 checksum drifted: $table" }
    return $snapshot
}
function Get-TargetTableState([string]$Table){
    Assert-SafeTtabTable $Table
    $meta=@(Invoke-TargetRows "SELECT name, storage_policy FROM system.tables WHERE database='$($script:Database)' AND name='$Table'" "Target table metadata $Table")
    if($meta.Count -ne 1 -or [string]$meta[0].storage_policy -ne $script:TargetPolicy){ throw "Target table/policy drifted: $Table" }
    $parts=@(Invoke-TargetRows "SELECT count() AS active_parts, coalesce(sum(rows),0) AS rows, coalesce(sum(bytes_on_disk),0) AS bytes FROM system.parts WHERE database='$($script:Database)' AND active AND table='$Table'" "Target parts $Table")
    if($parts.Count -ne 1){ throw "Target part summary invalid: $Table" }
    return [ordered]@{table=$Table;policy=[string]$meta[0].storage_policy;active_parts=[int64]$parts[0].active_parts;rows=[int64]$parts[0].rows;bytes=[int64]$parts[0].bytes}
}
function Assert-TargetTableEmpty([string]$Table){
    $state=Get-TargetTableState $Table
    if($state.active_parts -ne 0 -or $state.rows -ne 0 -or $state.bytes -ne 0){ throw "Target table is not empty before copy: $Table" }
    return $state
}
function Assert-AssignmentTargetAccepted([object]$Plan){
    $expected=@($Plan.assignment_acceptance)
    if($expected.Count -ne 4){ throw 'Assignment acceptance contract missing from reviewed plan.' }
    $expectedNames=@('us_assignment_record_history','us_assignment_assignor_history','us_assignment_assignee_history','us_assignment_property_history')
    foreach($name in $expectedNames){
        $frozen=@($expected|Where-Object { [string]$_.table -eq $name })
        if($frozen.Count -ne 1){ throw "Frozen Assignment acceptance missing/duplicate: $name" }
        $meta=@(Invoke-TargetRows "SELECT name, storage_policy FROM system.tables WHERE database='$($script:Database)' AND name='$name'" "Assignment target metadata $name")
        if($meta.Count -ne 1 -or [string]$meta[0].storage_policy -ne $script:TargetPolicy){ throw "Assignment target policy drifted: $name" }
        $parts=@(Invoke-TargetRows "SELECT disk_name, count() AS active_parts, sum(rows) AS rows FROM system.parts WHERE database='$($script:Database)' AND active AND table='$name' GROUP BY disk_name ORDER BY disk_name" "Assignment target residency $name")
        if($parts.Count -lt 1 -or @($parts|Where-Object { [string]$_.disk_name -ne $script:TargetDisk }).Count -ne 0){ throw "Assignment target residency drifted: $name" }
        [int64]$rows=0; foreach($row in $parts){ $rows += [int64]$row.rows }
        if($rows -ne [int64]$frozen[0].rows){ throw "Assignment target row count drifted: $name" }
        $checksumRows=@(Invoke-TargetRows "SELECT count() AS rows, sum($($script:RowHashExpression)) AS checksum_sum, groupBitXor($($script:RowHashExpression)) AS checksum_xor FROM $($script:Database).$name SETTINGS max_threads = 4, max_execution_time = 900, max_memory_usage = 4294967296, use_uncompressed_cache = 0" "Assignment target checksum $name")
        if($checksumRows.Count -ne 1){ throw "Assignment target checksum invalid: $name" }
        if([string]$checksumRows[0].rows -ne [string]$frozen[0].rows -or [string]$checksumRows[0].checksum_sum -ne [string]$frozen[0].checksum_sum -or [string]$checksumRows[0].checksum_xor -ne [string]$frozen[0].checksum_xor){ throw "Assignment target V2 checksum drifted: $name" }
    }
    return $true
}
function Get-CapacityGuard([int64]$RemainingSourceBytes){
    $disk=@(Invoke-TargetRows "SELECT name, total_space, free_space FROM system.disks WHERE name='$($script:TargetDisk)'" 'Target hot_us capacity')
    if($disk.Count -ne 1){ throw 'Target hot_us disk identity is not unique.' }
    $total=[int64]$disk[0].total_space; $free=[int64]$disk[0].free_space
    $floor=[int64][Math]::Ceiling([double]$total*0.30); $projected=[int64]($free-$RemainingSourceBytes)
    if($projected -lt $floor){ throw "hot_us 30 percent reserve would be violated. projected=$projected floor=$floor" }
    return [ordered]@{total_space=$total;free_space=$free;remaining_source_bytes=$RemainingSourceBytes;projected_free_after_equal_byte_copy=$projected;recommended_30pct_floor=$floor;recommended_30pct_fits=$true}
}
function Assert-TargetPartsOnHotUs([string]$Table){
    $rows=@(Invoke-TargetRows "SELECT disk_name, count() AS active_parts, sum(rows) AS rows FROM system.parts WHERE database='$($script:Database)' AND active AND table='$Table' GROUP BY disk_name ORDER BY disk_name" "Target residency $Table")
    if($rows.Count -lt 1){ throw "Target has no active parts after copy: $Table" }
    if(@($rows|Where-Object {[string]$_.disk_name -ne $script:TargetDisk}).Count -ne 0){ throw "Target parts escaped hot_us: $Table" }
    return @($rows)
}
function Redact-Secrets([string]$Text,[string]$User,[string]$Password){
    $value=[string]$Text
    foreach($secret in @($Password,$User)){ if(-not [string]::IsNullOrEmpty($secret)){ $value=$value.Replace($secret,'[REDACTED]') } }
    return $value
}
function Convert-ToWslLfText([string]$Text){
    return $Text.Replace("`r`n","`n").Replace("`r","`n")
}
function Invoke-WslScriptWithSourceCredentials([string]$ScriptText,[string]$User,[string]$Password,[switch]$AllowFailure){
    $oldUser=$env:MO_SRC_CH_USER; $oldPassword=$env:MO_SRC_CH_PASSWORD; $oldWslEnv=$env:WSLENV
    try {
        $env:MO_SRC_CH_USER=$User; $env:MO_SRC_CH_PASSWORD=$Password
        $bridge='MO_SRC_CH_USER/u:MO_SRC_CH_PASSWORD/u'
        $env:WSLENV=if([string]::IsNullOrWhiteSpace($oldWslEnv)){$bridge}else{"$oldWslEnv`:$bridge"}
        $psi=New-Object Diagnostics.ProcessStartInfo
        $psi.FileName='wsl.exe'; $psi.Arguments="-d $($script:TargetDistro) -u root -- bash -s --"
        $psi.UseShellExecute=$false; $psi.RedirectStandardInput=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true; $psi.CreateNoWindow=$true
        $process=New-Object Diagnostics.Process; $process.StartInfo=$psi
        if(-not $process.Start()){ throw 'Unable to start target-WSL bash transport.' }
        $normalizedScript=Convert-ToWslLfText $ScriptText
        $process.StandardInput.Write($normalizedScript); $process.StandardInput.Close()
        $stdout=$process.StandardOutput.ReadToEnd(); $stderr=$process.StandardError.ReadToEnd(); $process.WaitForExit(); $code=$process.ExitCode; $process.Dispose()
        $safeOut=Redact-Secrets $stdout $User $Password; $safeErr=Redact-Secrets $stderr $User $Password
        if(-not $AllowFailure -and $code -ne 0){ throw "Target-WSL source transport failed with exit code ${code}: $safeErr$safeOut" }
        return [ordered]@{exit_code=$code;stdout=$safeOut;stderr=$safeErr}
    } finally {
        $env:MO_SRC_CH_USER=$oldUser; $env:MO_SRC_CH_PASSWORD=$oldPassword; $env:WSLENV=$oldWslEnv
    }
}
function Resolve-TargetGateway {
    Assert-TargetRuntimeReady
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','sh','-lc','ip route show default') -AllowFailure
    if($probe.exit_code -ne 0){ throw 'Unable to inspect target WSL default route.' }
    $text=(@($probe.lines)-join ' ').Trim()
    if($text -notmatch '\bdefault\s+via\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})\b'){ throw "Unable to derive target WSL gateway from: $text" }
    $gateway=$matches[1]
    foreach($octet in $gateway.Split('.')){ if([int]$octet -gt 255){ throw "Invalid derived gateway: $gateway" } }
    return $gateway
}
function Assert-SourceDockerReady {
    $idsProbe=Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids=@($idsProbe.lines|Where-Object {$_.Trim()})
    if($idsProbe.exit_code -ne 0 -or $ids.Count -ne 1){ throw 'Source Docker ClickHouse is not uniquely running.' }
    $healthProbe=Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$ids[0].Trim()) -AllowFailure
    $health=(@($healthProbe.lines)-join '').Trim().ToLowerInvariant()
    if($healthProbe.exit_code -ne 0 -or $health -ne 'healthy'){ throw "Source Docker ClickHouse health drifted: $health" }
    $portProbe=Invoke-NativeText 'docker' @('compose','port','clickhouse',$script:SourceNativePort) -AllowFailure
    if($portProbe.exit_code -ne 0 -or @($portProbe.lines|Where-Object {$_ -match '9000$'}).Count -lt 1){ throw 'Source native port 9000 is not published.' }
    return [ordered]@{healthy=$true;native_port=9000}
}
function Invoke-AuthenticatedSourceProbe([string]$Gateway,[string]$User,[string]$Password){
    $bash=@'
set -euo pipefail
config="<config><user>${MO_SRC_CH_USER}</user><password>${MO_SRC_CH_PASSWORD}</password></config>"
exec 3<<<"$config"
exec clickhouse client --config-file=/dev/fd/3 --host "__GATEWAY__" --port 9000 --query "SELECT version() AS version FORMAT JSONEachRow"
'@
    $bash=$bash.Replace('__GATEWAY__',$Gateway)
    $probe=Invoke-WslScriptWithSourceCredentials $bash $User $Password
    $rows=@(Convert-JsonLines ($probe.stdout -split "`r?`n") 'Authenticated source version')
    if($rows.Count -ne 1){ throw 'Authenticated source version probe returned unexpected rows.' }
    return [string]$rows[0].version
}
function Assert-TargetServerCanReachSource([string]$Gateway){
    Assert-TargetRuntimeReady
    $query="SELECT count() FROM remote('$Gateway`:$($script:SourceNativePort)','system','one')"
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',$query) -AllowFailure
    $text=(@($probe.lines)-join "`n")
    if($probe.exit_code -eq 0){ throw 'Unauthenticated target-server remote probe unexpectedly succeeded.' }
    if($text -notmatch 'AUTHENTICATION_FAILED' -or $text -notmatch [regex]::Escape("$Gateway`:$($script:SourceNativePort)")){ throw 'Target server did not reach source with expected auth failure.' }
    return $true
}
function Assert-FreshConnectivity {
    $sourceRuntime=Assert-SourceDockerReady
    $gateway=Resolve-TargetGateway
    $user=Get-DotEnvValue 'CLICKHOUSE_USER'; $password=Get-DotEnvValue 'CLICKHOUSE_PASSWORD'
    $remoteVersion=Invoke-AuthenticatedSourceProbe $gateway $user $password
    $localVersion=@(Invoke-SourceRows 'SELECT version() AS version' 'Local source version')
    $targetVersion=@(Invoke-TargetRows 'SELECT version() AS version' 'Target version')
    if($localVersion.Count -ne 1 -or $targetVersion.Count -ne 1){ throw 'ClickHouse version probe returned unexpected rows.' }
    if([string]$localVersion[0].version -ne $remoteVersion){ throw 'Authenticated source version does not match local source.' }
    if([string]$targetVersion[0].version -ne $remoteVersion){ throw 'Target/source ClickHouse version drifted.' }
    [void](Assert-TargetServerCanReachSource $gateway)
    return [ordered]@{
        gateway=$gateway;source_native_port=9000
        source_version=$remoteVersion;target_version=[string]$targetVersion[0].version
        source_runtime_healthy=[bool]$sourceRuntime.healthy;credentials_runtime_only=$true
    }
}
function Get-NativePipeScript([string]$Table,[string]$Gateway){
    Assert-SafeTtabTable $Table
    if($Gateway -notmatch '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'){ throw 'Unsafe source gateway.' }
    $template=@'
set -euo pipefail
xml_escape(){ local s="$1"; s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; s="${s//\"/&quot;}"; printf '%s' "$s"; }
user_xml="$(xml_escape "$MO_SRC_CH_USER")"
password_xml="$(xml_escape "$MO_SRC_CH_PASSWORD")"
config="<config><user>${user_xml}</user><password>${password_xml}</password></config>"
exec 3<<<"$config"
clickhouse client --config-file=/dev/fd/3 --host "__GATEWAY__" --port 9000 --query "SELECT * FROM markorbit_facts.__TABLE__ FORMAT Native" |
  clickhouse client --host 127.0.0.1 --port 29000 --query "INSERT INTO markorbit_facts.__TABLE__ FORMAT Native"
status=("${PIPESTATUS[@]}")
if [ "${status[0]}" -ne 0 ] || [ "${status[1]}" -ne 0 ]; then exit 41; fi
'@
    return $template.Replace('__GATEWAY__',$Gateway).Replace('__TABLE__',$Table)
}
function Invoke-TtabNativePipe([string]$Table,[string]$Gateway){
    Assert-SafeTtabTable $Table
    $user=Get-DotEnvValue 'CLICKHOUSE_USER'; $password=Get-DotEnvValue 'CLICKHOUSE_PASSWORD'
    $scriptText=Get-NativePipeScript $Table $Gateway
    if($scriptText -match '(?i)--password|us_assignment_'){ throw 'Native pipe script contains forbidden credential/Assignment argv text.' }
    $probe=Invoke-WslScriptWithSourceCredentials $scriptText $user $password -AllowFailure
    if($probe.exit_code -ne 0){ throw "Native pipe failed for ${Table}: $($probe.stderr)$($probe.stdout)" }
}
function Get-TargetSchemaFingerprint([string]$Table){
    Assert-SafeTtabTable $Table
    $meta=@(Invoke-TargetRows "SELECT name, engine, sorting_key, primary_key, partition_key FROM system.tables WHERE database='$($script:Database)' AND name='$Table'" "Target schema $Table")
    if($meta.Count -ne 1){ throw "Target schema identity missing/duplicate: $Table" }
    $cols=@(Invoke-TargetRows "SELECT position, name, type, default_kind, default_expression FROM system.columns WHERE database='$($script:Database)' AND table='$Table' ORDER BY position" "Target columns $Table")
    return Get-SchemaFingerprint $meta[0] $cols
}
function Get-RemainingPlanBytes([object[]]$Tables,[int]$FromOrder){
    [int64]$bytes=0
    foreach($t in @($Tables)){ if([int]$t.migration_order -ge $FromOrder){ $bytes += [int64]$t.source_bytes } }
    return $bytes
}
function Assert-PreCopyTable([object]$PlanTable,[object]$InitialSource,[object[]]$AllTables,[object]$FullPlan){
    $table=[string]$PlanTable.table; Assert-ExactMain "pre-copy-$table"
    $sourceNow=Get-SourceIdentity
    if([string]$sourceNow.sha256 -ne [string]$InitialSource.sha256){ throw "Full source identity drift before copy: $table" }
    $snapshot=Get-SourceTableSnapshot $PlanTable $sourceNow
    $target=Assert-TargetTableEmpty $table
    if((Get-TargetSchemaFingerprint $table) -ne [string]$PlanTable.source_schema_sha256){ throw "Target/source schema fingerprint mismatch: $table" }
    [void](Assert-AssignmentTargetAccepted $FullPlan)
    $remaining=Get-RemainingPlanBytes $AllTables ([int]$PlanTable.migration_order)
    $capacity=Get-CapacityGuard $remaining
    $connectivity=Assert-FreshConnectivity
    return [ordered]@{source=$snapshot;target=$target;capacity=$capacity;connectivity=$connectivity}
}
function Assert-PostCopyTable([object]$PlanTable,[object]$InitialSource,[object[]]$AllTables,[object]$FullPlan){
    $table=[string]$PlanTable.table; Assert-ExactMain "post-copy-$table"
    $sourceNow=Get-SourceIdentity
    if([string]$sourceNow.sha256 -ne [string]$InitialSource.sha256){ throw "Source changed during copy: $table" }
    $state=Get-TargetTableState $table
    if($state.rows -ne [int64]$PlanTable.source_rows){ throw "Target row count mismatch after copy: $table" }
    $checksum=Get-TargetLogicalChecksum $table
    if([string]$checksum.rows -ne [string]$PlanTable.logical_checksum.rows -or [string]$checksum.checksum_sum -ne [string]$PlanTable.logical_checksum.checksum_sum -or [string]$checksum.checksum_xor -ne [string]$PlanTable.logical_checksum.checksum_xor){ throw "Target V2 checksum mismatch after copy: $table" }
    $residency=@(Assert-TargetPartsOnHotUs $table)
    [void](Assert-AssignmentTargetAccepted $FullPlan)
    $nextOrder=[int]$PlanTable.migration_order+1
    $remaining=Get-RemainingPlanBytes $AllTables $nextOrder
    $capacity=Get-CapacityGuard $remaining
    return [ordered]@{
        table=$table;rows=[int64]$state.rows;bytes=[int64]$state.bytes
        logical_checksum=$checksum;residency=@($residency)
        source_identity_sha256=[string]$sourceNow.sha256
        capacity=$capacity;assignment_target_accepted=$true;accepted_at=(Get-Date).ToUniversalTime().ToString('o')
    }
}
function Get-JournalPaths([string]$PlanSha){
    $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){[IO.Path]::GetFullPath($EvidenceRoot)}else{[IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))}
    $dir=Join-Path $root "us_ttab_bounded_copy_$PlanSha"
    return [ordered]@{directory=$dir;journal=(Join-Path $dir 'copy_journal.json');success=(Join-Path $dir 'success_receipt.json')}
}
function Assert-NoExistingJournal([string]$Path){
    if(Test-Path -LiteralPath $Path -PathType Leaf){ $old=Read-JsonFile $Path 'Existing copy journal'; throw "TTAB copy authority already consumed/frozen. state=$($old.state) journal=$Path" }
}
function Save-Journal([object]$Journal,[string]$Path){
    $Journal.updated_at=(Get-Date).ToUniversalTime().ToString('o'); Write-JsonAtomic $Journal $Path
}
function New-CopyJournal([object]$Resolved,[object]$InitialSource,[string]$Token){
    return [ordered]@{
        journal_version=$script:JournalVersion;issue=$script:Issue;state='AUTHORIZED_NOT_STARTED'
        main_sha=$ExpectedMainSha.Trim().ToLowerInvariant();plan_sha256=$Resolved.plan_sha256
        review_receipt_sha256=$Resolved.review_sha256;review_receipt_path=$Resolved.review_path
        authority_token_sha256=Get-AuthorityTokenSha $Token;authority_consumed=$true
        source_identity_sha256=[string]$InitialSource.sha256;source_rows=[int64]$InitialSource.rows;source_bytes=[int64]$InitialSource.bytes
        started_at=(Get-Date).ToUniversalTime().ToString('o');updated_at=(Get-Date).ToUniversalTime().ToString('o')
        current_table=$null;accepted_tables=@();last_error=$null
        ordinary_retry_allowed=$false;auto_truncate_allowed=$false;auto_drop_allowed=$false
        source_remains_authoritative=$true;next_gate='US_TTAB_BOUNDED_COPY_IN_PROGRESS'
    }
}
function Write-TableAcceptance([object]$Acceptance,[string]$Directory,[int]$Order){
    $path=Join-Path $Directory ("table_{0:D2}_{1}_acceptance.json" -f $Order,[string]$Acceptance.table)
    Write-JsonAtomic $Acceptance $path
    return [ordered]@{path=$path;sha256=Get-FileSha256 $path}
}
function Get-FailureTargetState {
    $states=@()
    foreach($table in $script:TtabTables){
        try { $states += Get-TargetTableState $table }
        catch { $states += [ordered]@{table=$table;inspection_error=$_.Exception.Message} }
    }
    return @($states)
}
function Invoke-ContractFixture {
    if(($script:TtabTables -join '|') -ne 'us_ttab_proceeding_history|us_ttab_party_history|us_ttab_property_history|us_ttab_docket_history'){ throw 'TTAB copy order drifted.' }
    $sampleSha='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $token=Get-RequiredAuthorityToken $sampleSha
    if($token -ne "GO #710 US TTAB bounded table copy $sampleSha"){ throw 'Authority token format drifted.' }
    if((Get-RequiredAuthorityToken $script:SupersededPlanSha) -notmatch '^GO #710 '){ throw 'Issue binding drifted.' }
    $pipe=Get-NativePipeScript 'us_ttab_proceeding_history' '172.19.32.1'
    $lfFixture=Convert-ToWslLfText "set -euo pipefail`r`necho ok`r`n"
    if($lfFixture.Contains("`r") -or $lfFixture -ne "set -euo pipefail`necho ok`n"){ throw 'WSL LF normalization contract drifted.' }
    if($pipe -notmatch '/dev/fd/3' -or $pipe -match '(?i)--password|us_assignment_'){ throw 'Native pipe credential/scope contract drifted.' }
    if($pipe -notmatch 'MO_SRC_CH_USER' -or $pipe -notmatch 'MO_SRC_CH_PASSWORD'){ throw 'Native pipe runtime env bridge drifted.' }
    if($pipe -notmatch 'FORMAT Native' -or $pipe -notmatch 'INSERT INTO markorbit_facts.us_ttab_proceeding_history'){ throw 'Native pipe SQL contract drifted.' }
    $fixture=[ordered]@{plan_sha256=$sampleSha;review_sha256='b';review_path='fixture'}
    $source=[ordered]@{sha256='c';rows=1;bytes=1}
    $journal=New-CopyJournal $fixture $source $token
    if(-not [bool]$journal.authority_consumed -or [bool]$journal.ordinary_retry_allowed -or [bool]$journal.auto_truncate_allowed -or [bool]$journal.auto_drop_allowed){ throw 'Journal fail-closed contract drifted.' }
    if(-not [bool]$journal.source_remains_authoritative){ throw 'Journal source authority contract drifted.' }
    Write-Host 'US_TTAB_BOUNDED_COPY_EXECUTOR_CONTRACT_PASS'
    Write-Host "copy_primitive=$($script:CopyPrimitive)"
    Write-Host 'copy_executed=False'
    Write-Host 'authority_consumed=False'
    Write-Host 'assignment_mutation_authorized=False'
    Write-Host 'credential_values_persisted=False'
}

$script:ActiveJournal=$null
$script:ActiveJournalPath=$null
try {
    Write-Host '===== US TTAB BOUNDED COPY EXECUTOR ====='
    Write-Host 'source_mutation_authorized=False'
    Write-Host 'assignment_mutation_authorized=False'
    Write-Host 'serving_cutover_authorized=False'
    Write-Host 'source_cleanup_authorized=False'
    Write-Host 'auto_truncate_authorized=False'
    Write-Host 'auto_drop_authorized=False'
    if($ContractOnly){ Invoke-ContractFixture; exit 0 }
    if($Apply -and $DryRun){ throw 'Choose exactly one of -DryRun or -Apply.' }
    if(-not $Apply -and -not $DryRun){ throw 'Outside ContractOnly, exactly one of -DryRun or -Apply is required.' }
    & git fetch origin main | Out-Host; if($LASTEXITCODE -ne 0){ throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    if(-not (Test-Path -LiteralPath (Join-Path $repoRoot '.env') -PathType Leaf)){ throw '.env missing; refusing source credential/runtime checks.' }
    $resolved=Resolve-ReviewPlan
    $tables=@(Assert-ReviewedPlan $resolved)
    $requiredToken=Get-RequiredAuthorityToken $resolved.plan_sha256
    if($Apply -and $AuthorityToken -ne $requiredToken){ throw "Authority token mismatch. Exact required token: $requiredToken" }
    if($DryRun -and $AuthorityToken){ throw 'DryRun must not accept or consume AuthorityToken.' }
    $paths=Get-JournalPaths $resolved.plan_sha256
    Assert-NoExistingJournal $paths.journal
    $sourceInitial=Get-SourceIdentity
    if([string]$sourceInitial.sha256 -ne [string]$resolved.plan.accepted_assignment.source_identity_sha256){ throw 'Full source identity drifted from reviewed plan.' }
    if([string]$sourceInitial.sha256 -ne [string]$resolved.review.source_identity_sha256){ throw 'Full source identity drifted from review receipt.' }
    [void](Assert-AssignmentTargetAccepted $resolved.plan)
    $preflights=@()
    foreach($tablePlan in $tables){ $preflights += Assert-PreCopyTable $tablePlan $sourceInitial $tables $resolved.plan }
    Assert-ExactMain 'post-preflight'
    if($DryRun){
        $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){[IO.Path]::GetFullPath($EvidenceRoot)}else{[IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))}
        $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
        $dir=Join-Path $root "us_ttab_bounded_copy_dryrun_$stamp"; New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $receiptPath=Join-Path $dir 'dry_run_receipt.json'
        $receipt=[ordered]@{
            receipt_version=$script:ReceiptVersion;mode='DRY_RUN';decision='US_TTAB_BOUNDED_COPY_READY_FOR_EXACT_GO'
            main_sha=$ExpectedMainSha.Trim().ToLowerInvariant();plan_sha256=$resolved.plan_sha256;review_receipt_sha256=$resolved.review_sha256
            required_authority_token=$requiredToken;authority_consumed=$false;copy_executed=$false;mutation_performed=$false
            source_identity_sha256=[string]$sourceInitial.sha256;table_count=4
            checksum_definition=$script:ChecksumDefinition;copy_primitive=$script:CopyPrimitive
            preflight_count=$preflights.Count;credential_values_persisted=$false
            next_gate='EXACT_OPERATOR_GO_710_REQUIRED'
        }
        Write-JsonAtomic $receipt $receiptPath; Assert-ExactMain 'dry-run-exit'
        Write-Host 'decision=US_TTAB_BOUNDED_COPY_READY_FOR_EXACT_GO'
        Write-Host "plan_sha256=$($resolved.plan_sha256)"
        Write-Host "required_authority_token=$requiredToken"
        Write-Host 'authority_consumed=False'; Write-Host 'copy_executed=False'
        Write-Host 'credential_values_persisted=False'; Write-Host "receipt_path=$receiptPath"
        exit 0
    }

    $script:ActiveJournal=New-CopyJournal $resolved $sourceInitial $AuthorityToken
    $script:ActiveJournalPath=$paths.journal
    Save-Journal $script:ActiveJournal $script:ActiveJournalPath
    Write-Host 'authority_consumed=True'
    Write-Host "journal_path=$script:ActiveJournalPath"
    foreach($tablePlan in $tables){
        $order=[int]$tablePlan.migration_order; $table=[string]$tablePlan.table
        $pre=Assert-PreCopyTable $tablePlan $sourceInitial $tables $resolved.plan
        $script:ActiveJournal.state='COPY_INTENT'
        $script:ActiveJournal.current_table=[ordered]@{
            order=$order;table=$table;intent_at=(Get-Date).ToUniversalTime().ToString('o')
            source_rows=[int64]$tablePlan.source_rows;source_checksum_sum=[string]$tablePlan.logical_checksum.checksum_sum
        }
        Save-Journal $script:ActiveJournal $script:ActiveJournalPath
        Invoke-TtabNativePipe $table ([string]$pre.connectivity.gateway)
        $post=Assert-PostCopyTable $tablePlan $sourceInitial $tables $resolved.plan
        $acceptance=[ordered]@{
            receipt_version='US_TTAB_TABLE_COPY_ACCEPTANCE_V1'
            main_sha=$ExpectedMainSha.Trim().ToLowerInvariant();plan_sha256=$resolved.plan_sha256
            order=$order;table=$table;source_rows=[int64]$tablePlan.source_rows
            checksum_definition=$script:ChecksumDefinition;target_acceptance=$post
            source_unchanged=$true;target_parts_hot_us_only=$true;assignment_target_accepted=$true;credential_values_persisted=$false
        }
        $artifact=Write-TableAcceptance $acceptance $paths.directory $order
        $entry=[ordered]@{order=$order;table=$table;receipt_path=$artifact.path;receipt_sha256=$artifact.sha256;accepted_at=(Get-Date).ToUniversalTime().ToString('o')}
        $script:ActiveJournal.accepted_tables=@($script:ActiveJournal.accepted_tables)+@($entry)
        $script:ActiveJournal.state='APPLYING'; $script:ActiveJournal.current_table=$null
        Save-Journal $script:ActiveJournal $script:ActiveJournalPath
        Write-Host "copied_and_verified=$order|$table"
    }
    Assert-ExactMain 'pre-final-audit'
    $sourceFinal=Get-SourceIdentity
    if([string]$sourceFinal.sha256 -ne [string]$sourceInitial.sha256){ throw 'Authoritative source changed during TTAB copy.' }
    [void](Assert-AssignmentTargetAccepted $resolved.plan)
    if(@($script:ActiveJournal.accepted_tables).Count -ne 4){ throw 'Final accepted TTAB table count is not four.' }
    $finalStates=@()
    foreach($tablePlan in $tables){
        $state=Get-TargetTableState ([string]$tablePlan.table)
        if($state.rows -ne [int64]$tablePlan.source_rows){ throw "Final target rows drifted: $($tablePlan.table)" }
        $finalStates += $state
    }
    $finalCapacity=Get-CapacityGuard 0
    Assert-ExactMain 'final-audit-complete'
    $script:ActiveJournal.state='SUCCESS'; $script:ActiveJournal.current_table=$null
    $script:ActiveJournal.completed_at=(Get-Date).ToUniversalTime().ToString('o')
    $script:ActiveJournal.final_source_identity_sha256=[string]$sourceFinal.sha256
    $script:ActiveJournal.next_gate='SECONDARY_FAMILY_TARGET_ACCEPTANCE_AND_SERVING_CUTOVER_REVIEW'
    Save-Journal $script:ActiveJournal $script:ActiveJournalPath
    $success=[ordered]@{
        receipt_version=$script:ReceiptVersion;decision='US_TTAB_BOUNDED_COPY_SUCCESS'
        main_sha=$ExpectedMainSha.Trim().ToLowerInvariant();plan_sha256=$resolved.plan_sha256
        review_receipt_sha256=$resolved.review_sha256;authority_consumed=$true;copy_executed=$true
        copied_table_count=4;source_unchanged=$true;assignment_target_accepted=$true
        target_states=@($finalStates);capacity_after=$finalCapacity;journal_path=$script:ActiveJournalPath
        credential_values_persisted=$false;next_gate='SECONDARY_FAMILY_TARGET_ACCEPTANCE_AND_SERVING_CUTOVER_REVIEW'
    }
    Write-JsonAtomic $success $paths.success; Assert-ExactMain 'success-exit'
    Write-Host 'decision=US_TTAB_BOUNDED_COPY_SUCCESS'
    Write-Host "plan_sha256=$($resolved.plan_sha256)"; Write-Host 'copied_table_count=4'
    Write-Host 'source_unchanged=True'; Write-Host 'assignment_target_accepted=True'
    Write-Host 'next_gate=SECONDARY_FAMILY_TARGET_ACCEPTANCE_AND_SERVING_CUTOVER_REVIEW'
    Write-Host "receipt_path=$($paths.success)"
    exit 0
}
catch {
    $message=$_.Exception.Message
    $safeUser=''; $safePassword=''
    try { $safeUser=Get-DotEnvValue 'CLICKHOUSE_USER' } catch {}
    try { $safePassword=Get-DotEnvValue 'CLICKHOUSE_PASSWORD' } catch {}
    $message=Redact-Secrets $message $safeUser $safePassword
    if($script:ActiveJournal -and $script:ActiveJournalPath -and (Test-Path -LiteralPath $script:ActiveJournalPath -PathType Leaf)){
        try {
            $script:ActiveJournal.state='PARTIAL_FAILURE_FROZEN'
            $script:ActiveJournal.last_error=$message
            $script:ActiveJournal.partial_state_observed_at=(Get-Date).ToUniversalTime().ToString('o')
            $script:ActiveJournal.target_ttab_state=@(Get-FailureTargetState)
            $script:ActiveJournal.ordinary_retry_allowed=$false
            $script:ActiveJournal.auto_truncate_allowed=$false; $script:ActiveJournal.auto_drop_allowed=$false
            $script:ActiveJournal.source_remains_authoritative=$true
            $script:ActiveJournal.next_gate='US_TTAB_PARTIAL_COPY_REMEDIATION_REVIEW'
            Save-Journal $script:ActiveJournal $script:ActiveJournalPath
        } catch { Write-Host 'journal_freeze_error=[REDACTED_OR_NONCRITICAL]' }
    }
    Write-Host "US_TTAB_BOUNDED_COPY_FAILED: $message"
    if($script:ActiveJournalPath){ Write-Host "journal_path=$script:ActiveJournalPath" }
    exit 2
}
finally { Pop-Location }
