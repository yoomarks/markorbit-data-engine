[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ExpectedMainSha,
    [Parameter(Mandatory=$true)][string]$AssignmentSuccessReceiptPath,
    [string]$EvidenceRoot='reports',
    [switch]$ContractOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repoRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot

$script:ReceiptVersion='US_ASSIGNMENT_TARGET_TTAB_COPY_REVIEW_V1'
$script:PlanVersion='US_TTAB_BOUNDED_COPY_PLAN_V1'
$script:Database='markorbit_facts'
$script:TargetDistro='MarkOrbit-ClickHouse'
$script:TargetHost='127.0.0.1'
$script:TargetPort='29000'
$script:SourceNativePort='9000'
$script:TargetDisk='hot_us'
$script:TargetPolicy='hot_us_only'
$script:AcceptedAssignmentSuccessReceiptSha256='01e8e777696ce79aa3db71eac4837f6e6c2114265f9c2662c998688ec62a38b8'
$script:AcceptedAssignmentMainSha='a81d0dd1af056aded650a411fa2eca5c3cc01fa8'
$script:AcceptedAssignmentPlanSha='22dc4e4a5234ed0a57b82ce1e7929693a6b5dc26341f1b8b1ead5492dd60f209'
$script:AcceptedSourceIdentitySha256='8f7d9f59bbc5bc77671c27d02e9c9bcacdacf01485cb6db2f3eb427e80438d01'
$script:ChecksumDefinition='NULL_SAFE_JSON_TUPLE_CITYHASH64_V2'
$script:RowHashExpression='cityHash64(toJSONString(tuple(*)))'
$script:TransferStrategy='TARGET_WSL_CLICKHOUSE_NETWORK_PULL_FROM_ACCEPTED_DOCKER_CLICKHOUSE'
$script:CopyPrimitive='TARGET_WSL_DUAL_CLICKHOUSE_CLIENT_NATIVE_PIPE_V1'
$script:AssignmentTables=@(
    'us_assignment_record_history','us_assignment_assignor_history',
    'us_assignment_assignee_history','us_assignment_property_history'
)
$script:TtabTables=@(
    'us_ttab_proceeding_history','us_ttab_party_history',
    'us_ttab_property_history','us_ttab_docket_history'
)
$script:AllSecondaryTables=@($script:AssignmentTables)+@($script:TtabTables)

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
    if($Table -notmatch '^us_(assignment|ttab)_[a-z0-9_]+$'){ throw "Unsafe secondary table: $Table" }
    $sql="SELECT count() AS rows, sum($($script:RowHashExpression)) AS checksum_sum, groupBitXor($($script:RowHashExpression)) AS checksum_xor FROM $($script:Database).$Table SETTINGS max_threads = 4, max_execution_time = 900, max_memory_usage = 4294967296, use_uncompressed_cache = 0"
    $rows=@(Invoke-SourceRows $sql "Logical checksum $Table")
    if($rows.Count -ne 1){ throw "Logical checksum returned unexpected row count for $Table" }
    foreach($field in @('rows','checksum_sum','checksum_xor')){ if([string]$rows[0].$field -notmatch '^\d+$'){ throw "Logical checksum field $field is not unsigned decimal for $Table" } }
    return [ordered]@{definition=$script:ChecksumDefinition;row_hash_expression=$script:RowHashExpression;rows=[string]$rows[0].rows;checksum_sum=[string]$rows[0].checksum_sum;checksum_xor=[string]$rows[0].checksum_xor}
}
function Get-TargetLogicalChecksum([string]$Table){
    if($Table -notmatch '^us_(assignment|ttab)_[a-z0-9_]+$'){ throw "Unsafe secondary table: $Table" }
    $sql="SELECT count() AS rows, sum($($script:RowHashExpression)) AS checksum_sum, groupBitXor($($script:RowHashExpression)) AS checksum_xor FROM $($script:Database).$Table SETTINGS max_threads = 4, max_execution_time = 900, max_memory_usage = 4294967296, use_uncompressed_cache = 0"
    $rows=@(Invoke-TargetRows $sql "Target logical checksum $Table")
    if($rows.Count -ne 1){ throw "Target logical checksum returned unexpected row count for $Table" }
    foreach($field in @('rows','checksum_sum','checksum_xor')){ if([string]$rows[0].$field -notmatch '^\d+$'){ throw "Target logical checksum field $field is not unsigned decimal for $Table" } }
    return [ordered]@{definition=$script:ChecksumDefinition;row_hash_expression=$script:RowHashExpression;rows=[string]$rows[0].rows;checksum_sum=[string]$rows[0].checksum_sum;checksum_xor=[string]$rows[0].checksum_xor}
}
function Get-DotEnvValue([string]$Name){
    $path=Join-Path $repoRoot '.env'
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)){ throw '.env missing.' }
    $matches=@(Get-Content -LiteralPath $path | Where-Object { $_ -match ('^'+[regex]::Escape($Name)+'=(.*)$') })
    if($matches.Count -ne 1){ throw "Expected exactly one $Name entry in .env." }
    $value=($matches[0] -split '=',2)[1]
    if([string]::IsNullOrWhiteSpace($value)){ throw "$Name is empty." }
    return $value
}
function Resolve-TargetGateway {
    Assert-TargetRuntimeReady
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','sh','-lc','ip route show default') -AllowFailure
    if($probe.exit_code -ne 0){ throw 'Unable to inspect target WSL default route.' }
    $text=(@($probe.lines)-join ' ').Trim()
    if($text -notmatch '\bdefault\s+via\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})\b'){ throw "Unable to derive target WSL gateway from: $text" }
    return $matches[1]
}function Assert-SourceDockerReady {
    $idsProbe=Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure
    $ids=@($idsProbe.lines|Where-Object { $_.Trim() })
    if($idsProbe.exit_code -ne 0 -or $ids.Count -ne 1){ throw 'Source Docker ClickHouse is not uniquely running.' }
    $healthProbe=Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$ids[0].Trim()) -AllowFailure
    $health=(@($healthProbe.lines)-join '').Trim().ToLowerInvariant()
    if($healthProbe.exit_code -ne 0 -or $health -ne 'healthy'){ throw "Source Docker ClickHouse health drifted: $health" }
    $portProbe=Invoke-NativeText 'docker' @('compose','port','clickhouse',$script:SourceNativePort) -AllowFailure
    $ports=@($portProbe.lines|Where-Object { $_.Trim() })
    if($portProbe.exit_code -ne 0 -or @($ports|Where-Object { $_ -match '(^|\])0\.0\.0\.0:9000$|^0\.0\.0\.0:9000$|^\[::\]:9000$' }).Count -lt 1){ throw 'Source native 9000 is not published on the host.' }
    $versionRows=@(Invoke-SourceRows 'SELECT version() AS version' 'Source ClickHouse version')
    if($versionRows.Count -ne 1){ throw 'Source ClickHouse version probe returned unexpected rows.' }
    return [ordered]@{health=$health;version=[string]$versionRows[0].version}
}
function Invoke-TargetWslSourceSql([string]$Gateway,[string]$User,[string]$Password,[string]$Sql){
    Assert-ReadOnlySelect $Sql 'Target-WSL source SQL'
    $oldUser=$env:MO_SRC_CH_USER; $oldPassword=$env:MO_SRC_CH_PASSWORD; $oldWslEnv=$env:WSLENV
    try {
        $env:MO_SRC_CH_USER=$User; $env:MO_SRC_CH_PASSWORD=$Password
        $bridge='MO_SRC_CH_USER/u:MO_SRC_CH_PASSWORD/u'
        $env:WSLENV=if([string]::IsNullOrWhiteSpace($oldWslEnv)){$bridge}else{"$oldWslEnv`:$bridge"}
        $psi=New-Object Diagnostics.ProcessStartInfo
        $psi.FileName='wsl.exe'
        $psi.Arguments="-d $($script:TargetDistro) -u root -- sh -lc 'exec clickhouse client --host $Gateway --port $($script:SourceNativePort) --user `"`$MO_SRC_CH_USER`" --password `"`$MO_SRC_CH_PASSWORD`"'"
        $psi.UseShellExecute=$false; $psi.RedirectStandardInput=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true; $psi.CreateNoWindow=$true
        $process=New-Object Diagnostics.Process; $process.StartInfo=$psi
        if(-not $process.Start()){ throw 'Unable to start target-WSL source client.' }
        $process.StandardInput.Write($Sql); $process.StandardInput.Close()
        $stdout=$process.StandardOutput.ReadToEnd(); $stderr=$process.StandardError.ReadToEnd(); $process.WaitForExit(); $code=$process.ExitCode; $process.Dispose()
        if($code -ne 0){ throw "Authenticated target-WSL source SELECT failed: $stderr" }
        return $stdout
    } finally { $env:MO_SRC_CH_USER=$oldUser; $env:MO_SRC_CH_PASSWORD=$oldPassword; $env:WSLENV=$oldWslEnv }
}function Assert-TargetServerCanReachSource([string]$Gateway){
    $query="SELECT count() FROM remote('$Gateway`:$($script:SourceNativePort)','system','one')"
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',$query) -AllowFailure
    $text=(@($probe.lines)-join "`n")
    if($probe.exit_code -eq 0){ throw 'Unauthenticated target-server remote probe unexpectedly succeeded.' }
    if($text -notmatch 'AUTHENTICATION_FAILED'){ throw 'Target ClickHouse server did not reach source native endpoint with expected auth failure.' }
    return [ordered]@{source_endpoint_reached=$true;expected_authentication_failure_observed=$true;credential_material_sent=$false}
}
function Assert-LiveConnectivity {
    $runtime=Assert-SourceDockerReady
    $gateway=Resolve-TargetGateway
    $user=Get-DotEnvValue 'CLICKHOUSE_USER'; $password=Get-DotEnvValue 'CLICKHOUSE_PASSWORD'
    $remoteText=Invoke-TargetWslSourceSql $gateway $user $password "SELECT version() AS version, count() AS table_count FROM system.tables WHERE database='$($script:Database)' AND name IN ('us_assignment_record_history','us_assignment_assignor_history','us_assignment_assignee_history','us_assignment_property_history','us_ttab_proceeding_history','us_ttab_party_history','us_ttab_property_history','us_ttab_docket_history') FORMAT JSONEachRow"
    $remote=@(Convert-JsonLines ($remoteText -split "`r?`n") 'Remote source connectivity')
    if($remote.Count -ne 1 -or [int]$remote[0].table_count -ne 8){ throw 'Authenticated target-WSL source inventory probe drifted.' }
    $targetVersion=@(Invoke-TargetRows 'SELECT version() AS version' 'Target ClickHouse version')
    if($targetVersion.Count -ne 1 -or [string]$targetVersion[0].version -ne [string]$remote[0].version){ throw 'Target/source ClickHouse version drifted.' }
    $server=Assert-TargetServerCanReachSource $gateway
    return [ordered]@{gateway=$gateway;source_version=[string]$remote[0].version;target_version=[string]$targetVersion[0].version;source_health=[string]$runtime.health;server_to_source_native_path=$server;credential_values_persisted=$false}
}
function Resolve-AssignmentSuccessReceipt {
    $path=[IO.Path]::GetFullPath($AssignmentSuccessReceiptPath)
    $sha=Get-FileSha256 $path
    if($sha -ne $script:AcceptedAssignmentSuccessReceiptSha256){ throw "Assignment success receipt SHA drifted: $sha" }
    $r=Read-JsonFile $path 'Assignment success receipt'
    if([string]$r.receipt_version -ne 'US_ASSIGNMENT_BOUNDED_COPY_EXECUTOR_V1' -or [string]$r.decision -ne 'US_ASSIGNMENT_BOUNDED_COPY_SUCCESS'){ throw 'Assignment success receipt identity/decision drifted.' }
    if([string]$r.main_sha -ne $script:AcceptedAssignmentMainSha -or [string]$r.plan_sha256 -ne $script:AcceptedAssignmentPlanSha){ throw 'Assignment success receipt main/plan drifted.' }
    if(-not [bool]$r.authority_consumed -or -not [bool]$r.copy_executed -or [int]$r.copied_table_count -ne 4 -or -not [bool]$r.source_unchanged -or -not [bool]$r.ttab_untouched){ throw 'Assignment success invariants drifted.' }
    if([string]$r.next_gate -ne 'ASSIGNMENT_TARGET_ACCEPTANCE_AND_TTAB_REVIEW'){ throw 'Assignment success next gate drifted.' }
    $journalPath=[IO.Path]::GetFullPath([string]$r.journal_path); $journal=Read-JsonFile $journalPath 'Assignment copy journal'
    if([string]$journal.state -ne 'SUCCESS' -or @($journal.accepted_tables).Count -ne 4 -or -not [bool]$journal.authority_consumed){ throw 'Assignment journal is not durable SUCCESS.' }
    if([string]$journal.final_source_identity_sha256 -ne $script:AcceptedSourceIdentitySha256){ throw 'Assignment journal source identity drifted.' }
    return [ordered]@{path=$path;sha256=$sha;receipt=$r;journal_path=$journalPath;journal=$journal}
}function Get-TargetState {
    $quoted=@($script:AllSecondaryTables|ForEach-Object { "'$_'" }) -join ','
    $tables=@(Invoke-TargetRows "SELECT name, engine, sorting_key, primary_key, partition_key, storage_policy FROM system.tables WHERE database='$($script:Database)' AND name IN ($quoted) ORDER BY name" 'Target secondary schemas')
    if($tables.Count -ne 8){ throw "Expected 8 target secondary tables; got $($tables.Count)." }
    if(@($tables|Where-Object { [string]$_.storage_policy -ne $script:TargetPolicy }).Count -ne 0){ throw 'Target storage policy drifted.' }
    $ttabQuoted=@($script:TtabTables|ForEach-Object { "'$_'" }) -join ','
    $ttab=@(Invoke-TargetRows "SELECT count() AS active_parts, coalesce(sum(rows),0) AS rows, coalesce(sum(bytes_on_disk),0) AS bytes FROM system.parts WHERE database='$($script:Database)' AND active AND table IN ($ttabQuoted)" 'Target TTAB empty state')
    if($ttab.Count -ne 1 -or [int64]$ttab[0].active_parts -ne 0 -or [int64]$ttab[0].rows -ne 0 -or [int64]$ttab[0].bytes -ne 0){ throw 'Target TTAB tables are not empty.' }
    $disk=@(Invoke-TargetRows "SELECT name, total_space, free_space FROM system.disks WHERE name='$($script:TargetDisk)'" 'Target hot_us disk')
    if($disk.Count -ne 1){ throw 'hot_us disk missing.' }
    return [ordered]@{tables=$tables;ttab_active_parts=0;ttab_rows=0;ttab_bytes=0;total_space=[int64]$disk[0].total_space;free_space=[int64]$disk[0].free_space}
}
function Get-AssignmentAcceptance([object]$Accepted){
    $results=@()
    foreach($table in $script:AssignmentTables){
        $sourceChecksum=Get-LogicalChecksum $table
        $targetChecksum=Get-TargetLogicalChecksum $table
        foreach($field in @('rows','checksum_sum','checksum_xor')){ if([string]$sourceChecksum[$field] -ne [string]$targetChecksum[$field]){ throw "Assignment source/target $field mismatch for $table" } }
        $parts=@(Invoke-TargetRows "SELECT count() AS active_parts, coalesce(sum(rows),0) AS rows, coalesce(sum(bytes_on_disk),0) AS bytes, groupUniqArray(disk_name) AS disks FROM system.parts WHERE database='$($script:Database)' AND active AND table='$table'" "Target Assignment parts $table")
        if($parts.Count -ne 1 -or [int64]$parts[0].active_parts -lt 1 -or [int64]$parts[0].rows -ne [int64]$targetChecksum.rows){ throw "Target Assignment parts/rows drifted for $table" }
        $bad=@($parts[0].disks|Where-Object { [string]$_ -ne $script:TargetDisk })
        if($bad.Count -ne 0){ throw "Target Assignment disk residency drifted for $table" }
        $acceptedState=@($Accepted.receipt.target_states|Where-Object { [string]$_.table -eq $table })
        if($acceptedState.Count -ne 1 -or [int64]$acceptedState[0].rows -ne [int64]$targetChecksum.rows -or [string]$acceptedState[0].policy -ne $script:TargetPolicy){ throw "Accepted Assignment receipt state drifted for $table" }
        $results += [ordered]@{table=$table;rows=[int64]$targetChecksum.rows;checksum_sum=[string]$targetChecksum.checksum_sum;checksum_xor=[string]$targetChecksum.checksum_xor;active_parts=[int64]$parts[0].active_parts;target_bytes=[int64]$parts[0].bytes;disk=$script:TargetDisk}
    }
    return @($results)
}
function Get-TtabTablePlan([string]$Table,[object]$Source,[object]$Target){
    $sourceTable=@($Source.tables|Where-Object { [string]$_.name -eq $Table }); $targetTable=@($Target.tables|Where-Object { [string]$_.name -eq $Table })
    if($sourceTable.Count -ne 1 -or $targetTable.Count -ne 1){ throw "Schema row missing for $Table" }
    foreach($field in @('engine','sorting_key','primary_key','partition_key')){ if([string]$sourceTable[0].$field -ne [string]$targetTable[0].$field){ throw "Source/target $field mismatch for $Table" } }
    $sourceCols=@(Invoke-SourceRows "SELECT position, name, type, default_kind, default_expression FROM system.columns WHERE database='$($script:Database)' AND table='$Table' ORDER BY position" "Source columns $Table")
    $targetCols=@(Invoke-TargetRows "SELECT position, name, type, default_kind, default_expression FROM system.columns WHERE database='$($script:Database)' AND table='$Table' ORDER BY position" "Target columns $Table")
    $sourceLines=@($sourceCols|ForEach-Object { "$($_.position)|$($_.name)|$($_.type)|$($_.default_kind)|$($_.default_expression)" }); $targetLines=@($targetCols|ForEach-Object { "$($_.position)|$($_.name)|$($_.type)|$($_.default_kind)|$($_.default_expression)" })
    if(($sourceLines -join "`n") -ne ($targetLines -join "`n")){ throw "Source/target column contract mismatch for $Table" }
    $parts=@($Source.parts|Where-Object { [string]$_.table -eq $Table }); if($parts.Count -lt 1){ throw "Source active parts missing for $Table" }
    [int64]$rows=0; [int64]$bytes=0; foreach($part in $parts){ $rows += [int64]$part.rows; $bytes += [int64]$part.bytes_on_disk }
    $checksum=Get-LogicalChecksum $Table; if([int64]$checksum.rows -ne $rows){ throw "Logical checksum row count mismatch for $Table" }
    return [ordered]@{table=$Table;source_rows=$rows;source_bytes=$bytes;source_active_parts=$parts.Count;source_schema_sha256=Get-SchemaFingerprint $sourceTable[0] $sourceCols;source_part_identity_sha256=Get-PartFingerprint $parts;logical_checksum=$checksum;target_policy=$script:TargetPolicy;target_empty_before_copy=$true}
}
function Get-PlanTotals([object[]]$Plans){ [int64]$rows=0; [int64]$bytes=0; foreach($plan in @($Plans)){ $rows += [int64]$plan.source_rows; $bytes += [int64]$plan.source_bytes }; return [ordered]@{rows=$rows;bytes=$bytes} }

