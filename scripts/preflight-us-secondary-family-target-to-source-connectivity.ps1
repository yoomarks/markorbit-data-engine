[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedSchemaApplyReceiptPath,
    [string]$EvidenceRoot='reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$repoRoot=Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:ReceiptVersion='US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_V1'
$script:ApplyReceiptVersion='US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_V1'
$script:AcceptedApplyReceiptSha256='dd8c5192f1be4dff3177c06795fd0b45a0b74eea12a3cf400f2934ec3a03755c'
$script:AcceptedApplyJournalSha256='8083c12a53b1b0f6fab2f43c73d95c2a569c42176cb081db6da6b467cf6538ad'
$script:AcceptedApplyMainSha='8fc783eb2b60b2341592e80953ec55007d1ad00b'
$script:AcceptedPlanSha='d9dc18fe66381d101bd5b94d2b155da1ed3f4c45102bd06e36e75aab30094620'
$script:TargetDistro='MarkOrbit-ClickHouse'
$script:TargetHost='127.0.0.1'
$script:TargetPort='29000'
$script:SourceNativePort='9000'
$script:Database='markorbit_facts'
$script:TargetPolicy='hot_us_only'
$script:TargetDisk='hot_us'
$script:ExpectedTables=@(
    'us_assignment_record_history',
    'us_assignment_assignor_history',
    'us_assignment_assignee_history',
    'us_assignment_property_history',
    'us_ttab_proceeding_history',
    'us_ttab_party_history',
    'us_ttab_property_history',
    'us_ttab_docket_history'
)

function Invoke-NativeText {
    param([string]$Command,[AllowEmptyString()][AllowEmptyCollection()][string[]]$Arguments,[switch]$AllowFailure)
    $previous=$ErrorActionPreference
    try { $ErrorActionPreference='Continue'; $output=@(& $Command @Arguments 2>&1); $exitCode=$LASTEXITCODE }
    finally { $ErrorActionPreference=$previous }
    $lines=@($output|ForEach-Object { $_.ToString() })
    if(-not $AllowFailure -and $exitCode -ne 0){ throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)" }
    return [ordered]@{exit_code=$exitCode;lines=@($lines)}
}

function Assert-ExactMain([string]$Phase){
    $expected=$ExpectedMainSha.Trim().ToLowerInvariant()
    $branch=(git branch --show-current).Trim()
    $head=(git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
    if($branch -ne 'main' -or $head -ne $expected -or $origin -ne $expected){ throw "Exact main drift during $Phase. branch=$branch expected=$expected head=$head origin_main=$origin" }
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
    if($dir){ New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $tmp="$Path.tmp"; $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($tmp,($Value|ConvertTo-Json -Depth 24),$utf8)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Convert-JsonLines([string[]]$Lines,[string]$Label){
    $rows=@()
    foreach($line in @($Lines|Where-Object { $_.Trim() })){
        try { $rows += ($line|ConvertFrom-Json) } catch { throw "$Label returned invalid JSONEachRow: $line" }
    }
    return @($rows)
}
function Assert-ReadOnlySelect([string]$Sql,[string]$Label){
    $normalized=' '+(($Sql -replace '\s+',' ').Trim().ToUpperInvariant())+' '
    if(-not $normalized.TrimStart().StartsWith('SELECT ')){ throw "$Label is not SELECT-only." }
    foreach($token in @(' INSERT ',' UPDATE ',' DELETE ',' CREATE ',' ALTER ',' DROP ',' TRUNCATE ',' OPTIMIZE ',' MOVE ',' ATTACH ',' DETACH ',' RENAME ',' KILL ')){
        if($normalized.Contains($token)){ throw "$Label contains forbidden mutation token: $($token.Trim())" }
    }
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
function Resolve-AcceptedApplyReceipt {
    if([string]::IsNullOrWhiteSpace($AcceptedSchemaApplyReceiptPath)){ throw 'AcceptedSchemaApplyReceiptPath is required outside ContractOnly.' }
    $path=[IO.Path]::GetFullPath($AcceptedSchemaApplyReceiptPath)
    $sha=Get-FileSha256 $path
    if($sha -ne $script:AcceptedApplyReceiptSha256){ throw "Accepted schema-apply receipt SHA mismatch. expected=$($script:AcceptedApplyReceiptSha256) actual=$sha" }
    $r=Read-JsonFile $path 'Accepted schema-apply receipt'
    if([string]$r.receipt_version -ne $script:ApplyReceiptVersion -or [string]$r.decision -ne 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_SUCCESS'){ throw 'Accepted schema-apply receipt identity/decision drifted.' }
    if([string]$r.main_sha -ne $script:AcceptedApplyMainSha -or [string]$r.plan_sha256 -ne $script:AcceptedPlanSha){ throw 'Accepted schema-apply main/plan drifted.' }
    if(-not [bool]$r.authority_consumed -or -not [bool]$r.schema_apply_executed -or [int]$r.created_table_count -ne 8 -or [int]$r.target_active_parts -ne 0 -or -not [bool]$r.source_unchanged){ throw 'Accepted schema-apply success invariants drifted.' }
    if([string]$r.next_gate -ne 'TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT'){ throw 'Accepted schema-apply next gate drifted.' }
    $journalPath=[IO.Path]::GetFullPath([string]$r.journal_path)
    if((Get-FileSha256 $journalPath) -ne $script:AcceptedApplyJournalSha256){ throw 'Accepted schema-apply journal SHA drifted.' }
    $journal=Read-JsonFile $journalPath 'Accepted schema-apply journal'
    if([string]$journal.state -ne 'SUCCESS' -or @($journal.created_tables).Count -ne 8){ throw 'Accepted schema-apply journal is not durable SUCCESS.' }
    return [ordered]@{path=$path;sha256=$sha;receipt=$r;journal_path=$journalPath;journal=$journal}
}
function Get-RunningWslNames {
    $probe=Invoke-NativeText 'wsl.exe' @('--list','--running','--quiet') -AllowFailure
    if($probe.exit_code -ne 0){ throw 'Unable to inspect running WSL distributions.' }
    return @($probe.lines|ForEach-Object { $_.Trim([char]0).Trim() }|Where-Object { $_ })
}
function Assert-TargetRuntimeReady {
    if(@(Get-RunningWslNames) -notcontains $script:TargetDistro){ throw "Target distro $($script:TargetDistro) is not already running; refusing lifecycle mutation." }
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','pgrep','-f','[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml') -AllowFailure
    $pids=@($probe.lines|Where-Object { $_.Trim() -match '^\d+$' })
    if($probe.exit_code -ne 0 -or $pids.Count -ne 1){ throw 'Accepted target ClickHouse server is not uniquely running.' }
}
function Invoke-TargetRows([string]$Sql,[string]$Label){
    Assert-ReadOnlySelect $Sql $Label; Assert-TargetRuntimeReady
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if($probe.exit_code -ne 0){ throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Invoke-SourceRows([string]$Sql,[string]$Label){
    Assert-ReadOnlySelect $Sql $Label
    $probe=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if($probe.exit_code -ne 0){ throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Get-SourceIdentityFromRows([object[]]$Tables,[object[]]$Parts){
    $lines=@()
    foreach($row in @($Tables)){ $lines += "T|$($row.name)|$($row.engine)|$($row.sorting_key)|$($row.primary_key)|$($row.partition_key)|$($row.create_table_query)" }
    foreach($row in @($Parts)){ $lines += "P|$($row.table)|$($row.partition_id)|$($row.name)|$($row.rows)|$($row.bytes_on_disk)|$($row.disk_name)|$($row.hash_of_all_files)|$($row.hash_of_uncompressed_files)|$($row.uncompressed_hash_of_compressed_files)" }
    return [ordered]@{
        sha256=Get-StringSha256 ($lines -join "`n")
        table_count=@($Tables).Count
        active_part_count=@($Parts).Count
        rows=[int64](($Parts|Measure-Object rows -Sum).Sum)
        bytes=[int64](($Parts|Measure-Object bytes_on_disk -Sum).Sum)
    }
}
function Get-SourceIdentityLocal {
    $quoted=@($script:ExpectedTables|ForEach-Object { "'$_'" }) -join ','
    $tables=@(Invoke-SourceRows "SELECT name, engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database='$($script:Database)' AND name IN ($quoted) ORDER BY name" 'Local source schemas')
    $parts=@(Invoke-SourceRows "SELECT table, partition_id, name, rows, bytes_on_disk, disk_name, hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files FROM system.parts WHERE database='$($script:Database)' AND active AND table IN ($quoted) ORDER BY table, partition_id, name" 'Local source parts')
    if($tables.Count -ne 8 -or $parts.Count -lt 8){ throw 'Authoritative source table/part inventory drifted.' }
    return Get-SourceIdentityFromRows $tables $parts
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
    $ids=@($idsProbe.lines|Where-Object { $_.Trim() })
    if($idsProbe.exit_code -ne 0 -or $ids.Count -ne 1){ throw 'Source Docker ClickHouse is not uniquely running.' }
    $healthProbe=Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$ids[0].Trim()) -AllowFailure
    $health=(@($healthProbe.lines)-join '').Trim().ToLowerInvariant()
    if($healthProbe.exit_code -ne 0 -or $health -ne 'healthy'){ throw "Source Docker ClickHouse health drifted: $health" }
    $portProbe=Invoke-NativeText 'docker' @('compose','port','clickhouse',$script:SourceNativePort) -AllowFailure
    $ports=@($portProbe.lines|Where-Object { $_.Trim() })
    if($portProbe.exit_code -ne 0 -or @($ports|Where-Object { $_ -match '(^|\])0\.0\.0\.0:9000$|^0\.0\.0\.0:9000$|^\[::\]:9000$' }).Count -lt 1){ throw "Source native 9000 is not published on the host: $($ports -join ',')" }
    $versionRows=@(Invoke-SourceRows 'SELECT version() AS version' 'Source ClickHouse version')
    if($versionRows.Count -ne 1){ throw 'Source ClickHouse version probe returned unexpected rows.' }
    return [ordered]@{container_id=$ids[0].Trim();health=$health;published_native_ports=@($ports);version=[string]$versionRows[0].version}
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
    } finally {
        $env:MO_SRC_CH_USER=$oldUser; $env:MO_SRC_CH_PASSWORD=$oldPassword; $env:WSLENV=$oldWslEnv
    }
}
function Get-RemoteSourceIdentity([string]$Gateway,[string]$User,[string]$Password){
    $quoted=@($script:ExpectedTables|ForEach-Object { "'$_'" }) -join ','
    $schemaSql="SELECT name, engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database='$($script:Database)' AND name IN ($quoted) ORDER BY name FORMAT JSONEachRow"
    $partSql="SELECT table, partition_id, name, rows, bytes_on_disk, disk_name, hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files FROM system.parts WHERE database='$($script:Database)' AND active AND table IN ($quoted) ORDER BY table, partition_id, name FORMAT JSONEachRow"
    $tables=@(Convert-JsonLines ((Invoke-TargetWslSourceSql $Gateway $User $Password $schemaSql) -split "`r?`n") 'Remote source schemas')
    $parts=@(Convert-JsonLines ((Invoke-TargetWslSourceSql $Gateway $User $Password $partSql) -split "`r?`n") 'Remote source parts')
    if($tables.Count -ne 8 -or $parts.Count -lt 8){ throw 'Remote source table/part inventory drifted.' }
    return Get-SourceIdentityFromRows $tables $parts
}
function Get-TargetEmptySchemaState {
    $quoted=@($script:ExpectedTables|ForEach-Object { "'$_'" }) -join ','
    $tables=@(Invoke-TargetRows "SELECT name, storage_policy FROM system.tables WHERE database='$($script:Database)' AND name IN ($quoted) ORDER BY name" 'Target schema inventory')
    if($tables.Count -ne 8){ throw "Expected 8 target schemas; observed=$($tables.Count)" }
    if(@($tables|Where-Object { [string]$_.storage_policy -ne $script:TargetPolicy }).Count -ne 0){ throw 'Target storage policy drifted from hot_us_only.' }
    $parts=@(Invoke-TargetRows "SELECT count() AS active_parts, coalesce(sum(rows),0) AS rows, coalesce(sum(bytes_on_disk),0) AS bytes FROM system.parts WHERE database='$($script:Database)' AND active AND table IN ($quoted)" 'Target empty-part state')
    if($parts.Count -ne 1 -or [int64]$parts[0].active_parts -ne 0 -or [int64]$parts[0].rows -ne 0 -or [int64]$parts[0].bytes -ne 0){ throw 'Target schemas are not empty before copy preflight.' }
    return [ordered]@{table_count=8;hot_us_only_count=8;active_parts=0;rows=0;bytes=0}
}
function Assert-TargetServerCanReachSource([string]$Gateway){
    Assert-TargetRuntimeReady
    $query="SELECT count() FROM remote('$Gateway`:$($script:SourceNativePort)','system','one')"
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',$query) -AllowFailure
    $text=(@($probe.lines)-join "`n")
    if($probe.exit_code -eq 0){ throw 'Unauthenticated target-server remote probe unexpectedly succeeded.' }
    if($text -notmatch 'AUTHENTICATION_FAILED' -or $text -notmatch [regex]::Escape("$Gateway`:$($script:SourceNativePort)")){ throw 'Target ClickHouse server did not reach source native endpoint with the expected authentication failure.' }
    return [ordered]@{source_endpoint_reached=$true;expected_authentication_failure_observed=$true;credential_material_sent=$false}
}
function Invoke-ContractFixture {
    if($script:ExpectedTables.Count -ne 8){ throw 'Expected exactly 8 secondary-family tables.' }
    if($script:AcceptedApplyReceiptSha256.Length -ne 64 -or $script:AcceptedApplyJournalSha256.Length -ne 64){ throw 'Accepted artifact SHA contract drifted.' }
    if($script:SourceNativePort -ne '9000' -or $script:TargetPolicy -ne 'hot_us_only'){ throw 'Connectivity topology contract drifted.' }
    $sample='default via 172.19.32.1 dev eth0 proto kernel'
    if($sample -notmatch '\bdefault\s+via\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})\b' -or $matches[1] -ne '172.19.32.1'){ throw 'Gateway parser contract drifted.' }
    Write-Host 'US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_CONTRACT_PASS'
    Write-Host 'read_only=True'
    Write-Host 'copy_authorized=False'
    Write-Host 'credential_values_persisted=False'
}
try {
    Write-Host '===== US SECONDARY FAMILY TARGET-TO-SOURCE CONNECTIVITY PREFLIGHT ====='
    foreach($marker in @('read_only=True','mutation_performed=False','copy_authorized=False','serving_cutover_authorized=False','source_cleanup_authorized=False','credential_values_persisted=False')){ Write-Host $marker }
    if($ContractOnly){ Invoke-ContractFixture; exit 0 }

    & git fetch origin main | Out-Host
    if($LASTEXITCODE -ne 0){ throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    $accepted=Resolve-AcceptedApplyReceipt
    $sourceInitial=Get-SourceIdentityLocal
    if([string]$accepted.journal.source_identity_sha256 -ne [string]$sourceInitial.sha256 -or [string]$accepted.journal.final_source_identity_sha256 -ne [string]$sourceInitial.sha256){ throw 'Current source identity no longer matches accepted #690 source identity.' }
    $targetInitial=Get-TargetEmptySchemaState
    $sourceRuntime=Assert-SourceDockerReady
    $gateway=Resolve-TargetGateway

    $user=Get-DotEnvValue 'CLICKHOUSE_USER'
    $password=Get-DotEnvValue 'CLICKHOUSE_PASSWORD'
    $versionText=Invoke-TargetWslSourceSql $gateway $user $password 'SELECT version() AS version FORMAT JSONEachRow'
    $versionRows=@(Convert-JsonLines ($versionText -split "`r?`n") 'Remote source version')
    if($versionRows.Count -ne 1){ throw 'Authenticated remote source version probe returned unexpected rows.' }
    $remoteSource=Get-RemoteSourceIdentity $gateway $user $password
    if([string]$remoteSource.sha256 -ne [string]$sourceInitial.sha256){ throw 'Target-WSL authenticated source identity does not match local authoritative source.' }
    $serverPath=Assert-TargetServerCanReachSource $gateway
    $targetVersionRows=@(Invoke-TargetRows 'SELECT version() AS version' 'Target ClickHouse version')
    if($targetVersionRows.Count -ne 1){ throw 'Target ClickHouse version probe returned unexpected rows.' }
    Assert-ExactMain 'post-connectivity-probes'

    $sourceFinal=Get-SourceIdentityLocal
    $targetFinal=Get-TargetEmptySchemaState
    if([string]$sourceFinal.sha256 -ne [string]$sourceInitial.sha256){ throw 'Authoritative source changed during connectivity preflight.' }
    if(($targetFinal | ConvertTo-Json -Compress) -ne ($targetInitial | ConvertTo-Json -Compress)){ throw 'Target empty-schema state changed during connectivity preflight.' }
    $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){[IO.Path]::GetFullPath($EvidenceRoot)}else{[IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))}
    $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $dir=Join-Path $root "us_secondary_family_connectivity_preflight_$stamp"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $receiptPath=Join-Path $dir 'connectivity_preflight_receipt.json'
    $receipt=[ordered]@{
        receipt_version=$script:ReceiptVersion
        generated_at=(Get-Date).ToUniversalTime().ToString('o')
        main_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_READY'
        next_gate='BOUNDED_ASSIGNMENT_TABLE_COPY_REVIEW'
        read_only=$true; mutation_performed=$false; copy_authorized=$false; serving_cutover_authorized=$false; source_cleanup_authorized=$false
        accepted_schema_apply=[ordered]@{receipt_path=$accepted.path;receipt_sha256=$accepted.sha256;main_sha=$script:AcceptedApplyMainSha;plan_sha256=$script:AcceptedPlanSha;journal_sha256=$script:AcceptedApplyJournalSha256}
        target_state=$targetFinal
        target_runtime=[ordered]@{distro=$script:TargetDistro;version=[string]$targetVersionRows[0].version;server_to_source_native_path=$serverPath}
        source_runtime=[ordered]@{version=[string]$sourceRuntime.version;native_port=[int]$script:SourceNativePort;published_on_host=$true;health=[string]$sourceRuntime.health}
        endpoint=[ordered]@{derivation='TARGET_WSL_DEFAULT_GATEWAY';gateway=$gateway;port=[int]$script:SourceNativePort;hard_coded=$false}
        authenticated_target_wsl_probe=[ordered]@{success=$true;source_version=[string]$versionRows[0].version;credential_values_persisted=$false;sql_transport='STDIN';credential_transport='WSLENV'}
        source_identity=$sourceFinal
        remote_source_identity=$remoteSource
        source_identity_match=$true
        credential_contract=[ordered]@{clickhouse_user_present=$true;clickhouse_password_present=$true;values_in_receipt=$false;values_printed=$false}
        blockers=@()
    }
    Write-JsonAtomic $receipt $receiptPath
    Assert-ExactMain 'exit'
    Write-Host 'decision=US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_READY'
    Write-Host 'next_gate=BOUNDED_ASSIGNMENT_TABLE_COPY_REVIEW'
    Write-Host "source_identity_sha256=$($sourceFinal.sha256)"
    Write-Host "source_rows=$($sourceFinal.rows)"
    Write-Host "source_bytes=$($sourceFinal.bytes)"
    Write-Host "target_table_count=$($targetFinal.table_count)"
    Write-Host 'target_active_parts=0'
    Write-Host 'copy_authorized=False'
    Write-Host 'credential_values_persisted=False'
    Write-Host "receipt_path=$receiptPath"
    exit 0
}
catch {
    Write-Host "US_SECONDARY_FAMILY_CONNECTIVITY_PREFLIGHT_FAILED: $($_.Exception.Message)"
    exit 2
}
finally { Pop-Location }
