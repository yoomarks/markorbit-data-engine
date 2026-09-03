[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedMainSha,
    [Parameter(Mandatory=$true)][string]$AuthorityReviewReceiptPath,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedAuthorityReviewReceiptSha256,
    [Parameter(Mandatory=$true)][string]$IncidentEvidenceDirectory,
    [Parameter(Mandatory=$true)][string]$OperatorGoToken,
    [string]$ToolingDistro='Ubuntu-24.04',
    [string]$AcceptedVolume='markorbit-data-engine_clickhouse_data',
    [switch]$Apply,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:IncidentEngineSha='111908335714292ae4d42e54b3664156d19d64ca'
$script:IncidentJournalVersion='PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_JOURNAL_V1'
$script:RemediationReceiptVersion='PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_V1'
$script:PhaseAReceiptVersion='PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_V2_REMEDIATED'
$script:OperatorGoIssue=506
$script:OperatorGoCommentId='5521853975'
$script:RemediationIssue=508
$script:ExpectedOperatorGoToken='PHASE_A_CN_WARM_PROVISIONING_GO_ISSUE_506_COMMENT_5521853975'
$script:ExpectedIncidentError='Production runtime cannot see named Warm ext4 mount.'
$script:ExpectedIncidentEvidenceDirectory='D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_phase_a_provisioning_20260903_072812'
$script:ExpectedReviewEngineSha='4be4ef8615ed16ff8e3aafb962b476fe2605f5ef'
$script:ExpectedReviewVersion='PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_V1'
$script:ExpectedDesignReceiptSha256='07a7af0bff5b97379c1a5203059f456746f789914040da8c037a37b755cfd837'
$script:ExpectedChecksumReceiptSha256='ddd17889b5d7f513515fc7b3e53b1e697e5671ddcd49b7409b5a877e59c587f0'
$script:ExpectedWarmManifestSha256='716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231'
$script:ExpectedChecksumManifestSha256='4aa3ae5f0d9b8c903b6275ea9a341a9b66f20843c19139a4a8355ca07e38d41a'
$script:ExpectedWarmRows=[int64]2430570761
$script:ExpectedWarmBytes=[int64]562600035674
$script:ExpectedWarmCandidateCount=[int64]4
$script:ExpectedETotalBytes=[int64]2048391114752
$script:ExpectedWarmVhdxPath='E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ExpectedWarmVhdxMaxBytes=[int64]842887331840
$script:ExpectedWarmMountName='markorbit_prod_warm_cn'
$script:ExpectedWarmMountPath='/mnt/wsl/markorbit_prod_warm_cn'
$script:ExpectedWarmDiskPath='/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/'
$script:ExpectedRuntimeDistro='MarkOrbit-ClickHouse'
$script:ExpectedRuntimeRoot='D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse'
$script:ExpectedClickHouseVersion='24.8.14.39'
$script:TargetHttpPort=28123
$script:TargetNativePort=29000
$script:RuntimeInstallDir='/opt/markorbit-clickhouse-production'
$script:RuntimeDataDir='/var/lib/markorbit-clickhouse-production'
$script:ExpectedFRecoveryVhdx='F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes=[int64]961542094848
$script:EBackupRoot='E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery'
$script:ExpectedDesignReceiptPath='D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_migration_design_20260903_043222\production_cn_warm_migration_design.json'
$script:ExpectedChecksumReceiptPath='D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_source_logical_checksum_v2_20260903_052306\production_cn_warm_source_logical_checksum_v2.json'
$script:AllowedRemediationFiles=@(
    'scripts/resume-production-cn-warm-phase-a-mount-remediation.ps1',
    'tests/test_production_cn_warm_phase_a_mount_remediation_contract.py',
    '.github/workflows/production-cn-warm-phase-a-mount-remediation-runtime.yml'
)
$script:ProtectedPaths=@(
    'D:\MarkOrbitData\spike\hot_cn_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_us_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_global_spike.vhdx',
    'E:\MarkOrbitData\spike\warm_spike.vhdx',
    'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike\ext4.vhdx',
    'E:\MarkOrbitData\wsl-tooling\Ubuntu-24.04\ext4.vhdx',
    $script:ExpectedFRecoveryVhdx
)
$script:ExpectedSourceIdentities=@(
    [ordered]@{table='cn_observed_event'; rows=[int64]413031435; bytes=[int64]127856495167; parts=[int64]11; sha='59118b96ccd4e6ba728b36670becf6d45bc85eb007b8f9cffd2fcfd590dd63ab'},
    [ordered]@{table='cn_goods_scope_lifecycle_current'; rows=[int64]158355910; bytes=[int64]4696234780; parts=[int64]11; sha='4ad4dbfc7b8527ea512ffca5b79dcf9c381e8b7fe45a750e0ac999be3dac862a'},
    [ordered]@{table='cn_goods_item_observation'; rows=[int64]219463289; bytes=[int64]58772877234; parts=[int64]14; sha='c591139333260615687087caddcd9cc91378785d64658d2796218170e6279776'},
    [ordered]@{table='cn_goods_item_current'; rows=[int64]1639720127; bytes=[int64]371274428493; parts=[int64]10; sha='5c1bf56661de5fbdb7cbfb4f3c9d0f797d7f23b5a55c9922e299fdbf6bf5eae3'}
)

function Invoke-NativeText {
    param([Parameter(Mandatory=$true)][string]$Command,[Parameter(Mandatory=$true)][AllowEmptyCollection()][string[]]$Arguments,[switch]$AllowFailure)
    $old=$ErrorActionPreference
    try { $ErrorActionPreference='Continue'; $out=@(& $Command @Arguments 2>&1); $code=$LASTEXITCODE }
    finally { $ErrorActionPreference=$old }
    $lines=@($out|ForEach-Object {$_.ToString()})
    if (-not $AllowFailure -and $code -ne 0) { throw "$Command failed with exit code ${code}: $($lines -join [Environment]::NewLine)" }
    return [ordered]@{exit_code=$code;lines=@($lines)}
}
function Get-StringSha256([string]$Text) {
    $h=[System.Security.Cryptography.SHA256]::Create(); try { $b=[System.Text.Encoding]::UTF8.GetBytes($Text); return ([BitConverter]::ToString($h.ComputeHash($b))).Replace('-','').ToLowerInvariant() } finally {$h.Dispose()}
}
function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Read-Json([string]$Path,[string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}
function Write-Json([object]$Value,[string]$Path) { $Value|ConvertTo-Json -Depth 60|Set-Content -LiteralPath $Path -Encoding UTF8 }
function Write-Utf8NoBom([string]$Path,[string]$Content) { $e=New-Object System.Text.UTF8Encoding($false); [IO.File]::WriteAllText($Path,$Content,$e) }
function Normalize-WindowsPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {return ''}; $p=$Path.Trim(); if ($p.StartsWith('\\?\')){$p=$p.Substring(4)}; if ($p.StartsWith('\??\')){$p=$p.Substring(4)}; if ($p -notmatch '^[A-Za-z]:[\\/]'){return ''}; return [IO.Path]::GetFullPath($p).TrimEnd('\')
}
function Test-SameWindowsPath([string]$A,[string]$B) { return (Normalize-WindowsPath $A).ToLowerInvariant() -eq (Normalize-WindowsPath $B).ToLowerInvariant() }
function Convert-WindowsPathToWsl([string]$Path) { $p=Normalize-WindowsPath $Path; if ($p -notmatch '^([A-Za-z]):\\(.*)$'){throw "Cannot convert path: $Path"}; return "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2].Replace('\','/'))" }
function Escape-Sql([string]$Value) {return $Value.Replace("'","''")}

function Assert-ExactMain([string]$Phase) {
    $expected=$ExpectedMainSha.Trim().ToLowerInvariant(); $head=(git rev-parse HEAD).Trim().ToLowerInvariant(); $origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"; Write-Host "HEAD=$head"; Write-Host "origin/main=$origin"; Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected){throw "Exact main drift during $Phase."}; if (git status --porcelain){throw "Working tree not clean during $Phase."}
}
function Assert-RemediationProvenance {
    $ancestor=Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:IncidentEngineSha,$ExpectedMainSha) -AllowFailure; if($ancestor.exit_code-ne 0){throw 'Incident engine is not ancestor of remediation main.'}
    $d=Invoke-NativeText 'git' @('diff','--name-only',"$($script:IncidentEngineSha)..$ExpectedMainSha"); $changed=@($d.lines|Where-Object{$_.Trim()}|ForEach-Object{$_.Trim().Replace('\','/')}); $unexpected=@($changed|Where-Object{$_ -notin $script:AllowedRemediationFiles}); $missing=@($script:AllowedRemediationFiles|Where-Object{$_ -notin $changed})
    Write-Host "incident_to_current_changed_file_count=$($changed.Count)"; Write-Host "incident_to_current_unexpected_changed_file_count=$($unexpected.Count)"; Write-Host "incident_to_current_missing_remediation_file_count=$($missing.Count)"
    if($changed.Count-ne 3 -or $unexpected.Count-ne 0 -or $missing.Count-ne 0){throw 'Remediation tooling changed outside exact 3-file boundary.'}
}
function Get-WslDistros {
    $root='HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'; if(-not(Test-Path -LiteralPath $root)){return @()}; $rows=@(); foreach($k in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)){ $i=Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue; if($i -and $i.DistributionName){$rows += [ordered]@{name=[string]$i.DistributionName;version=if($null-ne$i.Version){[int]$i.Version}else{$null};base_path=if($i.BasePath){Normalize-WindowsPath([string]$i.BasePath)}else{''}}}}; return @($rows)
}
function Get-DefaultWslDistro { $root='HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'; $i=Get-ItemProperty -LiteralPath $root -ErrorAction SilentlyContinue; if(-not$i -or -not$i.DefaultDistribution){return $null}; $d=Get-ItemProperty -LiteralPath (Join-Path $root ([string]$i.DefaultDistribution)) -ErrorAction SilentlyContinue; if($d -and $d.DistributionName){return [string]$d.DistributionName}; return $null }
function Get-MountProbe([string]$Distro,[string]$MountName) { $t="/mnt/wsl/$MountName"; $p=Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$t) -AllowFailure; $x=(@($p.lines)-join ' ').Trim(); return [ordered]@{ready=[bool]($p.exit_code-eq 0 -and $x-match '^ext4\s');exit_code=$p.exit_code;target=$t;output=$x} }
function Invoke-RuntimeText([string[]]$Arguments,[switch]$AllowFailure){return Invoke-NativeText 'wsl.exe' (@('-d',$script:ExpectedRuntimeDistro,'-u','root','--')+$Arguments) -AllowFailure:$AllowFailure}
function Invoke-RuntimeShell([string]$Command,[switch]$AllowFailure){return Invoke-RuntimeText @('sh','-lc',$Command) -AllowFailure:$AllowFailure}
function Invoke-TargetSql([string]$Query,[switch]$AllowFailure){return Invoke-RuntimeText @('clickhouse','client','--host','127.0.0.1','--port',$script:TargetNativePort.ToString(),'--query',$Query) -AllowFailure:$AllowFailure}
function Test-Port([int]$Port){if(-not(Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)){return $false};return [bool](@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count-gt 0)}
function Dismount-ExactWarmVhdx { $p=Invoke-NativeText 'wsl.exe' @('--unmount',$script:ExpectedWarmVhdxPath) -AllowFailure; return [bool]($p.exit_code-eq 0) }

function Assert-RawConsumersStopped {
    $total=0; foreach($s in @('api','worker','mark-image-worker','qcc-acquisition')){ $p=Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$s) -AllowFailure; if($p.exit_code-ne 0){throw "Unable to inspect $s"}; $running=0; foreach($cid in @($p.lines|Where-Object{$_.Trim()})){ $q=Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$cid.Trim()) -AllowFailure; if($q.exit_code-ne 0){throw "Unable to inspect $s state"}; if(((@($q.lines)-join '').Trim().ToLowerInvariant())-eq'true'){$running++}}; $total+=$running; Write-Host "raw_consumer_service=$s running_count=$running"}; Write-Host "running_raw_consumer_count=$total"; if($total-ne 0){throw 'Consumers must remain stopped.'}
}
function Get-SourceHealth {
    $p=Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure; $cid=(@($p.lines)-join '').Trim(); if(-not$cid){return [ordered]@{ready=$false}}
    $h=Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$cid) -AllowFailure; $one=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure; $ver=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure
    $ready=[bool]($h.exit_code-eq 0 -and ((@($h.lines)-join '').Trim()-eq'healthy' -and $one.exit_code-eq 0 -and ((@($one.lines)-join '').Trim()-eq'1' -and $ver.exit_code-eq 0 -and ((@($ver.lines)-join '').Trim()-eq$script:ExpectedClickHouseVersion)))
    if($ready){$m=Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$cid); $mounts=(@($m.lines)-join "`n")|ConvertFrom-Json; $x=@($mounts|Where-Object{[string]$_.Destination-eq'/var/lib/clickhouse'}); $ready=[bool]($x.Count-eq 1 -and [string]$x[0].Type-eq'volume' -and [string]$x[0].Name-eq$AcceptedVolume)}
    Write-Host "accepted_production_mount_ready=$ready"; if(-not$ready){throw 'Source ClickHouse/accepted volume not ready.'}; return [ordered]@{ready=$true;container_id=$cid}
}
function Invoke-ChJson([string]$Sql){$p=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql+' FORMAT JSONEachRow')); $rows=@(); foreach($l in @($p.lines|Where-Object{$_.Trim()})){$rows+=($l|ConvertFrom-Json)}; return @($rows)}
function Get-SchemaSha([object]$r){return Get-StringSha256(@([string]$r.table,[string]$r.engine,[string]$r.sorting_key,[string]$r.primary_key,[string]$r.partition_key,[string]$r.create_table_query)-join"`n")}
function Get-PartSha([object[]]$ps){$a=@();foreach($p in @($ps|Sort-Object name)){$a+=@([string]$p.name,[string]$p.rows,[string]$p.bytes_on_disk,[string]$p.hash_of_all_files,[string]$p.hash_of_uncompressed_files,[string]$p.uncompressed_hash_of_compressed_files)-join'|'};return Get-StringSha256($a-join"`n")}
function Get-ResidencySha([object[]]$ps){$a=@();foreach($p in @($ps|Sort-Object name)){$a+="$([string]$p.name)|$([string]$p.disk_name)"};return Get-StringSha256($a-join"`n")}
function Get-SourceIdentity([object]$plan){$t=[string]$plan.table;$lit=Escape-Sql$t;$tr=@(Invoke-ChJson "SELECT name AS table,engine,sorting_key,primary_key,partition_key,create_table_query FROM system.tables WHERE database='markorbit_facts' AND name='$lit'");if($tr.Count-ne 1){throw "Source table missing: $t"};$ps=@(Invoke-ChJson "SELECT partition_id,name,rows,bytes_on_disk,disk_name,hash_of_all_files,hash_of_uncompressed_files,uncompressed_hash_of_compressed_files FROM system.parts WHERE database='markorbit_facts' AND active AND table='$lit' ORDER BY partition_id,name");$rows=[int64]0;$bytes=[int64]0;foreach($p in$ps){$rows+=[int64]$p.rows;$bytes+=[int64]$p.bytes_on_disk};$sha=Get-StringSha256(@($t,'all',(Get-SchemaSha$tr[0]),$rows,$bytes,[int64]$ps.Count,(Get-PartSha$ps),(Get-ResidencySha$ps),$rows,$bytes,[int64]$ps.Count,(Get-PartSha$ps),(Get-ResidencySha$ps),[string]$plan.source_disk)-join"`n");return [ordered]@{rows=$rows;bytes=$bytes;parts=[int64]$ps.Count;sha=$sha}}