function Invoke-ContractFixture {
    if(($script:TtabTables -join '|') -ne 'us_ttab_proceeding_history|us_ttab_party_history|us_ttab_property_history|us_ttab_docket_history'){ throw 'TTAB copy order drifted.' }
    if($script:AcceptedAssignmentSuccessReceiptSha256.Length -ne 64){ throw 'Accepted Assignment receipt SHA contract drifted.' }
    if($script:ChecksumDefinition -ne 'NULL_SAFE_JSON_TUPLE_CITYHASH64_V2' -or $script:RowHashExpression -ne 'cityHash64(toJSONString(tuple(*)))'){ throw 'NULL-safe checksum contract drifted.' }
    if($script:CopyPrimitive -ne 'TARGET_WSL_DUAL_CLICKHOUSE_CLIENT_NATIVE_PIPE_V1'){ throw 'Copy primitive drifted.' }
    $totals=Get-PlanTotals @([ordered]@{source_rows=2;source_bytes=3},[ordered]@{source_rows=5;source_bytes=7})
    if([int64]$totals.rows -ne 7 -or [int64]$totals.bytes -ne 10){ throw 'PS5 ordered-plan aggregation contract failed.' }
    Write-Host 'US_ASSIGNMENT_TARGET_TTAB_COPY_REVIEW_CONTRACT_PASS'
    Write-Host "checksum_definition=$($script:ChecksumDefinition)"
    Write-Host "copy_primitive=$($script:CopyPrimitive)"
    Write-Host 'ttab_copy_authorized=False'
    Write-Host 'mutation_performed=False'
    Write-Host 'credential_values_persisted=False'
}

