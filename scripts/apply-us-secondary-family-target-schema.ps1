[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$ReviewReceiptPath,
    [string]$AuthorityToken = '',
    [string]$EvidenceRoot = 'reports',
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:ReceiptVersion = 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_V1'
$script:JournalVersion = 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_JOURNAL_V1'
$script:ReviewVersion = 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_V1'
$script:PlanVersion = 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_PLAN_V1'
$script:TargetDistro = 'MarkOrbit-ClickHouse'
$script:TargetHost = '127.0.0.1'
$script:TargetPort = '29000'
$script:TargetDisk = 'hot_us'
$script:TargetPolicy = 'hot_us_only'
$script:ExpectedTables = @(
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
    if (-not $AllowFailure -and $exitCode -ne 0) { throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)" }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}

function Assert-ExactMain([string]$Phase) {
    $expected=$ExpectedMainSha.Trim().ToLowerInvariant()
    $branch=(git branch --show-current).Trim(); $head=(git rev-parse HEAD).Trim().ToLowerInvariant(); $origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($branch -ne 'main' -or $head -ne $expected -or $origin -ne $expected) { throw "Exact main drift during $Phase. branch=$branch expected=$expected head=$head origin_main=$origin" }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}
function Get-StringSha256([string]$Text) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Read-JsonFile([string]$Path,[string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}
function Write-JsonAtomic([object]$Value,[string]$Path) {
    $dir=Split-Path -Parent $Path; if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $tmp="$Path.tmp"; $utf8=New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($tmp,($Value|ConvertTo-Json -Depth 30),$utf8)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Get-CanonicalJson([object]$Value) { return ($Value|ConvertTo-Json -Depth 16 -Compress) }
function Assert-SafeTable([string]$Table) {
    if ($Table -notmatch '^us_(assignment|ttab)_[a-z0-9_]+$') { throw "Unsafe reviewed table: $Table" }
}
function Resolve-ReviewPlan {
    if ([string]::IsNullOrWhiteSpace($ReviewReceiptPath)) { throw 'ReviewReceiptPath is required outside ContractOnly.' }
    $reviewPath=[IO.Path]::GetFullPath($ReviewReceiptPath)
    $review=Read-JsonFile $reviewPath 'Schema apply review receipt'
    if ([string]$review.receipt_version -ne $script:ReviewVersion) { throw 'Review receipt version mismatch.' }
    if ([string]$review.decision -ne 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_REVIEW_READY') { throw 'Review receipt is not READY.' }
    if ([string]$review.next_gate -ne 'TARGET_SCHEMA_APPLY_AUTHORITY_REQUIRED') { throw 'Review next gate drifted.' }
    if ([bool]$review.mutation_performed -or [bool]$review.schema_apply_executed -or [bool]$review.authority_consumed -or [bool]$review.apply_authorized) { throw 'Review safety state drifted.' }
    if (@($review.blockers).Count -ne 0) { throw 'Review receipt contains blockers.' }
    if ([string]$review.main_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant()) { throw 'Review main SHA is not the exact execution main.' }
    if ([string]$review.plan.version -ne $script:PlanVersion -or [int]$review.plan.step_count -ne 8) { throw 'Review plan identity drifted.' }
    $planPath=[IO.Path]::GetFullPath([string]$review.plan.path)
    $plan=Read-JsonFile $planPath 'Schema apply plan'
    $planSha=Get-StringSha256 (Get-CanonicalJson $plan)
    if ($planSha -ne ([string]$review.plan.sha256).ToLowerInvariant()) { throw 'Review plan SHA failed canonical recomputation.' }
    if (@($plan.schema_apply_steps).Count -ne 8) { throw 'Reviewed schema step count drifted.' }
    return [ordered]@{ review_path=$reviewPath; review_sha256=(Get-FileSha256 $reviewPath); review=$review; plan_path=$planPath; plan_sha256=$planSha; plan=$plan }
}
function Assert-ReviewedSteps([object]$PlanResult) {
    $steps=@($PlanResult.plan.schema_apply_steps|Sort-Object { [int]$_.order })
    $names=@($steps|ForEach-Object { [string]$_.table })
    if (($names -join '|') -ne ($script:ExpectedTables -join '|')) { throw 'Reviewed table order drifted.' }
    for ($i=0; $i -lt $steps.Count; $i++) {
        $step=$steps[$i]; Assert-SafeTable ([string]$step.table)
        if ([int]$step.order -ne ($i+1)) { throw "Reviewed order drift: $($step.table)" }
        $ddl=[string]$step.ddl
        if ($ddl -match '(?i)IF\s+NOT\s+EXISTS') { throw "Reviewed DDL contains IF NOT EXISTS: $($step.table)" }
        if ($ddl -notmatch "storage_policy = 'hot_us_only'") { throw "Reviewed DDL missing hot_us_only: $($step.table)" }
        if ((Get-StringSha256 $ddl) -ne ([string]$step.ddl_sha256).ToLowerInvariant()) { throw "Reviewed DDL SHA mismatch: $($step.table)" }
    }
    if ([string]$PlanResult.plan.partial_failure.behavior -ne 'HALT_AND_FREEZE_PARTIAL_EMPTY_SCHEMA_STATE') { throw 'Partial failure behavior drifted.' }
    if ([bool]$PlanResult.plan.partial_failure.auto_drop_allowed -or [bool]$PlanResult.plan.partial_failure.silent_continue_allowed -or [bool]$PlanResult.plan.partial_failure.ordinary_retry_allowed) { throw 'Partial failure safety contract drifted.' }
    if (-not [bool]$PlanResult.plan.partial_failure.remediation_review_required) { throw 'Partial failure remediation review must remain required.' }
    return @($steps)
}

function Get-RequiredAuthorityToken([string]$PlanSha) { return "GO #690 US secondary family target schema apply $PlanSha" }
function Get-AuthorityTokenSha([string]$Token) { return Get-StringSha256 $Token }
function Assert-ReadOnlySelect([string]$Sql,[string]$Label) {
    $normalized=' '+(($Sql -replace '\s+',' ').Trim().ToUpperInvariant())+' '
    if (-not $normalized.TrimStart().StartsWith('SELECT ')) { throw "$Label is not SELECT-only." }
    foreach($token in @(' INSERT ',' UPDATE ',' DELETE ',' CREATE ',' ALTER ',' DROP ',' TRUNCATE ',' OPTIMIZE ',' MOVE ',' ATTACH ',' DETACH ',' RENAME ',' KILL ')) {
        if ($normalized.Contains($token)) { throw "$Label contains forbidden mutation token: $($token.Trim())" }
    }
}
function Convert-JsonLines([string[]]$Lines,[string]$Label) {
    $rows=@(); foreach($line in @($Lines|Where-Object { $_.Trim() })) {
        try { $rows += ($line|ConvertFrom-Json) } catch { throw "$Label returned invalid JSONEachRow: $line" }
    }
    return @($rows)
}
function Get-RunningWslNames {
    $probe=Invoke-NativeText 'wsl.exe' @('--list','--running','--quiet') -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Unable to inspect running WSL distributions.' }
    return @($probe.lines|ForEach-Object { $_.Trim([char]0).Trim() }|Where-Object { $_ })
}
function Assert-TargetRuntimeReady {
    if (@(Get-RunningWslNames) -notcontains $script:TargetDistro) { throw "Target distro $($script:TargetDistro) is not already running; refusing to start it." }
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','pgrep','-f','[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml') -AllowFailure
    $pids=@($probe.lines|Where-Object { $_.Trim() -match '^\d+$' }); if ($probe.exit_code -ne 0 -or $pids.Count -ne 1) { throw 'Accepted target ClickHouse server is not uniquely running.' }
}
function Invoke-TargetRows([string]$Sql,[string]$Label) {
    Assert-ReadOnlySelect $Sql $Label; Assert-TargetRuntimeReady
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$script:TargetDistro,'-u','root','--','clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,'--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if ($probe.exit_code -ne 0) { throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Invoke-StdinProcessText {
    param([string]$FileName,[string]$ArgumentsLine,[string]$InputText,[switch]$AllowFailure)
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$FileName; $psi.Arguments=$ArgumentsLine; $psi.UseShellExecute=$false
    $psi.RedirectStandardInput=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true; $psi.CreateNoWindow=$true
    $process=New-Object System.Diagnostics.Process; $process.StartInfo=$psi
    try {
        if(-not $process.Start()){throw "Unable to start stdin transport process: $FileName"}
        $process.StandardInput.Write($InputText); $process.StandardInput.Close()
        $stdout=$process.StandardOutput.ReadToEnd(); $stderr=$process.StandardError.ReadToEnd(); $process.WaitForExit(); $exitCode=$process.ExitCode
    } finally { $process.Dispose() }
    if(-not $AllowFailure -and $exitCode -ne 0){throw "$FileName stdin process failed with exit code ${exitCode}: $stderr$stdout"}
    return [ordered]@{exit_code=$exitCode;stdout=$stdout;stderr=$stderr}
}
function Invoke-TargetCreate([string]$Ddl,[string]$Table) {
    Assert-SafeTable $Table
    if ($Ddl -notmatch '^CREATE TABLE markorbit_facts\.us_(assignment|ttab)_[a-z0-9_]+') { throw "Unsafe CREATE DDL for $Table" }
    if ($Ddl -match '(?i)IF\s+NOT\s+EXISTS|ALTER|DROP|TRUNCATE|INSERT|OPTIMIZE|MOVE') { throw "Reviewed CREATE contains forbidden fallback/mutation text: $Table" }
    Assert-TargetRuntimeReady
    $arguments="-d $($script:TargetDistro) -u root -- clickhouse client --host $($script:TargetHost) --port $($script:TargetPort)"
    $probe=Invoke-StdinProcessText 'wsl.exe' $arguments $Ddl -AllowFailure
    if ($probe.exit_code -ne 0) { throw "CREATE failed for ${Table}: $($probe.stderr)$($probe.stdout)" }
}
function Invoke-SourceRows([string]$Sql,[string]$Label) {
    Assert-ReadOnlySelect $Sql $Label
    $probe=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure
    if ($probe.exit_code -ne 0) { throw "$Label failed: $($probe.lines -join [Environment]::NewLine)" }
    return @(Convert-JsonLines $probe.lines $Label)
}
function Get-SourceIdentity {
    $quoted=@($script:ExpectedTables|ForEach-Object { "'$_'" }) -join ','
    $tables=@(Invoke-SourceRows "SELECT name, engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database='markorbit_facts' AND name IN ($quoted) ORDER BY name" 'Source schema identity')
    if ($tables.Count -ne 8) { throw "Expected 8 authoritative source tables; observed=$($tables.Count)" }
    $parts=@(Invoke-SourceRows "SELECT table, partition_id, name, rows, bytes_on_disk, disk_name, hash_of_all_files, hash_of_uncompressed_files, uncompressed_hash_of_compressed_files FROM system.parts WHERE database='markorbit_facts' AND active AND table IN ($quoted) ORDER BY table, partition_id, name" 'Source part identity')
    if ($parts.Count -lt 8) { throw 'Authoritative source active parts are unexpectedly sparse.' }
    $lines=@(); foreach($row in $tables){ $lines += "T|$($row.name)|$($row.engine)|$($row.sorting_key)|$($row.primary_key)|$($row.partition_key)|$($row.create_table_query)" }
    foreach($row in $parts){ $lines += "P|$($row.table)|$($row.partition_id)|$($row.name)|$($row.rows)|$($row.bytes_on_disk)|$($row.disk_name)|$($row.hash_of_all_files)|$($row.hash_of_uncompressed_files)|$($row.uncompressed_hash_of_compressed_files)" }
    return [ordered]@{
        sha256=Get-StringSha256 ($lines -join "`n")
        table_count=$tables.Count; active_part_count=$parts.Count
        rows=[int64](($parts|Measure-Object rows -Sum).Sum)
        bytes=[int64](($parts|Measure-Object bytes_on_disk -Sum).Sum)
    }
}
function Get-TargetPreflight([object]$SourceIdentity,[switch]$RequireAllAbsent) {
    $quoted=@($script:ExpectedTables|ForEach-Object { "'$_'" }) -join ','
    $tables=@(Invoke-TargetRows "SELECT name, engine, sorting_key, primary_key, partition_key, storage_policy, create_table_query FROM system.tables WHERE database='markorbit_facts' AND name IN ($quoted) ORDER BY name" 'Target reviewed-table inventory')
    if ($RequireAllAbsent -and $tables.Count -ne 0) { throw "Reviewed target schema is not empty; existing_table_count=$($tables.Count). Remediation review required." }
    $disk=@(Invoke-TargetRows "SELECT name, path, total_space, free_space FROM system.disks WHERE name='$($script:TargetDisk)'" 'Target hot_us disk')
    if ($disk.Count -ne 1) { throw 'Target hot_us disk identity is not unique.' }
    $policy=@(Invoke-TargetRows "SELECT policy_name, volume_name, volume_priority, disks FROM system.storage_policies WHERE policy_name='$($script:TargetPolicy)'" 'Target hot_us_only policy')
    if ($policy.Count -ne 1 -or @($policy[0].disks).Count -ne 1 -or [string]$policy[0].disks[0] -ne $script:TargetDisk) { throw 'hot_us_only -> hot_us policy identity drifted.' }
    $total=[int64]$disk[0].total_space; $free=[int64]$disk[0].free_space
    $floor=[int64][math]::Ceiling([double]$total*0.30); $after=[int64]($free-[int64]$SourceIdentity.bytes)
    if ($after -lt $floor) { throw "Current source footprint no longer fits 30 percent hot_us reserve. after=$after floor=$floor" }
    return [ordered]@{ existing_tables=@($tables); total_space=$total; free_space=$free; source_bytes=[int64]$SourceIdentity.bytes; projected_free_after_equal_byte_copy=$after; recommended_30pct_floor=$floor; recommended_30pct_fits=$true }
}
function Normalize-DdlIdentity([string]$Ddl) {
    $value=(($Ddl -replace '\s+',' ').Trim())
    $value=$value -replace '\s*=\s*','=' -replace '\s*,\s*',',' -replace '\(\s+','(' -replace '\s+\)',')'
    return $value.ToLowerInvariant()
}
function Assert-CreatedTable([object]$Step,[object]$InitialSource) {
    $table=[string]$Step.table; Assert-SafeTable $table
    $rows=@(Invoke-TargetRows "SELECT name, engine, sorting_key, primary_key, partition_key, storage_policy, create_table_query FROM system.tables WHERE database='markorbit_facts' AND name='$table'" "Verify target table $table")
    if ($rows.Count -ne 1) { throw "Created target table identity missing/duplicate: $table" }
    if ([string]$rows[0].storage_policy -ne $script:TargetPolicy) { throw "Target storage policy drift: $table" }
    if ((Normalize-DdlIdentity ([string]$rows[0].create_table_query)) -ne (Normalize-DdlIdentity ([string]$Step.ddl))) { throw "Target DDL identity mismatch after CREATE: $table" }
    $parts=@(Invoke-TargetRows "SELECT count() AS active_parts, coalesce(sum(rows),0) AS rows FROM system.parts WHERE database='markorbit_facts' AND active AND table='$table'" "Verify zero target parts $table")
    if ($parts.Count -ne 1 -or [int64]$parts[0].active_parts -ne 0 -or [int64]$parts[0].rows -ne 0) { throw "New target table is not empty: $table" }
    $sourceNow=Get-SourceIdentity
    if ([string]$sourceNow.sha256 -ne [string]$InitialSource.sha256) { throw "Authoritative source changed during schema apply: $table" }
    return [ordered]@{ table=$table; storage_policy=[string]$rows[0].storage_policy; active_parts=0; rows=0; source_identity_sha256=[string]$sourceNow.sha256; verified_at=(Get-Date).ToUniversalTime().ToString('o') }
}
function Get-JournalPaths([string]$PlanSha) {
    $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){[IO.Path]::GetFullPath($EvidenceRoot)}else{[IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))}
    $dir=Join-Path $root "us_secondary_family_target_schema_apply_$PlanSha"
    return [ordered]@{ directory=$dir; journal=(Join-Path $dir 'apply_journal.json'); success=(Join-Path $dir 'success_receipt.json') }
}
function New-ApplyJournal([object]$Resolved,[object]$InitialSource,[object]$Preflight,[string]$Token) {
    return [ordered]@{
        journal_version=$script:JournalVersion; state='AUTHORIZED_NOT_STARTED'; issue=690
        main_sha=$ExpectedMainSha.Trim().ToLowerInvariant(); plan_sha256=$Resolved.plan_sha256
        review_receipt_sha256=$Resolved.review_sha256; review_receipt_path=$Resolved.review_path
        authority_token_sha256=(Get-AuthorityTokenSha $Token); authority_consumed=$true
        started_at=(Get-Date).ToUniversalTime().ToString('o'); updated_at=(Get-Date).ToUniversalTime().ToString('o')
        source_identity_sha256=$InitialSource.sha256; source_rows=$InitialSource.rows; source_bytes=$InitialSource.bytes
        preflight=$Preflight; created_tables=@(); current_step=$null; last_error=$null
        ordinary_retry_allowed=$false; auto_drop_allowed=$false; next_gate='TARGET_SCHEMA_APPLY_IN_PROGRESS'
    }
}
function Save-Journal([object]$Journal,[string]$Path) { $Journal.updated_at=(Get-Date).ToUniversalTime().ToString('o'); Write-JsonAtomic $Journal $Path }
function Assert-NoExistingJournal([string]$Path) {
    if(Test-Path -LiteralPath $Path -PathType Leaf){ $old=Read-JsonFile $Path 'Existing apply journal'; throw "Schema apply authority is already consumed or frozen. state=$($old.state) journal=$Path" }
}
function Invoke-ContractFixture {
    $sampleSha='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $token=Get-RequiredAuthorityToken $sampleSha
    if($token -ne "GO #690 US secondary family target schema apply $sampleSha"){throw 'Authority token format drifted.'}
    if($token -like 'GO #688*'){throw 'Stale #688 authority format was accepted.'}
    $a="CREATE TABLE markorbit_facts.us_assignment_record_history (`id` String) ENGINE = MergeTree ORDER BY id SETTINGS storage_policy = 'hot_us_only', index_granularity = 8192"
    $b="CREATE   TABLE markorbit_facts.us_assignment_record_history (`id` String) ENGINE = MergeTree ORDER BY id SETTINGS storage_policy='hot_us_only',index_granularity=8192"
    if((Normalize-DdlIdentity $a) -ne (Normalize-DdlIdentity $b)){throw 'DDL canonical-format normalization contract failed.'}
    $stdinSample='CREATE TABLE markorbit_facts.sample (`observation_key` String) ENGINE = MergeTree ORDER BY tuple()'
    $roundTrip=Invoke-StdinProcessText 'more.com' '' $stdinSample
    if($roundTrip.exit_code -ne 0){throw 'stdin transport fixture process failed.'}
    $roundTripText=($roundTrip.stdout -replace "(\r\n|\n)+$",'')
    if($roundTripText -ne $stdinSample){throw 'stdin transport did not preserve backtick DDL text.'}

    $sample=[ordered]@{ plan_sha256=$sampleSha; review_sha256='b'; review_path='fixture'; plan=[ordered]@{} }
    $source=[ordered]@{ sha256='c'; rows=1; bytes=1 }; $pre=[ordered]@{ recommended_30pct_fits=$true }
    $journal=New-ApplyJournal $sample $source $pre $token
    if(-not [bool]$journal.authority_consumed -or [bool]$journal.ordinary_retry_allowed -or [bool]$journal.auto_drop_allowed){throw 'Journal fail-closed contract drifted.'}
    Write-Host 'US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_EXECUTOR_CONTRACT_PASS'
    Write-Host "required_authority_token=$token"
    Write-Host 'schema_apply_executed=False'
}

$script:ActiveJournal=$null
$script:ActiveJournalPath=$null
$script:MutationStarted=$false
try {
    Write-Host '===== US SECONDARY FAMILY TARGET SCHEMA APPLY EXECUTOR ====='
    Write-Host 'source_mutation_authorized=False'
    Write-Host 'data_copy_authorized=False'
    Write-Host 'serving_cutover_authorized=False'
    Write-Host 'auto_drop_authorized=False'
    if($ContractOnly){ Invoke-ContractFixture; exit 0 }
    if($Apply -and $DryRun){ throw 'Choose exactly one of -DryRun or -Apply.' }
    if(-not $Apply -and -not $DryRun){ throw 'Outside ContractOnly, exactly one of -DryRun or -Apply is required.' }
    & git fetch origin main | Out-Host; if($LASTEXITCODE -ne 0){throw 'Unable to fetch origin/main.'}
    Assert-ExactMain 'entry'
    if(-not (Test-Path -LiteralPath (Join-Path $repoRoot '.env') -PathType Leaf)){throw '.env missing; refusing source-runtime inspection.'}
    $resolved=Resolve-ReviewPlan; $steps=@(Assert-ReviewedSteps $resolved)
    $requiredToken=Get-RequiredAuthorityToken $resolved.plan_sha256
    if($Apply -and $AuthorityToken -ne $requiredToken){throw "Authority token mismatch. Exact required token: $requiredToken"}
    if(-not $Apply -and $AuthorityToken){throw 'DryRun must not consume or accept AuthorityToken.'}
    $paths=Get-JournalPaths $resolved.plan_sha256
    if($Apply){ Assert-NoExistingJournal $paths.journal }
    $sourceInitial=Get-SourceIdentity
    $preflight=Get-TargetPreflight $sourceInitial -RequireAllAbsent
    Assert-ExactMain 'post-preflight'
    if($DryRun){
        $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){[IO.Path]::GetFullPath($EvidenceRoot)}else{[IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))}
        $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
        $dir=Join-Path $root "us_secondary_family_target_schema_apply_dryrun_$stamp"; New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $receiptPath=Join-Path $dir 'dry_run_receipt.json'
        $receipt=[ordered]@{
            receipt_version=$script:ReceiptVersion; mode='DRY_RUN'; decision='US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_READY_FOR_EXACT_GO'
            main_sha=$ExpectedMainSha.Trim().ToLowerInvariant(); plan_sha256=$resolved.plan_sha256; review_receipt_sha256=$resolved.review_sha256
            required_authority_token=$requiredToken; authority_consumed=$false; schema_apply_executed=$false; mutation_performed=$false
            source_identity=$sourceInitial; target_preflight=$preflight; step_count=$steps.Count
            partial_failure_behavior='HALT_AND_FREEZE_PARTIAL_EMPTY_SCHEMA_STATE'; next_gate='EXACT_OPERATOR_GO_690_REQUIRED'
        }
        Write-JsonAtomic $receipt $receiptPath; Assert-ExactMain 'dry-run-exit'
        Write-Host 'decision=US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_READY_FOR_EXACT_GO'
        Write-Host "plan_sha256=$($resolved.plan_sha256)"; Write-Host "required_authority_token=$requiredToken"
        Write-Host 'authority_consumed=False'; Write-Host 'schema_apply_executed=False'; Write-Host "receipt_path=$receiptPath"
        exit 0
    }
    $script:ActiveJournal=New-ApplyJournal $resolved $sourceInitial $preflight $AuthorityToken
    $script:ActiveJournalPath=$paths.journal
    Save-Journal $script:ActiveJournal $script:ActiveJournalPath
    Write-Host "authority_consumed=True"
    Write-Host "journal_path=$script:ActiveJournalPath"

    foreach($step in $steps){
        Assert-ExactMain "pre-create-$($step.order)"
        $table=[string]$step.table
        $existing=@(Invoke-TargetRows "SELECT name FROM system.tables WHERE database='markorbit_facts' AND name='$table'" "Pre-create absence $table")
        if($existing.Count -ne 0){throw "Target table appeared before reviewed CREATE: $table"}
        $sourceNow=Get-SourceIdentity; if([string]$sourceNow.sha256 -ne [string]$sourceInitial.sha256){throw "Source identity drift before CREATE: $table"}
        $script:ActiveJournal.state='CREATE_INTENT'; $script:ActiveJournal.current_step=[ordered]@{order=[int]$step.order;table=$table;ddl_sha256=[string]$step.ddl_sha256;intent_at=(Get-Date).ToUniversalTime().ToString('o')}
        Save-Journal $script:ActiveJournal $script:ActiveJournalPath
        $script:MutationStarted=$true
        Invoke-TargetCreate ([string]$step.ddl) $table
        $verified=Assert-CreatedTable $step $sourceInitial
        $script:ActiveJournal.created_tables=@($script:ActiveJournal.created_tables)+@([ordered]@{order=[int]$step.order;table=$table;ddl_sha256=[string]$step.ddl_sha256;verification=$verified})
        $script:ActiveJournal.state='APPLYING'; $script:ActiveJournal.current_step=$null
        Save-Journal $script:ActiveJournal $script:ActiveJournalPath
        Write-Host "created_and_verified=$($step.order)|$table"
    }
    Assert-ExactMain 'pre-final-audit'
    $quoted=@($script:ExpectedTables|ForEach-Object { "'$_'" }) -join ','
    $finalTables=@(Invoke-TargetRows "SELECT name, storage_policy FROM system.tables WHERE database='markorbit_facts' AND name IN ($quoted) ORDER BY name" 'Final target schema inventory')
    if($finalTables.Count -ne 8){throw "Final target schema count mismatch: $($finalTables.Count)"}
    if(@($finalTables|Where-Object {[string]$_.storage_policy -ne $script:TargetPolicy}).Count -ne 0){throw 'Final target storage policy mismatch.'}
    $finalParts=@(Invoke-TargetRows "SELECT count() AS active_parts, coalesce(sum(rows),0) AS rows FROM system.parts WHERE database='markorbit_facts' AND active AND table IN ($quoted)" 'Final target empty-parts audit')
    if($finalParts.Count -ne 1 -or [int64]$finalParts[0].active_parts -ne 0 -or [int64]$finalParts[0].rows -ne 0){throw 'Final target schemas are not empty.'}
    $sourceFinal=Get-SourceIdentity; if([string]$sourceFinal.sha256 -ne [string]$sourceInitial.sha256){throw 'Authoritative source changed during schema apply.'}
    $finalCapacity=Get-TargetPreflight $sourceFinal
    Assert-ExactMain 'final-audit-complete'

    $script:ActiveJournal.state='SUCCESS'; $script:ActiveJournal.current_step=$null; $script:ActiveJournal.completed_at=(Get-Date).ToUniversalTime().ToString('o')
    $script:ActiveJournal.next_gate='TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT'; $script:ActiveJournal.final_source_identity_sha256=$sourceFinal.sha256
    Save-Journal $script:ActiveJournal $script:ActiveJournalPath
    $success=[ordered]@{receipt_version=$script:ReceiptVersion;decision='US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_SUCCESS';main_sha=$ExpectedMainSha.ToLowerInvariant();plan_sha256=$resolved.plan_sha256;review_receipt_sha256=$resolved.review_sha256;authority_consumed=$true;schema_apply_executed=$true;created_table_count=8;target_active_parts=0;source_unchanged=$true;target_preflight_after=$finalCapacity;journal_path=$script:ActiveJournalPath;next_gate='TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT'}
    Write-JsonAtomic $success $paths.success; Assert-ExactMain 'success-exit'
    Write-Host 'decision=US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_SUCCESS'; Write-Host "plan_sha256=$($resolved.plan_sha256)"; Write-Host 'created_table_count=8'; Write-Host 'target_active_parts=0'; Write-Host 'source_unchanged=True'; Write-Host 'next_gate=TARGET_TO_SOURCE_CONNECTIVITY_PREFLIGHT'; Write-Host "receipt_path=$($paths.success)"
    exit 0
}
catch {
    $message=$_.Exception.Message
    if($script:ActiveJournal -and $script:ActiveJournalPath -and (Test-Path -LiteralPath $script:ActiveJournalPath -PathType Leaf)){
        try {
            $observed=@()
            try {
                $quoted=@($script:ExpectedTables|ForEach-Object { "'$_'" }) -join ','
                $rows=@(Invoke-TargetRows "SELECT name, storage_policy FROM system.tables WHERE database='markorbit_facts' AND name IN ($quoted) ORDER BY name" 'Failure-state target inventory')
                $observed=@($rows|ForEach-Object {[ordered]@{table=[string]$_.name;storage_policy=[string]$_.storage_policy}})
            } catch { $observed=@([ordered]@{inventory_error=$_.Exception.Message}) }
            $script:ActiveJournal.state='PARTIAL_FAILURE_FROZEN'; $script:ActiveJournal.last_error=$message
            $script:ActiveJournal.partial_state_observed_at=(Get-Date).ToUniversalTime().ToString('o'); $script:ActiveJournal.target_tables_observed=@($observed)
            $script:ActiveJournal.ordinary_retry_allowed=$false; $script:ActiveJournal.auto_drop_allowed=$false
            $script:ActiveJournal.next_gate='TARGET_SCHEMA_PARTIAL_STATE_REMEDIATION_REVIEW'
            Save-Journal $script:ActiveJournal $script:ActiveJournalPath
        } catch { Write-Host "journal_freeze_error=$($_.Exception.Message)" }
    }
    Write-Host "US_SECONDARY_FAMILY_TARGET_SCHEMA_APPLY_FAILED: $message"
    if($script:ActiveJournalPath){Write-Host "journal_path=$script:ActiveJournalPath"}
    exit 2
}
finally { Pop-Location }