function Resolve-Authority {
    $path=[IO.Path]::GetFullPath($AuthorityReviewReceiptPath);$sha=Get-FileSha256$path;if($sha-ne$ExpectedAuthorityReviewReceiptSha256.Trim().ToLowerInvariant()){throw 'Authority review SHA mismatch.'};$r=Read-Json$path 'Authority review';if([string]$r.receipt_version-ne$script:ExpectedReviewVersion -or [string]$r.engine_sha-ne$script:ExpectedReviewEngineSha){throw 'Authority review identity drift.'};if([string]$r.decision-ne'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_READY_FOR_OPERATOR_GO' -or @($r.blockers).Count-ne 0){throw 'Authority review not READY.'};if([string]$r.accepted_design.receipt_sha256-ne$script:ExpectedDesignReceiptSha256 -or [string]$r.accepted_design.warm_candidate_manifest_sha256-ne$script:ExpectedWarmManifestSha256){throw 'Design provenance drift.'};if([string]$r.accepted_checksum.receipt_sha256-ne$script:ExpectedChecksumReceiptSha256 -or [string]$r.accepted_checksum.checksum_manifest_sha256-ne$script:ExpectedChecksumManifestSha256){throw 'Checksum provenance drift.'};if([int64]$r.frozen_workload.rows-ne$script:ExpectedWarmRows -or [int64]$r.frozen_workload.bytes-ne$script:ExpectedWarmBytes -or [int64]$r.frozen_workload.candidate_count-ne$script:ExpectedWarmCandidateCount){throw 'Frozen workload drift.'};$dp=[string]$r.accepted_design.receipt_path;if(-not(Test-SameWindowsPath$dp$script:ExpectedDesignReceiptPath)-or(Get-FileSha256$dp)-ne$script:ExpectedDesignReceiptSha256){throw 'Design receipt file drift.'};$cp=[string]$r.accepted_checksum.receipt_path;if(-not(Test-SameWindowsPath$cp$script:ExpectedChecksumReceiptPath)-or(Get-FileSha256$cp)-ne$script:ExpectedChecksumReceiptSha256){throw 'Checksum receipt file drift.'};return [ordered]@{path=$path;sha=$sha;receipt=$r;design=(Read-Json$dp 'Design receipt')}
}
function Assert-LiveSource([object]$a){$plans=@($a.design.candidates|Sort-Object migration_order);if($plans.Count-ne 4){throw 'Design candidates drift.'};foreach($p in$plans){$live=Get-SourceIdentity$p;$e=@($script:ExpectedSourceIdentities|Where-Object{$_.table-eq[string]$p.table});if($e.Count-ne1 -or $live.rows-ne$e[0].rows -or $live.bytes-ne$e[0].bytes -or $live.parts-ne$e[0].parts -or $live.sha-ne$e[0].sha){throw "SOURCE_IDENTITY_DRIFT:$($p.table)"};Write-Host "source_identity_ready=$($p.table)|all|rows=$($live.rows)|parts=$($live.parts)|source_identity_sha256=$($live.sha)"}}
function Assert-Capacity { $d=New-Object IO.DriveInfo('E:\');if(-not$d.IsReady-or[int64]$d.TotalSize-ne$script:ExpectedETotalBytes){throw 'E capacity identity drift.'};$reserve=[int64][math]::Ceiling([double]$d.TotalSize*0.30);$margin=[int64]($d.AvailableFreeSpace-$script:ExpectedWarmVhdxMaxBytes-$reserve);Write-Host "e_total_bytes=$($d.TotalSize)";Write-Host "e_free_bytes=$($d.AvailableFreeSpace)";Write-Host "e_margin_after_proposed_max_bytes=$margin";if($margin-lt0){throw 'E_30_PERCENT_RESERVE_ADMISSION_FAILED'}}
function Assert-Protected {foreach($p in$script:ProtectedPaths){if(-not(Test-Path -LiteralPath$p -PathType Leaf)){throw "Protected path missing: $p"}};$f=New-Object IO.FileInfo($script:ExpectedFRecoveryVhdx);if([int64]$f.Length-ne$script:ExpectedFRecoveryBytes){throw 'F recovery VHDX changed.'};if(Test-Path -LiteralPath$script:EBackupRoot){throw 'Superseded E backup root reappeared.'}}
function Get-MountUuid([string]$Distro){$cmd="src=`$(findmnt -n -o SOURCE '$($script:ExpectedWarmMountPath)') || exit 10; blkid -s UUID -o value `"`$src`"";$p=Invoke-NativeText'wsl.exe'@('-d',$Distro,'-u','root','--','sh','-lc',$cmd)-AllowFailure;if($p.exit_code-ne0){return ''};return(@($p.lines)-join'').Trim()}
function Assert-WarmEmpty([string]$Distro){$cmd="test -d '$($script:ExpectedWarmDiskPath)' || exit 10; if find '$($script:ExpectedWarmDiskPath)' -mindepth 1 -print -quit | grep -q .; then exit 20; fi";$p=Invoke-NativeText'wsl.exe'@('-d',$Distro,'-u','root','--','sh','-lc',$cmd)-AllowFailure;if($p.exit_code-ne0){throw 'Warm clickhouse-data is not empty.'}}

function Resolve-Incident([object]$Authority){
    $dir=[IO.Path]::GetFullPath($IncidentEvidenceDirectory);if(-not(Test-SameWindowsPath$dir$script:ExpectedIncidentEvidenceDirectory)){throw 'Incident evidence directory mismatch.'};if(-not(Test-Path -LiteralPath$dir -PathType Container)){throw 'Incident evidence directory missing.'};$jp=Join-Path$dir'production_cn_warm_phase_a_provisioning_journal.json';$jsha=Get-FileSha256$jp;$j=Read-Json$jp 'Incident journal'
    if([string]$j.receipt_version-ne$script:IncidentJournalVersion -or [string]$j.engine_sha-ne$script:IncidentEngineSha -or [string]$j.authority_review_sha256-ne$Authority.sha -or [string]$j.operator_go_comment_id-ne$script:OperatorGoCommentId -or [string]$j.operator_go_token_sha256-ne(Get-StringSha256$script:ExpectedOperatorGoToken)){throw 'Incident journal provenance mismatch.'}
    if([string]$j.stage-ne'blocked' -or [string]$j.last_error-ne$script:ExpectedIncidentError){throw 'Incident journal is not exact mount-visibility failure.'}
    foreach($n in @('vhdx_create_started','vhdx_created','ext4_format_started','ext4_formatted','named_mount_started','named_mount_ready','tooling_export_started','tooling_exported','runtime_import_started')){if(-not[bool]$j.$n){throw "Incident journal prerequisite false: $n"}}
    foreach($n in @('runtime_imported','clickhouse_install_started','clickhouse_installed','config_write_started','config_written','server_start_started','server_started','storage_foundation_ready','completed','cn_data_transfer_performed','cross_runtime_transfer_performed','cn_warm_move_performed','source_cleanup_performed','accepted_volume_mutation_performed','source_clickhouse_mutation_performed','docker_mutation_performed','cn_replay_performed','us_bulk_performed','no_arg_wsl_unmount_performed','wsl_shutdown_performed','runtime_distro_unregister_performed','target_vhdx_delete_performed')){if([bool]$j.$n){throw "Incident journal forbidden/unexpected flag true: $n"}}
    if([string]::IsNullOrWhiteSpace([string]$j.ext4_uuid)){throw 'Incident ext4 UUID missing.'};$tar=Join-Path$dir'tooling-rootfs.tar';if((Get-FileSha256$tar)-ne[string]$j.tooling_export_tar_sha256){throw 'Incident tooling export SHA drift.'};return [ordered]@{dir=$dir;journal_path=$jp;journal_sha256=$jsha;journal=$j}
}
function Assert-IncidentPhysical([object]$Incident){
    if(-not(Test-Path -LiteralPath$script:ExpectedWarmVhdxPath -PathType Leaf)){throw 'Incident Warm VHDX missing.'};$ds=@(Get-WslDistros);$r=@($ds|Where-Object{$_.name-eq$script:ExpectedRuntimeDistro});if($r.Count-ne1 -or $r[0].version-ne2 -or -not(Test-SameWindowsPath$r[0].base_path$script:ExpectedRuntimeRoot)){throw 'Incident runtime identity mismatch.'};$rp=Invoke-RuntimeShell'printf RUNTIME_OK'-AllowFailure;if($rp.exit_code-ne0 -or ((@($rp.lines)-join'').Trim()-ne'RUNTIME_OK')){throw 'Incident runtime cannot start.'};$tm=Get-MountProbe$ToolingDistro$script:ExpectedWarmMountName;$rm=Get-MountProbe$script:ExpectedRuntimeDistro$script:ExpectedWarmMountName;if(-not$tm.ready){throw 'Incident tooling mount is not ready.'};if($rm.ready){throw 'Incident mount visibility failure is no longer present; refusing blind remediation.'};$uuid=Get-MountUuid$ToolingDistro;if($uuid-ne[string]$Incident.journal.ext4_uuid){throw 'Incident ext4 UUID mismatch.'};Assert-WarmEmpty$ToolingDistro;if(Test-Port$script:TargetHttpPort -or Test-Port$script:TargetNativePort){throw 'Target port collision before remediation.'};Write-Host "incident_ext4_uuid=$uuid";Write-Host 'incident_partial_state_ready=True'
}
function Invoke-ContractFixture {if($script:IncidentEngineSha-ne'111908335714292ae4d42e54b3664156d19d64ca'){throw 'Incident engine contract drift.'};if($script:ExpectedIncidentError-ne'Production runtime cannot see named Warm ext4 mount.'){throw 'Incident error contract drift.'};if($script:AllowedRemediationFiles.Count-ne3){throw 'Remediation file boundary drift.'};Write-Host 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_CONTRACT_OK'}

$remediationJournal=$null;$remediationJournalPath=$null;$incident=$null
try {
    Write-Host '===== PRODUCTION CN WARM PHASE A MOUNT REMEDIATION =====';Write-Host "remediation_issue=$script:RemediationIssue";Write-Host "incident_engine_sha=$script:IncidentEngineSha";Write-Host "apply_requested=$([bool]$Apply)"
    foreach($m in @('fresh_provisioning_authorized=False','vhdx_create_authorized=False','vhdx_format_authorized=False','vhdx_delete_authorized=False','runtime_import_authorized=False','runtime_unregister_authorized=False','wsl_shutdown_authorized=False','no_arg_wsl_unmount_authorized=False','docker_mutation_authorized=False','accepted_volume_mutation_authorized=False','source_clickhouse_mutation_authorized=False','cn_data_transfer_authorized=False','cross_runtime_transfer_authorized=False','cn_warm_move_authorized=False','source_cleanup_authorized=False','cn_replay_authorized=False','us_bulk_authorized=False')){Write-Host$m}
    if($ContractOnly){Invoke-ContractFixture;exit 0};if($OperatorGoToken-ne$script:ExpectedOperatorGoToken){throw 'Original Phase A GO token mismatch.'};if((git branch --show-current).Trim()-ne'main'){throw 'Remediation must run from local main.'};&git fetch origin main|Out-Host;if($LASTEXITCODE-ne0){throw 'Unable to fetch origin/main.'};Assert-ExactMain'entry';Assert-RemediationProvenance
    $authority=Resolve-Authority;Assert-RawConsumersStopped;[void](Get-SourceHealth);Assert-LiveSource$authority;Assert-Protected;Assert-Capacity;$defaultBefore=Get-DefaultWslDistro;$env=Join-Path$repoRoot'.env';$envSha=Get-FileSha256$env;$incident=Resolve-Incident$authority;Assert-IncidentPhysical$incident
    $remediationJournalPath=Join-Path$incident.dir'production_cn_warm_phase_a_mount_remediation_journal.json';if(Test-Path -LiteralPath$remediationJournalPath){throw 'Remediation journal already exists; this operator is single-attempt fail-closed.'}
    $remediationJournal=[ordered]@{receipt_version=$script:RemediationReceiptVersion;engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant();incident_engine_sha=$script:IncidentEngineSha;incident_journal_path=$incident.journal_path;incident_journal_sha256=$incident.journal_sha256;authority_review_sha256=$authority.sha;stage='ready';exact_path_unmount_performed=$false;named_remount_performed=$false;tooling_mount_ready=$false;runtime_mount_ready=$false;ext4_uuid_verified=$false;warm_empty_verified=$false;clickhouse_installed=$false;config_written=$false;server_started=$false;storage_foundation_ready=$false;completed=$false;last_error=$null;no_arg_wsl_unmount_performed=$false;wsl_shutdown_performed=$false;runtime_distro_unregister_performed=$false;target_vhdx_delete_performed=$false;vhdx_create_performed=$false;vhdx_format_performed=$false;runtime_import_performed=$false;docker_mutation_performed=$false;accepted_volume_mutation_performed=$false;source_clickhouse_mutation_performed=$false;cn_data_transfer_performed=$false;cross_runtime_transfer_performed=$false;cn_warm_move_performed=$false;source_cleanup_performed=$false;cn_replay_performed=$false;us_bulk_performed=$false};Write-Json$remediationJournal$remediationJournalPath
    Write-Host "Incident evidence directory: $($incident.dir)";Write-Host "incident_journal_sha256=$($incident.journal_sha256)";Write-Host "remediation_journal_path=$remediationJournalPath"
    if(-not$Apply){Write-Host 'decision=PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_READY_FOR_APPLY';Write-Host 'mutation_performed=False';exit 0}
    $admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent());if(-not$admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Remediation Apply requires Administrator PowerShell.'}

    $remediationJournal.stage='exact_path_remount';Write-Json$remediationJournal$remediationJournalPath
    if(-not(Dismount-ExactWarmVhdx)){throw 'Exact-path Warm VHDX unmount failed.'};$remediationJournal.exact_path_unmount_performed=$true;Write-Json$remediationJournal$remediationJournalPath;Start-Sleep -Seconds 1
    $tm0=Get-MountProbe$ToolingDistro$script:ExpectedWarmMountName;$rm0=Get-MountProbe$script:ExpectedRuntimeDistro$script:ExpectedWarmMountName;if($tm0.ready -or $rm0.ready){throw 'Warm mount still visible after exact-path unmount.'}
    $rp=Invoke-RuntimeShell'printf RUNTIME_OK'-AllowFailure;if($rp.exit_code-ne0){throw 'Existing production runtime did not remain startable.'}
    $mount=Invoke-NativeText'wsl.exe'@('--mount','--vhd',$script:ExpectedWarmVhdxPath,'--name',$script:ExpectedWarmMountName)-AllowFailure;if($mount.exit_code-ne0){throw "Exact Warm remount failed: $($mount.lines -join [Environment]::NewLine)"};$remediationJournal.named_remount_performed=$true;Write-Json$remediationJournal$remediationJournalPath;Start-Sleep -Seconds 1
    $tm=Get-MountProbe$ToolingDistro$script:ExpectedWarmMountName;$rm=Get-MountProbe$script:ExpectedRuntimeDistro$script:ExpectedWarmMountName;if(-not$tm.ready -or -not$rm.ready){throw "Remounted Warm ext4 is not visible in both distros. tooling=$($tm.output) runtime=$($rm.output)"};$remediationJournal.tooling_mount_ready=$true;$remediationJournal.runtime_mount_ready=$true
    $uuid=Get-MountUuid$script:ExpectedRuntimeDistro;if($uuid-ne[string]$incident.journal.ext4_uuid){throw 'Remounted ext4 UUID mismatch.'};$remediationJournal.ext4_uuid_verified=$true;Assert-WarmEmpty$script:ExpectedRuntimeDistro;$remediationJournal.warm_empty_verified=$true;Write-Json$remediationJournal$remediationJournalPath

    $remediationJournal.stage='install_clickhouse';Write-Json$remediationJournal$remediationJournalPath;$pkg="clickhouse-common-static_$($script:ExpectedClickHouseVersion)_amd64.deb";$url="https://packages.clickhouse.com/deb/pool/main/c/clickhouse/$pkg";$cmd="set -eu; curl -fL --retry 3 --connect-timeout 15 '$url' -o '/tmp/$pkg'; sha256sum '/tmp/$pkg'; dpkg-deb -f '/tmp/$pkg' Version | grep -Fx '$($script:ExpectedClickHouseVersion)' >/dev/null; if clickhouse client --version 2>/dev/null | grep -F '$($script:ExpectedClickHouseVersion)' >/dev/null; then echo PACKAGE_INSTALL_SKIPPED_EXACT_VERSION; else dpkg -i '/tmp/$pkg' >/tmp/markorbit-clickhouse-prod-dpkg.log 2>&1; echo PACKAGE_INSTALL_PERFORMED; fi; clickhouse client --version";$ins=Invoke-RuntimeShell$cmd-AllowFailure;if($ins.exit_code-ne0){throw "ClickHouse install failed: $($ins.lines -join [Environment]::NewLine)"};$pkgSha='';foreach($l in$ins.lines){if($l-match'^([0-9a-fA-F]{64})\s+'){$pkgSha=$Matches[1].ToLowerInvariant()}};if(-not$pkgSha){throw 'Package SHA not captured.'};$remediationJournal.clickhouse_package_sha256=$pkgSha;$remediationJournal.clickhouse_installed=$true;Write-Json$remediationJournal$remediationJournalPath

    $remediationJournal.stage='write_config';Write-Json$remediationJournal$remediationJournalPath;$source=Get-SourceHealth;$base=Join-Path$incident.dir'remediated-source-config.xml';$cp=Invoke-NativeText'docker'@('cp',"$($source.container_id):/etc/clickhouse-server/config.xml",$base)-AllowFailure;if($cp.exit_code-ne0){throw 'Unable to copy source ClickHouse config.'};$usersPath=Join-Path$incident.dir'remediated-target-users.xml';$users=@'
<clickhouse><profiles><default/></profiles><users><default><password></password><networks><ip>127.0.0.1</ip><ip>::1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users><quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas></clickhouse>
'@;Write-Utf8NoBom$usersPath$users;$overridePath=Join-Path$incident.dir'remediated-target-override.xml';$override=@"
<clickhouse><listen_host replace="replace">127.0.0.1</listen_host><http_port replace="replace">$($script:TargetHttpPort)</http_port><tcp_port replace="replace">$($script:TargetNativePort)</tcp_port><path replace="replace">$($script:RuntimeDataDir)/</path><tmp_path replace="replace">$($script:RuntimeDataDir)/tmp/</tmp_path><user_files_path replace="replace">$($script:RuntimeDataDir)/user_files/</user_files_path><format_schema_path replace="replace">$($script:RuntimeDataDir)/format_schemas/</format_schema_path><storage_configuration><disks><warm_cn><type>local</type><path>$($script:ExpectedWarmDiskPath)</path></warm_cn></disks><policies><warm_cn_only><volumes><main><disk>warm_cn</disk></main></volumes></warm_cn_only></policies></storage_configuration></clickhouse>
"@;Write-Utf8NoBom$overridePath$override;$bw=Convert-WindowsPathToWsl$base;$uw=Convert-WindowsPathToWsl$usersPath;$ow=Convert-WindowsPathToWsl$overridePath;$prep=Invoke-RuntimeShell"set -eu; mkdir -p '$($script:RuntimeInstallDir)/etc/config.d' '$($script:RuntimeDataDir)/tmp' '$($script:RuntimeDataDir)/user_files' '$($script:RuntimeDataDir)/format_schemas' '/var/log/clickhouse-server'; cp '$bw' '$($script:RuntimeInstallDir)/etc/config.xml'; cp '$uw' '$($script:RuntimeInstallDir)/etc/users.xml'; cp '$ow' '$($script:RuntimeInstallDir)/etc/config.d/markorbit-production-warm.xml'; chmod 0644 '$($script:RuntimeInstallDir)/etc/config.xml' '$($script:RuntimeInstallDir)/etc/users.xml' '$($script:RuntimeInstallDir)/etc/config.d/markorbit-production-warm.xml'"-AllowFailure;if($prep.exit_code-ne0){throw 'Target config write failed.'};$remediationJournal.config_written=$true;Write-Json$remediationJournal$remediationJournalPath

    $remediationJournal.stage='start_target';Write-Json$remediationJournal$remediationJournalPath;$start=Invoke-RuntimeShell"set -eu; rm -f '$($script:RuntimeInstallDir)/server.pid'; nohup clickhouse server --config-file='$($script:RuntimeInstallDir)/etc/config.xml' >'$($script:RuntimeInstallDir)/console.log' 2>&1 & echo `"`$!`" >'$($script:RuntimeInstallDir)/server.pid'"-AllowFailure;if($start.exit_code-ne0){throw 'Target ClickHouse start failed.'};$ready=$false;for($i=0;$i-lt45;$i++){$q=Invoke-TargetSql'SELECT 1'-AllowFailure;if($q.exit_code-eq0 -and ((@($q.lines)-join'').Trim()-eq'1'){$ready=$true;break};Start-Sleep -Seconds 2};if(-not$ready){$log=Invoke-RuntimeShell"tail -n 200 '$($script:RuntimeInstallDir)/console.log' 2>/dev/null || true"-AllowFailure;throw "Target ClickHouse not ready: $($log.lines -join [Environment]::NewLine)"};$remediationJournal.server_started=$true;Write-Json$remediationJournal$remediationJournalPath

    $ver=Invoke-TargetSql'SELECT version()';$targetVersion=(@($ver.lines)-join'').Trim();if($targetVersion-ne$script:ExpectedClickHouseVersion){throw 'Target version mismatch.'};$disk=Invoke-TargetSql"SELECT name,path FROM system.disks WHERE name='warm_cn' FORMAT TSV";$diskText=(@($disk.lines)-join'').Trim();$policy=Invoke-TargetSql"SELECT policy_name,volume_name,arrayStringConcat(disks, ',') FROM system.storage_policies WHERE policy_name='warm_cn_only' FORMAT TSV";$policyText=(@($policy.lines)-join'').Trim();if($policyText-ne"warm_cn_only`tmain`twarm_cn"){throw "Policy mismatch: $policyText"};$parts=Invoke-TargetSql"SELECT count() FROM system.parts WHERE disk_name='warm_cn'";$warmParts=[int64]((@($parts.lines)-join'').Trim());if($warmParts-ne0){throw "Warm parts not empty: $warmParts"};Assert-WarmEmpty$script:ExpectedRuntimeDistro;$remediationJournal.storage_foundation_ready=$true;$remediationJournal.completed=$true;$remediationJournal.stage='complete';Write-Json$remediationJournal$remediationJournalPath

    Assert-RawConsumersStopped;[void](Get-SourceHealth);Assert-LiveSource$authority;Assert-Protected;Assert-Capacity;if((Get-FileSha256$env)-ne$envSha){throw '.env changed.'};if((Get-DefaultWslDistro)-ne$defaultBefore){throw 'Default WSL distro changed.'};Assert-ExactMain'final'
    $receiptPath=Join-Path$incident.dir'production_cn_warm_phase_a_mount_remediation.json';$receipt=[ordered]@{receipt_version=$script:RemediationReceiptVersion;engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant();decision='PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_COMPLETE';next_gate='PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE';incident=[ordered]@{engine_sha=$script:IncidentEngineSha;journal_path=$incident.journal_path;journal_sha256=$incident.journal_sha256;failure=$script:ExpectedIncidentError};authority_review_sha256=$authority.sha;topology=[ordered]@{warm_vhdx_path=$script:ExpectedWarmVhdxPath;ext4_uuid=$uuid;mount_name=$script:ExpectedWarmMountName;runtime_distro=$script:ExpectedRuntimeDistro;runtime_root=$script:ExpectedRuntimeRoot;clickhouse_version=$targetVersion;clickhouse_package_sha256=$pkgSha;disk='warm_cn';policy='warm_cn_only';http_port=$script:TargetHttpPort;native_port=$script:TargetNativePort};storage_foundation=[ordered]@{system_disk=$diskText;system_policy=$policyText;warm_part_count=$warmParts;empty_for_cn_migration=$true};exact_path_unmount_performed=$true;named_remount_performed=$true;no_arg_wsl_unmount_performed=$false;wsl_shutdown_performed=$false;runtime_distro_unregister_performed=$false;target_vhdx_delete_performed=$false;vhdx_create_performed=$false;vhdx_format_performed=$false;runtime_import_performed=$false;docker_mutation_performed=$false;accepted_volume_mutation_performed=$false;source_clickhouse_mutation_performed=$false;cn_data_transfer_performed=$false;cross_runtime_transfer_performed=$false;cn_warm_move_performed=$false;source_cleanup_performed=$false;cn_replay_performed=$false;us_bulk_performed=$false;remediation_journal_path=$remediationJournalPath};Write-Json$receipt$receiptPath
    $phaseAPath=Join-Path$incident.dir'production_cn_warm_phase_a_provisioning_remediated.json';$phaseA=[ordered]@{receipt_version=$script:PhaseAReceiptVersion;engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant();decision='PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE';next_gate='PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE';incident_journal_sha256=$incident.journal_sha256;remediation_receipt_path=$receiptPath;remediation_receipt_sha256=(Get-FileSha256$receiptPath);warm_vhdx_path=$script:ExpectedWarmVhdxPath;ext4_uuid=$uuid;target_runtime_distro=$script:ExpectedRuntimeDistro;target_clickhouse_version=$targetVersion;target_clickhouse_package_sha256=$pkgSha;target_http_port=$script:TargetHttpPort;target_native_port=$script:TargetNativePort;warm_part_count=$warmParts;cn_data_transfer_performed=$false;cross_runtime_transfer_performed=$false;cn_warm_move_performed=$false;source_cleanup_performed=$false;accepted_volume_mutation_performed=$false;source_clickhouse_mutation_performed=$false;docker_mutation_performed=$false};Write-Json$phaseA$phaseAPath;Assert-ExactMain'exit'
    Write-Host '===== PRODUCTION CN WARM PHASE A MOUNT REMEDIATION RESULT =====';Write-Host 'decision=PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE';Write-Host 'next_gate=PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE';Write-Host 'mount_remediation_performed=True';Write-Host 'exact_path_unmount_performed=True';Write-Host 'named_remount_performed=True';Write-Host "ext4_uuid=$uuid";Write-Host "target_clickhouse_version=$targetVersion";Write-Host "warm_part_count=$warmParts";foreach($m in @('no_arg_wsl_unmount_performed=False','wsl_shutdown_performed=False','runtime_distro_unregister_performed=False','target_vhdx_delete_performed=False','vhdx_create_performed=False','vhdx_format_performed=False','runtime_import_performed=False','docker_mutation_performed=False','accepted_volume_mutation_performed=False','source_clickhouse_mutation_performed=False','cn_data_transfer_performed=False','cross_runtime_transfer_performed=False','cn_warm_move_performed=False','source_cleanup_performed=False','cn_replay_performed=False','us_bulk_performed=False')){Write-Host$m};Write-Host "remediation_receipt_path=$receiptPath";Write-Host "phase_a_receipt_path=$phaseAPath";Write-Host "remediation_journal_path=$remediationJournalPath";Write-Host 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_DONE';exit 0
}
catch {if($remediationJournal -and $remediationJournalPath){try{$remediationJournal.last_error=$_.Exception.Message;$remediationJournal.stage='blocked';Write-Json$remediationJournal$remediationJournalPath}catch{}};Write-Host "PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_FAILED: $($_.Exception.Message)";if($incident){Write-Host "Incident evidence directory: $($incident.dir)"};if($remediationJournalPath){Write-Host "remediation_journal_path=$remediationJournalPath"};exit 2}
finally {Pop-Location}