Write-Host '===== US ASSIGNMENT TARGET ACCEPTANCE + TTAB COPY REVIEW ====='
Write-Host 'review_only=True'
Write-Host 'assignment_target_acceptance_only=True'
Write-Host 'ttab_copy_authorized=False'
Write-Host 'mutation_performed=False'
Write-Host 'copy_primitive_execution_performed=False'
Write-Host 'serving_cutover_authorized=False'
Write-Host 'source_cleanup_authorized=False'
Write-Host 'credential_values_persisted=False'
if($ContractOnly){ Invoke-ContractFixture; Pop-Location; exit 0 }

try {
    Assert-ExactMain 'review-entry'
    $accepted=Resolve-AssignmentSuccessReceipt
    $source=Get-SourceIdentity
    if([string]$source.sha256 -ne $script:AcceptedSourceIdentitySha256){ throw "Current source identity drifted: $($source.sha256)" }
    $target=Get-TargetState
    $connectivity=Assert-LiveConnectivity
    $assignmentAcceptance=@(Get-AssignmentAcceptance $accepted)
    if($assignmentAcceptance.Count -ne 4){ throw 'Assignment target acceptance did not return four tables.' }

    $tablePlans=@()
    for($i=0;$i -lt $script:TtabTables.Count;$i++){
        $table=$script:TtabTables[$i]
        $evidence=Get-TtabTablePlan $table $source $target
        $tablePlans += [ordered]@{migration_order=$i+1;table=$table;source_rows=[int64]$evidence.source_rows;source_bytes=[int64]$evidence.source_bytes;source_active_parts=[int64]$evidence.source_active_parts;source_schema_sha256=[string]$evidence.source_schema_sha256;source_part_identity_sha256=[string]$evidence.source_part_identity_sha256;logical_checksum=$evidence.logical_checksum;target_policy=$script:TargetPolicy;target_empty_before_copy=$true;source_select_sql="SELECT * FROM $($script:Database).$table FORMAT Native";target_insert_sql="INSERT INTO $($script:Database).$table FORMAT Native"}
    }
    $byteOrder=@($tablePlans|Sort-Object @{Expression={[int64]$_.source_bytes}}, @{Expression={[string]$_.table}}|ForEach-Object {$_.table})
    if(($byteOrder -join '|') -ne ($script:TtabTables -join '|')){ throw "TTAB source-byte order drifted: $($byteOrder -join ',')" }
    $ttabTotals=Get-PlanTotals $tablePlans
    $floor=[int64][Math]::Ceiling([double]$target.total_space*0.30)
    $projected=[int64]$target.free_space-[int64]$ttabTotals.bytes
    if($projected -lt $floor){ throw 'TTAB equal-byte copy would violate 30% hot_us reserve.' }

    $plan=[ordered]@{
        plan_version=$script:PlanVersion; main_sha=$ExpectedMainSha; scope='TTAB_ONLY_AFTER_ASSIGNMENT_ACCEPTANCE'
        accepted_assignment=[ordered]@{receipt_sha256=$accepted.sha256;main_sha=$script:AcceptedAssignmentMainSha;plan_sha256=$script:AcceptedAssignmentPlanSha;source_identity_sha256=$script:AcceptedSourceIdentitySha256}
        assignment_acceptance=@($assignmentAcceptance)
        connectivity=[ordered]@{gateway=$connectivity.gateway;source_version=$connectivity.source_version;target_version=$connectivity.target_version;source_health=$connectivity.source_health;server_to_source_native_path=$connectivity.server_to_source_native_path;credential_values_persisted=$false}
        checksum_contract=[ordered]@{definition=$script:ChecksumDefinition;row_hash_expression=$script:RowHashExpression;null_safe=$true}
        transfer_strategy=$script:TransferStrategy
        copy_primitive=[ordered]@{name=$script:CopyPrimitive;execution_runtime='TARGET_WSL';source_endpoint_derivation='TARGET_WSL_DEFAULT_GATEWAY_AT_EXECUTION';source_native_port=9000;source_credentials_transport='WSLENV_RUNTIME_ONLY';source_stream_format='Native';target_stream_format='Native';stream_contract='SOURCE_CLICKHOUSE_CLIENT_STDOUT_TO_TARGET_CLICKHOUSE_CLIENT_STDIN';filesystem_copy_allowed=$false;credential_values_in_plan=$false}
        capacity_guard=[ordered]@{hot_us_total_space=[int64]$target.total_space;hot_us_free_space=[int64]$target.free_space;ttab_source_bytes=[int64]$ttabTotals.bytes;projected_free_after_equal_byte_copy=$projected;recommended_30pct_floor=$floor;recommended_30pct_fits=$true;future_growth_projection_performed=$false;future_growth_sufficiency_claimed=$false}
        tables=@($tablePlans)
        future_authority_token_format='GO #<TTAB_COPY_EXECUTOR_ISSUE> US TTAB bounded table copy <plan_sha256>'
    }
    $plan.pre_copy_requirements=@('exact_main_and_exact_plan_sha','accepted_assignment_target_reconciled','fresh_target_to_source_connectivity','ttab_source_identity_and_logical_checksum_stable','ttab_target_empty_and_hot_us_only','hot_us_30pct_reserve_revalidated','credentials_runtime_only')
    $plan.post_copy_acceptance=@('source_and_target_row_count_equal','source_and_target_null_safe_logical_checksum_equal','target_active_parts_only_on_hot_us','source_identity_unchanged','hot_us_30pct_reserve_revalidated','durable_per_table_receipt_required')
    $plan.failure_contract=[ordered]@{halt_on_first_table_failure=$true;continue_to_later_tables=$false;auto_truncate_allowed=$false;auto_drop_allowed=$false;ordinary_retry_allowed=$false;source_remains_authoritative=$true;partial_target_state_must_be_preserved=$true;remediation_review_required=$true}
    $plan.constraints=[ordered]@{review_only=$true;ttab_copy_authorized=$false;future_ttab_copy_authorized=$false;copy_primitive_execution_performed=$false;assignment_mutation_authorized=$false;serving_cutover_authorized=$false;source_mutation_authorized=$false;source_cleanup_authorized=$false;replay_authorized=$false;wsl_lifecycle_mutation_authorized=$false;docker_lifecycle_mutation_authorized=$false;credential_values_persisted=$false}

    $canonical=$plan|ConvertTo-Json -Depth 24 -Compress
    $planSha=Get-StringSha256 $canonical
    $sourceFinal=Get-SourceIdentity
    if([string]$sourceFinal.sha256 -ne [string]$source.sha256){ throw 'Source identity changed during review.' }
    $targetFinal=Get-TargetState
    if([int64]$targetFinal.ttab_rows -ne 0 -or [int64]$targetFinal.ttab_active_parts -ne 0){ throw 'TTAB target changed during review.' }
    Assert-ExactMain 'pre-receipt'

    $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){$EvidenceRoot}else{Join-Path $repoRoot $EvidenceRoot}
    $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $evidenceDir=Join-Path $root "us_assignment_target_ttab_copy_review_$stamp"
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
    $planPath=Join-Path $evidenceDir 'ttab_copy_plan.json'
    Write-JsonAtomic $plan $planPath
    $writtenPlanSha=Get-StringSha256 ((Read-JsonFile $planPath 'Written TTAB copy plan')|ConvertTo-Json -Depth 24 -Compress)
    if($writtenPlanSha -ne $planSha){ throw 'Canonical TTAB copy plan SHA changed after write/read round trip.' }

    [int64]$assignmentRows=0; foreach($row in $assignmentAcceptance){ $assignmentRows += [int64]$row.rows }
    $receipt=[ordered]@{
        receipt_version=$script:ReceiptVersion;generated_at=(Get-Date).ToUniversalTime().ToString('o');main_sha=$ExpectedMainSha
        decision='US_ASSIGNMENT_TARGET_TTAB_COPY_REVIEW_READY';next_gate='BOUNDED_TTAB_TABLE_COPY_EXECUTOR_IMPLEMENTATION'
        plan_sha256=$planSha;plan_path=$planPath;accepted_assignment_success_receipt_sha256=$accepted.sha256
        source_identity_sha256=[string]$source.sha256;assignment_table_count=4;assignment_rows=$assignmentRows;assignment_target_accepted=$true
        ttab_table_count=4;ttab_rows=[int64]$ttabTotals.rows;ttab_bytes=[int64]$ttabTotals.bytes;ttab_target_empty=$true
        checksum_definition=$script:ChecksumDefinition;recommended_30pct_fits=$true;ttab_copy_authorized=$false;mutation_performed=$false;credential_values_persisted=$false;blockers=@()
    }
    $receiptPath=Join-Path $evidenceDir 'review_receipt.json'; Write-JsonAtomic $receipt $receiptPath
    Assert-ExactMain 'review-exit'
    Write-Host "decision=$($receipt.decision)"; Write-Host "next_gate=$($receipt.next_gate)"; Write-Host "plan_sha256=$planSha"; Write-Host "source_identity_sha256=$($source.sha256)"
    Write-Host "assignment_rows=$assignmentRows"; Write-Host "ttab_rows=$($receipt.ttab_rows)"; Write-Host "ttab_bytes=$($receipt.ttab_bytes)"; Write-Host "checksum_definition=$($script:ChecksumDefinition)"
    Write-Host 'assignment_target_accepted=True'; Write-Host 'ttab_copy_authorized=False'; Write-Host 'mutation_performed=False'; Write-Host 'credential_values_persisted=False'
    Write-Host "plan_path=$planPath"; Write-Host "receipt_path=$receiptPath"; exit 0
}
catch { Write-Host "US_ASSIGNMENT_TARGET_TTAB_COPY_REVIEW_FAILED: $($_.Exception.Message)"; exit 1 }
finally { Pop-Location }
