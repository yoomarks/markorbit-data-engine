[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [string]$AuthorityReviewReceiptPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedAuthorityReviewReceiptSha256,
    [Parameter(Mandatory = $true)]
    [string]$OperatorGoToken,
    [string]$ToolingDistro = 'Ubuntu-24.04',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply,
    [string]$ResumeEvidenceDirectory,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:AcceptedReviewEngineSha = '4be4ef8615ed16ff8e3aafb962b476fe2605f5ef'
$script:AcceptedReviewReceiptVersion = 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_V1'
$script:PhaseAReceiptVersion = 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_V1'
$script:PhaseAJournalVersion = 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_JOURNAL_V1'
$script:OperatorGoIssue = 506
$script:OperatorGoCommentId = '5521853975'
$script:ExpectedOperatorGoToken = 'PHASE_A_CN_WARM_PROVISIONING_GO_ISSUE_506_COMMENT_5521853975'
$script:ExpectedDesignReceiptSha256 = '07a7af0bff5b97379c1a5203059f456746f789914040da8c037a37b755cfd837'
$script:ExpectedChecksumReceiptSha256 = 'ddd17889b5d7f513515fc7b3e53b1e697e5671ddcd49b7409b5a877e59c587f0'
$script:ExpectedWarmManifestSha256 = '716302c34060a091330c31b822d331bbcfb802e824a1d296052cd64e9d893231'
$script:ExpectedChecksumManifestSha256 = '4aa3ae5f0d9b8c903b6275ea9a341a9b66f20843c19139a4a8355ca07e38d41a'
$script:ExpectedWarmRows = [int64]2430570761
$script:ExpectedWarmBytes = [int64]562600035674
$script:ExpectedWarmCandidateCount = [int64]4
$script:ExpectedETotalBytes = [int64]2048391114752
$script:ExpectedWarmVhdxPath = 'E:\MarkOrbitData\production\clickhouse\warm_cn.vhdx'
$script:ExpectedWarmVhdxMaxBytes = [int64]842887331840
$script:ExpectedWarmVhdxMaxMiB = [int64]803840
$script:ExpectedWarmLabel = 'mo_warm_cn_prod'
$script:ExpectedWarmMountName = 'markorbit_prod_warm_cn'
$script:ExpectedWarmMountPath = '/mnt/wsl/markorbit_prod_warm_cn'
$script:ExpectedWarmDiskPath = '/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/'
$script:ExpectedRuntimeDistro = 'MarkOrbit-ClickHouse'
$script:ExpectedRuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse'
$script:ExpectedClickHouseVersion = '24.8.14.39'
$script:ExpectedWarmDiskName = 'warm_cn'
$script:ExpectedWarmPolicyName = 'warm_cn_only'
$script:TargetHttpPort = 28123
$script:TargetNativePort = 29000
$script:RuntimeInstallDir = '/opt/markorbit-clickhouse-production'
$script:RuntimeDataDir = '/var/lib/markorbit-clickhouse-production'
$script:ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery'
$script:ExpectedDesignReceiptPath = 'D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_migration_design_20260903_043222\production_cn_warm_migration_design.json'
$script:ExpectedChecksumReceiptPath = 'D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_source_logical_checksum_v2_20260903_052306\production_cn_warm_source_logical_checksum_v2.json'
$script:AllowedPhaseAFiles = @(
    'scripts/run-production-cn-warm-phase-a-provisioning.ps1',
    'tests/test_production_cn_warm_phase_a_provisioning_contract.py',
    '.github/workflows/production-cn-warm-phase-a-provisioning-runtime.yml'
)
$script:ProtectedPaths = @(
    'D:\MarkOrbitData\spike\hot_cn_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_us_spike.vhdx',
    'D:\MarkOrbitData\spike\hot_global_spike.vhdx',
    'E:\MarkOrbitData\spike\warm_spike.vhdx',
    'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse-Spike\ext4.vhdx',
    'E:\MarkOrbitData\wsl-tooling\Ubuntu-24.04\ext4.vhdx',
    $script:ExpectedFRecoveryVhdx
)
$script:ProtectedMountNames = @('markorbit_hot_cn_spike','markorbit_hot_us_spike','markorbit_hot_global_spike','markorbit_warm_spike')
$script:ExpectedSourceIdentities = @(
    [ordered]@{ table='cn_observed_event'; partition_id='all'; rows=[int64]413031435; bytes=[int64]127856495167; active_parts=[int64]11; source_identity_sha256='59118b96ccd4e6ba728b36670becf6d45bc85eb007b8f9cffd2fcfd590dd63ab' },
    [ordered]@{ table='cn_goods_scope_lifecycle_current'; partition_id='all'; rows=[int64]158355910; bytes=[int64]4696234780; active_parts=[int64]11; source_identity_sha256='4ad4dbfc7b8527ea512ffca5b79dcf9c381e8b7fe45a750e0ac999be3dac862a' },
    [ordered]@{ table='cn_goods_item_observation'; partition_id='all'; rows=[int64]219463289; bytes=[int64]58772877234; active_parts=[int64]14; source_identity_sha256='c591139333260615687087caddcd9cc91378785d64658d2796218170e6279776' },
    [ordered]@{ table='cn_goods_item_current'; partition_id='all'; rows=[int64]1639720127; bytes=[int64]371274428493; active_parts=[int64]10; source_identity_sha256='5c1bf56661de5fbdb7cbfb4f3c9d0f797d7f23b5a55c9922e299fdbf6bf5eae3' }
)

function Invoke-NativeText {
    param([Parameter(Mandatory=$true)][string]$Command,[Parameter(Mandatory=$true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,[switch]$AllowFailure)
    $previous=$ErrorActionPreference
    try { $ErrorActionPreference='Continue'; $output=@(& $Command @Arguments 2>&1); $exitCode=$LASTEXITCODE }
    finally { $ErrorActionPreference=$previous }
    $lines=@($output|ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) { throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)" }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}
function Get-StringSha256([string]$Text) { $sha=[System.Security.Cryptography.SHA256]::Create(); try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() } }
function Get-FileSha256([string]$Path) { if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }; return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Read-JsonFile([string]$Path,[string]$Label) { if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }; try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw "$Label JSON invalid: $($_.Exception.Message)" } }
function Write-JsonFile([object]$Value,[string]$Path) { $Value | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $Path -Encoding UTF8 }
function Write-Utf8NoBom([string]$Path,[string]$Content) { $enc=New-Object System.Text.UTF8Encoding($false); [IO.File]::WriteAllText($Path,$Content,$enc) }
function Normalize-WindowsPath([string]$Path) { if ([string]::IsNullOrWhiteSpace($Path)) { return '' }; $p=$Path.Trim(); if ($p.StartsWith('\\?\')) { $p=$p.Substring(4) }; if ($p.StartsWith('\??\')) { $p=$p.Substring(4) }; if ($p -notmatch '^[A-Za-z]:[\\/]') { return '' }; return [IO.Path]::GetFullPath($p).TrimEnd('\') }
function Test-SameWindowsPath([string]$A,[string]$B) { return (Normalize-WindowsPath $A).ToLowerInvariant() -eq (Normalize-WindowsPath $B).ToLowerInvariant() }
function Convert-WindowsPathToWsl([string]$Path) { $p=Normalize-WindowsPath $Path; if ($p -notmatch '^([A-Za-z]):\\(.*)$') { throw "Cannot convert Windows path to WSL path: $Path" }; return "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2].Replace('\','/'))" }
function Assert-SafeTableName([string]$Table) { if ($Table -notmatch '^cn_[a-z0-9_]+$') { throw "Unsafe table name: $Table" } }
function Escape-SqlLiteral([string]$Value) { return $Value.Replace("'","''") }

function Assert-ExactMain([string]$Phase) {
    $expected=$ExpectedMainSha.Trim().ToLowerInvariant(); $head=(git rev-parse HEAD).Trim().ToLowerInvariant(); $origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"; Write-Host "HEAD=$head"; Write-Host "origin/main=$origin"; Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw "Exact main drift detected during $Phase." }
    if (git status --porcelain) { throw "Working tree must be clean during $Phase." }
}
function Assert-ToolingProvenance {
    $ancestor=Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:AcceptedReviewEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Accepted authority-review engine is not an ancestor of exact main.' }
    $diff=Invoke-NativeText 'git' @('diff','--name-only',"$($script:AcceptedReviewEngineSha)..$ExpectedMainSha")
    $changed=@($diff.lines|Where-Object { $_.Trim() }|ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected=@($changed|Where-Object { $_ -notin $script:AllowedPhaseAFiles }); $missing=@($script:AllowedPhaseAFiles|Where-Object { $_ -notin $changed })
    Write-Host "accepted_review_to_current_changed_file_count=$($changed.Count)"; Write-Host "accepted_review_to_current_unexpected_changed_file_count=$($unexpected.Count)"; Write-Host "accepted_review_to_current_missing_phase_a_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) { throw 'Phase A tooling changed outside the exact 3-file boundary.' }
}

function Get-WslDistros {
    $root='HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'; if (-not (Test-Path -LiteralPath $root)) { return @() }; $rows=@()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) { $item=Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue; if (-not $item -or -not $item.DistributionName) { continue }; $rows += [ordered]@{ name=[string]$item.DistributionName; version=if ($null -ne $item.Version) { [int]$item.Version } else { $null }; base_path=if ($item.BasePath) { Normalize-WindowsPath ([string]$item.BasePath) } else { '' } } }
    return @($rows|Sort-Object name)
}
function Get-DefaultWslDistro {
    $root='HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'; if (-not (Test-Path -LiteralPath $root)) { return $null }; $item=Get-ItemProperty -LiteralPath $root -ErrorAction SilentlyContinue; if (-not $item -or -not $item.DefaultDistribution) { return $null }; $d=Get-ItemProperty -LiteralPath (Join-Path $root ([string]$item.DefaultDistribution)) -ErrorAction SilentlyContinue; if ($d -and $d.DistributionName) { return [string]$d.DistributionName }; return $null
}
function Test-ToolingDistroReady {
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','sh','-lc','for c in mkfs.ext4 lsblk blkid findmnt tar curl sha256sum dpkg; do command -v "$c" >/dev/null 2>&1 || exit 10; done') -AllowFailure
    return [bool]($probe.exit_code -eq 0)
}
function Get-WslBlockDisks {
    $probe=Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','lsblk','-dn','-o','NAME,TYPE') -AllowFailure; if ($probe.exit_code -ne 0) { throw 'Unable to inventory WSL block disks.' }; $names=@(); foreach ($line in $probe.lines) { $f=@($line.Trim() -split '\s+'); if ($f.Count -ge 2 -and $f[1] -eq 'disk') { $names += $f[0] } }; return @($names)
}
function Get-MountProbe([string]$Distro,[string]$MountName) {
    $target="/mnt/wsl/$MountName"; $p=Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$target) -AllowFailure; $text=(@($p.lines)-join ' ').Trim(); return [ordered]@{ ready=[bool]($p.exit_code -eq 0 -and $text -match '^ext4\s'); exit_code=$p.exit_code; target=$target; output=$text }
}
function Dismount-ExactVhdx([string]$VhdxPath) {
    if (-not (Test-SameWindowsPath $VhdxPath $script:ExpectedWarmVhdxPath)) { throw 'Refusing to unmount any VHDX except the exact production Warm path.' }
    $p=Invoke-NativeText 'wsl.exe' @('--unmount',$VhdxPath) -AllowFailure; return [bool]($p.exit_code -eq 0)
}
function Invoke-RuntimeText { param([Parameter(Mandatory=$true)][string[]]$Arguments,[switch]$AllowFailure); return Invoke-NativeText 'wsl.exe' (@('-d',$script:ExpectedRuntimeDistro,'-u','root','--')+$Arguments) -AllowFailure:$AllowFailure }
function Invoke-RuntimeShell { param([Parameter(Mandatory=$true)][string]$Command,[switch]$AllowFailure); return Invoke-RuntimeText @('sh','-lc',$Command) -AllowFailure:$AllowFailure }
function Invoke-TargetSql { param([Parameter(Mandatory=$true)][string]$Query,[switch]$AllowFailure); return Invoke-RuntimeText @('clickhouse','client','--host','127.0.0.1','--port',$script:TargetNativePort.ToString(),'--query',$Query) -AllowFailure:$AllowFailure }

function Get-ProductionClickHouseHealth {
    $id=Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse') -AllowFailure; $ids=@($id.lines|Where-Object { $_.Trim() }); if ($id.exit_code -ne 0 -or $ids.Count -ne 1) { return [ordered]@{ ready=$false; container_id=$null; version=$null } }; $container=$ids[0].Trim(); $health=Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$container) -AllowFailure; $one=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure; $ver=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure; $h=(@($health.lines)-join '').Trim().ToLowerInvariant(); $v=if ($ver.exit_code -eq 0) { (@($ver.lines)-join '').Trim() } else { $null }; return [ordered]@{ ready=[bool]($health.exit_code -eq 0 -and $h -eq 'healthy' -and $one.exit_code -eq 0 -and ((@($one.lines)-join '').Trim() -eq '1')); container_id=$container; version=$v }
}
function Assert-AcceptedProductionMount([string]$ContainerId) {
    $p=Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$ContainerId) -AllowFailure; if ($p.exit_code -ne 0) { throw 'Unable to inspect source ClickHouse mount.' }; $mounts=((@($p.lines)-join "`n")|ConvertFrom-Json); $m=@($mounts|Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' }); $ready=[bool]($m.Count -eq 1 -and [string]$m[0].Type -eq 'volume' -and [string]$m[0].Name -eq $AcceptedVolume); Write-Host "accepted_production_mount_ready=$ready"; if (-not $ready) { throw 'Accepted source ClickHouse named volume identity changed.' }
}
function Assert-RawConsumersStopped {
    $total=0; foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) { $p=Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure; if ($p.exit_code -ne 0) { throw "Unable to inspect $service." }; $running=0; foreach ($cid in @($p.lines|Where-Object { $_.Trim() })) { $s=Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$cid.Trim()) -AllowFailure; if ($s.exit_code -ne 0) { throw "Unable to inspect $service container state." }; if (((@($s.lines)-join '').Trim().ToLowerInvariant()) -eq 'true') { $running++ } }; $total += $running; Write-Host "raw_consumer_service=$service running_count=$running" }; Write-Host "running_raw_consumer_count=$total"; if ($total -ne 0) { throw 'Raw/runtime consumers must remain stopped for Phase A.' }
}
function Invoke-ClickHouseJsonRows([string]$Sql,[string]$Label) { $p=Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql+' FORMAT JSONEachRow')) -AllowFailure; if ($p.exit_code -ne 0) { throw "$Label failed." }; $rows=@(); foreach ($line in @($p.lines|Where-Object { $_.Trim() })) { $rows += ($line|ConvertFrom-Json) }; return @($rows) }
function Get-LiveSchemaFingerprint([object]$Row) { return Get-StringSha256 (@([string]$Row.table,[string]$Row.engine,[string]$Row.sorting_key,[string]$Row.primary_key,[string]$Row.partition_key,[string]$Row.create_table_query) -join "`n") }
function Get-PartContentFingerprint([object[]]$Parts) { $lines=@(); foreach ($p in @($Parts|Sort-Object name)) { $lines += @([string]$p.name,[string]$p.rows,[string]$p.bytes_on_disk,[string]$p.hash_of_all_files,[string]$p.hash_of_uncompressed_files,[string]$p.uncompressed_hash_of_compressed_files) -join '|' }; return Get-StringSha256 ($lines -join "`n") }
function Get-ResidencyFingerprint([object[]]$Parts) { $lines=@(); foreach ($p in @($Parts|Sort-Object name)) { $lines += "$([string]$p.name)|$([string]$p.disk_name)" }; return Get-StringSha256 ($lines -join "`n") }
function Get-LiveUnitSnapshot([object]$Plan,[object]$Unit) {
    $table=[string]$Plan.table; Assert-SafeTableName $table; $lit=Escape-SqlLiteral $table; $tables=@(Invoke-ClickHouseJsonRows "SELECT name AS table, engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database='markorbit_facts' AND name='$lit'" "schema $table"); if ($tables.Count -ne 1) { throw "Expected one source table for $table." }; $parts=@(Invoke-ClickHouseJsonRows "SELECT partition_id,name,rows,bytes_on_disk,disk_name,hash_of_all_files,hash_of_uncompressed_files,uncompressed_hash_of_compressed_files FROM system.parts WHERE database='markorbit_facts' AND active AND table='$lit' ORDER BY partition_id,name" "parts $table"); $unitParts=@($parts|Where-Object { [string]$_.partition_id -eq [string]$Unit.partition_id }); $tr=[int64]0; $tb=[int64]0; foreach ($p in $parts) { $tr += [int64]$p.rows; $tb += [int64]$p.bytes_on_disk }; $ur=[int64]0; $ub=[int64]0; foreach ($p in $unitParts) { $ur += [int64]$p.rows; $ub += [int64]$p.bytes_on_disk }; return [ordered]@{ schema_fingerprint_sha256=(Get-LiveSchemaFingerprint $tables[0]); table_rows=$tr; table_bytes=$tb; table_active_parts=[int64]$parts.Count; table_part_content_sha256=(Get-PartContentFingerprint $parts); table_residency_sha256=(Get-ResidencyFingerprint $parts); unit_rows=$ur; unit_bytes=$ub; unit_active_parts=[int64]$unitParts.Count; unit_part_content_sha256=(Get-PartContentFingerprint $unitParts); unit_residency_sha256=(Get-ResidencyFingerprint $unitParts) }
}
function Get-SourceIdentitySha([object]$Snapshot,[object]$Plan,[object]$Unit) { return Get-StringSha256 (@([string]$Plan.table,[string]$Unit.partition_id,[string]$Snapshot.schema_fingerprint_sha256,[string]$Snapshot.table_rows,[string]$Snapshot.table_bytes,[string]$Snapshot.table_active_parts,[string]$Snapshot.table_part_content_sha256,[string]$Snapshot.table_residency_sha256,[string]$Snapshot.unit_rows,[string]$Snapshot.unit_bytes,[string]$Snapshot.unit_active_parts,[string]$Snapshot.unit_part_content_sha256,[string]$Snapshot.unit_residency_sha256,[string]$Plan.source_disk) -join "`n") }

function Resolve-AuthorityReview {
    $path=[IO.Path]::GetFullPath($AuthorityReviewReceiptPath); $sha=Get-FileSha256 $path; if ($sha -ne $ExpectedAuthorityReviewReceiptSha256.Trim().ToLowerInvariant()) { throw 'Authority review receipt SHA256 mismatch.' }; $r=Read-JsonFile $path 'Authority review receipt'
    if ([string]$r.receipt_version -ne $script:AcceptedReviewReceiptVersion -or [string]$r.engine_sha -ne $script:AcceptedReviewEngineSha) { throw 'Authority review receipt identity changed.' }
    if ([string]$r.decision -ne 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_READY_FOR_OPERATOR_GO' -or [string]$r.next_gate -ne 'EXPLICIT_OPERATOR_GO_REQUIRED_BEFORE_PRODUCTION_PROVISIONING_APPLY') { throw 'Authority review is not the accepted READY state.' }
    if (-not [bool]$r.review_only -or -not [bool]$r.read_only -or [bool]$r.mutation_performed -or [bool]$r.apply_authorized -or [bool]$r.provisioning_authorized -or [bool]$r.cn_warm_move_authorized -or [bool]$r.source_cleanup_authorized) { throw 'Authority review safety state changed.' }
    if (@($r.blockers).Count -ne 0) { throw 'Authority review contains blockers.' }
    if ([string]$r.accepted_design.receipt_sha256 -ne $script:ExpectedDesignReceiptSha256 -or [string]$r.accepted_design.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmManifestSha256) { throw 'Authority review design provenance changed.' }
    if ([string]$r.accepted_checksum.receipt_sha256 -ne $script:ExpectedChecksumReceiptSha256 -or [string]$r.accepted_checksum.checksum_manifest_sha256 -ne $script:ExpectedChecksumManifestSha256) { throw 'Authority review checksum provenance changed.' }
    if ([int64]$r.frozen_workload.candidate_count -ne $script:ExpectedWarmCandidateCount -or [int64]$r.frozen_workload.migration_unit_count -ne $script:ExpectedWarmCandidateCount -or [int64]$r.frozen_workload.rows -ne $script:ExpectedWarmRows -or [int64]$r.frozen_workload.bytes -ne $script:ExpectedWarmBytes) { throw 'Authority review frozen workload changed.' }
    if (-not [bool]$r.capacity.recommended_30_percent_admission -or [int64]$r.capacity.proposed_warm_vhdx_max_bytes -ne $script:ExpectedWarmVhdxMaxBytes) { throw 'Authority review capacity basis changed.' }
    if ([string]$r.target_collision_review.warm_vhdx_path -ne $script:ExpectedWarmVhdxPath -or [bool]$r.target_collision_review.warm_vhdx_exists -or [string]$r.target_collision_review.target_runtime_distro -ne $script:ExpectedRuntimeDistro -or [bool]$r.target_collision_review.target_runtime_distro_registered -or [string]$r.target_collision_review.target_runtime_root -ne $script:ExpectedRuntimeRoot -or [bool]$r.target_collision_review.target_runtime_root_exists -or [string]$r.target_collision_review.proposed_mount_name -ne $script:ExpectedWarmMountName) { throw 'Authority review target collision state changed.' }
    foreach ($name in @('exact_clean_main','raw_consumers_stopped','production_clickhouse_ready','accepted_named_volume_mounted','env_unchanged','e_backup_root_absent','f_recovery_preserved','proposed_warm_vhdx_still_absent')) { if (-not [bool]$r.production_invariants.$name) { throw "Authority review invariant false: $name" } }
    foreach ($name in @('vhdx_mutation_authorized','wsl_mutation_authorized','docker_mutation_authorized','clickhouse_mutation_authorized','cross_runtime_transfer_authorized','cn_replay_authorized','us_bulk_authorized','accepted_volume_mutation_authorized','raw_delete_authorized','f_recovery_mutation_authorized')) { if ([bool]$r.constraints.$name) { throw "Authority review unexpectedly authorizes $name." } }
    $live=@($r.live_source_identity|Sort-Object table); foreach ($expected in $script:ExpectedSourceIdentities) { $m=@($live|Where-Object { [string]$_.table -eq [string]$expected.table -and [string]$_.partition_id -eq 'all' }); if ($m.Count -ne 1 -or [string]$m[0].source_identity_sha256 -ne [string]$expected.source_identity_sha256 -or [int64]$m[0].rows -ne [int64]$expected.rows -or [int64]$m[0].bytes -ne [int64]$expected.bytes -or [int64]$m[0].active_parts -ne [int64]$expected.active_parts) { throw "Authority review live source identity changed: $($expected.table)" } }
    $designPath=[string]$r.accepted_design.receipt_path; if (-not (Test-SameWindowsPath $designPath $script:ExpectedDesignReceiptPath) -or (Get-FileSha256 $designPath) -ne $script:ExpectedDesignReceiptSha256) { throw 'Accepted design receipt file drifted.' }
    $checksumPath=[string]$r.accepted_checksum.receipt_path; if (-not (Test-SameWindowsPath $checksumPath $script:ExpectedChecksumReceiptPath) -or (Get-FileSha256 $checksumPath) -ne $script:ExpectedChecksumReceiptSha256) { throw 'Accepted checksum receipt file drifted.' }
    $design=Read-JsonFile $designPath 'Accepted design receipt'; return [ordered]@{ path=$path; sha256=$sha; receipt=$r; design=$design }
}
function Assert-LiveSourceIdentity([object]$Authority) {
    $plans=@($Authority.design.candidates|Sort-Object migration_order); if ($plans.Count -ne 4) { throw 'Accepted design candidate count changed.' }; foreach ($plan in $plans) { $units=@($plan.partitions); if ($units.Count -ne 1) { throw "Unexpected unit count for $($plan.table)." }; $unit=$units[0]; $snap=Get-LiveUnitSnapshot $plan $unit; if ([string]$snap.schema_fingerprint_sha256 -ne [string]$plan.schema_fingerprint_sha256 -or [int64]$snap.table_rows -ne [int64]$plan.rows -or [int64]$snap.table_bytes -ne [int64]$plan.bytes_on_disk -or [int64]$snap.table_active_parts -ne [int64]$plan.active_parts -or [string]$snap.table_part_content_sha256 -ne [string]$plan.source_part_content_manifest_sha256 -or [string]$snap.table_residency_sha256 -ne [string]$plan.source_residency_manifest_sha256) { throw "SOURCE_DESIGN_DRIFT:$($plan.table)" }; $identity=Get-SourceIdentitySha $snap $plan $unit; $expected=@($script:ExpectedSourceIdentities|Where-Object { $_.table -eq [string]$plan.table }); if ($expected.Count -ne 1 -or $identity -ne [string]$expected[0].source_identity_sha256) { throw "SOURCE_IDENTITY_DRIFT:$($plan.table)" }; Write-Host "source_identity_ready=$($plan.table)|all|rows=$($snap.unit_rows)|parts=$($snap.unit_active_parts)|source_identity_sha256=$identity" }
}
function Get-DriveCapacity([string]$DriveLetter) { $d=New-Object IO.DriveInfo($DriveLetter); if (-not $d.IsReady) { throw "$DriveLetter not ready." }; return [ordered]@{ total_bytes=[int64]$d.TotalSize; free_bytes=[int64]$d.AvailableFreeSpace } }
function Assert-FreshCapacity {
    $c=Get-DriveCapacity 'E:\'; if ([int64]$c.total_bytes -ne $script:ExpectedETotalBytes) { throw 'E total capacity changed.' }; $reserve=[int64][math]::Ceiling([double]$c.total_bytes*0.30); $margin=[int64]($c.free_bytes-$script:ExpectedWarmVhdxMaxBytes-$reserve); Write-Host "e_total_bytes=$($c.total_bytes)"; Write-Host "e_free_bytes=$($c.free_bytes)"; Write-Host "e_recommended_30_percent_reserve_bytes=$reserve"; Write-Host "e_margin_after_proposed_max_bytes=$margin"; Write-Host "recommended_30_percent_admission=$([bool]($margin -ge 0))"; if ($margin -lt 0) { throw 'E_30_PERCENT_RESERVE_ADMISSION_FAILED' }; return [ordered]@{ total_bytes=$c.total_bytes; free_bytes=$c.free_bytes; reserve_bytes=$reserve; margin_after_max_bytes=$margin }
}
function Assert-ProtectedState {
    foreach ($p in $script:ProtectedPaths) { if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Protected retained path missing: $p" } }; $f=New-Object IO.FileInfo($script:ExpectedFRecoveryVhdx); if ([int64]$f.Length -ne $script:ExpectedFRecoveryBytes) { throw 'F recovery VHDX length changed.' }; if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root unexpectedly exists.' }; foreach ($m in $script:ProtectedMountNames) { $probe=Get-MountProbe $ToolingDistro $m; if ($probe.exit_code -eq 0) { throw "Protected spike VHDX is mounted: $m" } }
}
function Assert-SourceRuntimeReady {
    Assert-RawConsumersStopped; $p=Get-ProductionClickHouseHealth; if (-not $p.ready -or [string]$p.version -ne $script:ExpectedClickHouseVersion) { throw 'Accepted source ClickHouse health/version drifted.' }; Assert-AcceptedProductionMount $p.container_id; return $p
}
function Test-PortListening([int]$Port) { if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return $false }; return [bool](@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0) }

function New-InitialJournal([string]$AuthoritySha,[string]$DefaultDistro) {
    return [ordered]@{ receipt_version=$script:PhaseAJournalVersion; engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant(); authority_review_sha256=$AuthoritySha; operator_go_issue=$script:OperatorGoIssue; operator_go_comment_id=$script:OperatorGoCommentId; operator_go_token_sha256=(Get-StringSha256 $script:ExpectedOperatorGoToken); stage='authorized_pre_mutation'; default_wsl_distro_before=$DefaultDistro; vhdx_create_started=$false; vhdx_created=$false; ext4_format_started=$false; ext4_formatted=$false; ext4_uuid=$null; named_mount_started=$false; named_mount_ready=$false; tooling_export_started=$false; tooling_exported=$false; tooling_export_tar_sha256=$null; runtime_import_started=$false; runtime_imported=$false; clickhouse_install_started=$false; clickhouse_installed=$false; clickhouse_package_sha256=$null; config_write_started=$false; config_written=$false; server_start_started=$false; server_started=$false; storage_foundation_ready=$false; completed=$false; last_error=$null; cn_data_transfer_performed=$false; cross_runtime_transfer_performed=$false; cn_warm_move_performed=$false; source_cleanup_performed=$false; accepted_volume_mutation_performed=$false; source_clickhouse_mutation_performed=$false; docker_mutation_performed=$false; cn_replay_performed=$false; us_bulk_performed=$false; no_arg_wsl_unmount_performed=$false; wsl_shutdown_performed=$false; runtime_distro_unregister_performed=$false; target_vhdx_delete_performed=$false }
}
function Save-Journal([object]$Journal,[string]$Path) { Write-JsonFile $Journal $Path }
function Assert-JournalIdentity([object]$J,[string]$AuthoritySha) { if ([string]$J.receipt_version -ne $script:PhaseAJournalVersion -or [string]$J.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant() -or [string]$J.authority_review_sha256 -ne $AuthoritySha -or [string]$J.operator_go_comment_id -ne $script:OperatorGoCommentId -or [string]$J.operator_go_token_sha256 -ne (Get-StringSha256 $script:ExpectedOperatorGoToken)) { throw 'Phase A resume journal identity drifted.' } }
function Assert-ResumeArtifacts([object]$J) {
    $vhdx=Test-Path -LiteralPath $script:ExpectedWarmVhdxPath -PathType Leaf; if ([bool]$J.vhdx_created -ne $vhdx) { throw 'Resume VHDX postcondition is ambiguous.' }
    $mount=Get-MountProbe $ToolingDistro $script:ExpectedWarmMountName; if ([bool]$J.named_mount_ready -ne [bool]$mount.ready) { throw 'Resume named mount postcondition is ambiguous.' }
    $distros=@(Get-WslDistros); $runtime=@($distros|Where-Object { $_.name -eq $script:ExpectedRuntimeDistro }); if ([bool]$J.runtime_imported) { if ($runtime.Count -ne 1 -or $runtime[0].version -ne 2 -or -not (Test-SameWindowsPath $runtime[0].base_path $script:ExpectedRuntimeRoot)) { throw 'Resume runtime identity mismatch.' } } elseif ($runtime.Count -ne 0 -or (Test-Path -LiteralPath $script:ExpectedRuntimeRoot)) { throw 'Unexpected target runtime state before recorded import.' }
    if ([bool]$J.server_started) { $q=Invoke-TargetSql 'SELECT 1' -AllowFailure; if ($q.exit_code -ne 0 -or ((@($q.lines)-join '').Trim() -ne '1') { throw 'Recorded target server is not running.' } }
}

function Invoke-ContractFixture {
    if ($script:ExpectedWarmVhdxMaxMiB -ne 803840 -or ($script:ExpectedWarmVhdxMaxMiB*1MB) -ne $script:ExpectedWarmVhdxMaxBytes) { throw 'VHDX max unit conversion contract failed.' }
    if ($script:ExpectedOperatorGoToken -ne 'PHASE_A_CN_WARM_PROVISIONING_GO_ISSUE_506_COMMENT_5521853975') { throw 'Operator GO token contract changed.' }
    if ($script:AllowedPhaseAFiles.Count -ne 3) { throw 'Phase A tooling boundary changed.' }
    if ($script:TargetHttpPort -ne 28123 -or $script:TargetNativePort -ne 29000) { throw 'Target port contract changed.' }
    Write-Host 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_CONTRACT_OK'
}

$journal=$null; $journalPath=$null; $evidenceDir=$null
try {
    Write-Host '===== PRODUCTION CN WARM PHASE A PROVISIONING ====='
    Write-Host "operator_go_issue=$script:OperatorGoIssue"; Write-Host "operator_go_comment_id=$script:OperatorGoCommentId"; Write-Host 'phase_a_scope=EMPTY_EXT4_VHDX_DEDICATED_WSL_CLICKHOUSE_STORAGE_FOUNDATION_ONLY'; Write-Host "apply_requested=$([bool]$Apply)"; Write-Host 'cn_data_transfer_authorized=False'; Write-Host 'cross_runtime_transfer_authorized=False'; Write-Host 'cn_warm_move_authorized=False'; Write-Host 'source_cleanup_authorized=False'; Write-Host 'accepted_volume_mutation_authorized=False'; Write-Host 'source_clickhouse_mutation_authorized=False'; Write-Host 'docker_mutation_authorized=False'; Write-Host 'cn_replay_authorized=False'; Write-Host 'us_bulk_authorized=False'; Write-Host 'no_arg_wsl_unmount_authorized=False'; Write-Host 'wsl_shutdown_authorized=False'; Write-Host 'runtime_distro_unregister_authorized=False'; Write-Host 'target_vhdx_delete_authorized=False'
    if ($ContractOnly) { Invoke-ContractFixture; exit 0 }
    if ($OperatorGoToken -ne $script:ExpectedOperatorGoToken) { throw 'Explicit Phase A operator GO token mismatch.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Phase A provisioning must run from local main.' }; & git fetch origin main | Out-Host; if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }; Assert-ExactMain 'entry'; Assert-ToolingProvenance
    $authority=Resolve-AuthorityReview
    $envPath=Join-Path $repoRoot '.env'; if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw '.env missing.' }; $envShaBefore=Get-FileSha256 $envPath
    $distros=@(Get-WslDistros); $tooling=@($distros|Where-Object { $_.name -eq $ToolingDistro }); if ($tooling.Count -ne 1 -or $tooling[0].version -ne 2 -or -not (Test-ToolingDistroReady)) { throw 'Tooling WSL2 distro not ready.' }
    Assert-ProtectedState; [void](Assert-SourceRuntimeReady); Assert-LiveSourceIdentity $authority; $capacityBefore=Assert-FreshCapacity
    $defaultDistroBefore=Get-DefaultWslDistro

    if ($ResumeEvidenceDirectory) {
        if (-not $Apply) { throw 'ResumeEvidenceDirectory requires -Apply.' }; $evidenceDir=[IO.Path]::GetFullPath($ResumeEvidenceDirectory); if (-not (Test-Path -LiteralPath $evidenceDir -PathType Container)) { throw 'Resume evidence directory missing.' }; $journalPath=Join-Path $evidenceDir 'production_cn_warm_phase_a_provisioning_journal.json'; $journal=Read-JsonFile $journalPath 'Phase A journal'; Assert-JournalIdentity $journal $authority.sha256; Assert-ResumeArtifacts $journal
    } else {
        $runtimeExisting=@($distros|Where-Object { $_.name -eq $script:ExpectedRuntimeDistro }); if ($runtimeExisting.Count -ne 0 -or (Test-Path -LiteralPath $script:ExpectedRuntimeRoot) -or (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath)) { throw 'Fresh Phase A requires target VHDX/runtime to be absent; partial state requires explicit resume/remediation.' }; $mount=Get-MountProbe $ToolingDistro $script:ExpectedWarmMountName; if ($mount.exit_code -eq 0) { throw 'Target Warm mount name already exists.' }; if (Test-PortListening $script:TargetHttpPort -or Test-PortListening $script:TargetNativePort) { throw 'Target ClickHouse port collision detected.' }; $timestamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss'); $evidenceDir=Join-Path $repoRoot (Join-Path $EvidenceRoot "production_cn_warm_phase_a_provisioning_$timestamp"); New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null; $evidenceDir=[IO.Path]::GetFullPath($evidenceDir); $journalPath=Join-Path $evidenceDir 'production_cn_warm_phase_a_provisioning_journal.json'; $journal=New-InitialJournal $authority.sha256 $defaultDistroBefore; Save-Journal $journal $journalPath
    }
    Write-Host "Evidence directory: $evidenceDir"; Write-Host "journal_path=$journalPath"

    if (-not $Apply) { Write-Host 'decision=PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_READY_FOR_APPLY'; Write-Host 'next_gate=EXPLICIT_APPLY_INVOCATION_WITH_RECORDED_OPERATOR_GO'; Write-Host 'mutation_performed=False'; Write-Host 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_DONE'; Assert-ExactMain 'exit'; exit 0 }
    $admin=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Phase A Apply requires elevated Administrator PowerShell.' }

    if (-not [bool]$journal.vhdx_created) {
        $journal.stage='create_vhdx'; $journal.vhdx_create_started=$true; Save-Journal $journal $journalPath
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $script:ExpectedWarmVhdxPath) | Out-Null
        $diskpartPath=Join-Path $evidenceDir 'diskpart_create_warm_cn.txt'; @("create vdisk file=`"$($script:ExpectedWarmVhdxPath)`" maximum=$($script:ExpectedWarmVhdxMaxMiB) type=expandable",'exit') | Set-Content -LiteralPath $diskpartPath -Encoding ASCII
        $create=Invoke-NativeText 'diskpart.exe' @('/s',$diskpartPath) -AllowFailure; if ($create.exit_code -ne 0 -or -not (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath -PathType Leaf)) { throw "Warm VHDX create failed: $($create.lines -join [Environment]::NewLine)" }; $journal.vhdx_created=$true; Save-Journal $journal $journalPath
    }

    if (-not [bool]$journal.ext4_formatted) {
        $journal.stage='format_ext4'; $journal.ext4_format_started=$true; Save-Journal $journal $journalPath
        $before=@(Get-WslBlockDisks); $bare=Invoke-NativeText 'wsl.exe' @('--mount','--vhd',$script:ExpectedWarmVhdxPath,'--bare') -AllowFailure; if ($bare.exit_code -ne 0) { throw 'Unable to bare-mount new Warm VHDX.' }; Start-Sleep -Seconds 1; $after=@(Get-WslBlockDisks); $new=@($after|Where-Object { $_ -notin $before }); if ($new.Count -ne 1) { throw "Expected exactly one new WSL block disk; observed $($new -join ',')." }; $device="/dev/$($new[0])"; $mkfs=Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','mkfs.ext4','-F','-L',$script:ExpectedWarmLabel,$device) -AllowFailure; if ($mkfs.exit_code -ne 0) { throw 'mkfs.ext4 failed for production Warm VHDX.' }; $blkid=Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','blkid','-s','UUID','-o','value',$device) -AllowFailure; if ($blkid.exit_code -ne 0) { throw 'Unable to capture Warm ext4 UUID.' }; $journal.ext4_uuid=(@($blkid.lines)-join '').Trim(); if (-not (Dismount-ExactVhdx $script:ExpectedWarmVhdxPath)) { throw 'Exact Warm VHDX detach after format failed.' }; $journal.ext4_formatted=$true; Save-Journal $journal $journalPath
    }

    if (-not [bool]$journal.named_mount_ready) {
        $journal.stage='mount_named_ext4'; $journal.named_mount_started=$true; Save-Journal $journal $journalPath
        $mount=Invoke-NativeText 'wsl.exe' @('--mount','--vhd',$script:ExpectedWarmVhdxPath,'--name',$script:ExpectedWarmMountName) -AllowFailure; if ($mount.exit_code -ne 0) { throw 'Named Warm ext4 mount failed.' }; Start-Sleep -Seconds 1; $probe=Get-MountProbe $ToolingDistro $script:ExpectedWarmMountName; if (-not $probe.ready) { throw "Named Warm mount is not ext4: $($probe.output)" }; $prepare=Invoke-NativeText 'wsl.exe' @('-d',$ToolingDistro,'-u','root','--','sh','-lc',"mkdir -p '$($script:ExpectedWarmDiskPath)' && chmod 0770 '$($script:ExpectedWarmDiskPath)' && test -z `"`$(find '$($script:ExpectedWarmDiskPath)' -mindepth 1 -print -quit)`"") -AllowFailure; if ($prepare.exit_code -ne 0) { throw 'Warm ClickHouse disk directory is not freshly empty.' }; $journal.named_mount_ready=$true; Save-Journal $journal $journalPath
    }

    $exportTar=Join-Path $evidenceDir 'tooling-rootfs.tar'
    if (-not [bool]$journal.tooling_exported) {
        if (Test-Path -LiteralPath $exportTar) { throw 'Unexpected tooling export tar before recorded export.' }; $journal.stage='export_tooling_rootfs'; $journal.tooling_export_started=$true; Save-Journal $journal $journalPath
        $export=Invoke-NativeText 'wsl.exe' @('--export',$ToolingDistro,$exportTar) -AllowFailure; if ($export.exit_code -ne 0 -or -not (Test-Path -LiteralPath $exportTar -PathType Leaf)) { throw 'Tooling WSL export failed.' }; $journal.tooling_export_tar_sha256=Get-FileSha256 $exportTar; $journal.tooling_exported=$true; Save-Journal $journal $journalPath
    } else { if (-not (Test-Path -LiteralPath $exportTar -PathType Leaf) -or (Get-FileSha256 $exportTar) -ne [string]$journal.tooling_export_tar_sha256) { throw 'Recorded tooling rootfs export drifted.' } }

    if (-not [bool]$journal.runtime_imported) {
        $journal.stage='import_runtime'; $journal.runtime_import_started=$true; Save-Journal $journal $journalPath
        New-Item -ItemType Directory -Force -Path $script:ExpectedRuntimeRoot | Out-Null; $import=Invoke-NativeText 'wsl.exe' @('--import',$script:ExpectedRuntimeDistro,$script:ExpectedRuntimeRoot,$exportTar,'--version','2') -AllowFailure; if ($import.exit_code -ne 0) { throw 'Dedicated production WSL import failed.' }; $d=@(Get-WslDistros|Where-Object { $_.name -eq $script:ExpectedRuntimeDistro }); if ($d.Count -ne 1 -or $d[0].version -ne 2 -or -not (Test-SameWindowsPath $d[0].base_path $script:ExpectedRuntimeRoot)) { throw 'Imported production runtime identity mismatch.' }; if ((Get-DefaultWslDistro) -ne $defaultDistroBefore) { throw 'WSL default distro changed during production runtime import.' }; $runtimeProbe=Invoke-RuntimeShell 'printf RUNTIME_OK' -AllowFailure; if ($runtimeProbe.exit_code -ne 0 -or ((@($runtimeProbe.lines)-join '').Trim() -ne 'RUNTIME_OK') { throw 'Production runtime did not start.' }; $runtimeMount=Get-MountProbe $script:ExpectedRuntimeDistro $script:ExpectedWarmMountName; if (-not $runtimeMount.ready) { throw 'Production runtime cannot see named Warm ext4 mount.' }; $journal.runtime_imported=$true; Save-Journal $journal $journalPath
    }

    if (-not [bool]$journal.clickhouse_installed) {
        $journal.stage='install_clickhouse'; $journal.clickhouse_install_started=$true; Save-Journal $journal $journalPath
        $pkg="clickhouse-common-static_$($script:ExpectedClickHouseVersion)_amd64.deb"; $url="https://packages.clickhouse.com/deb/pool/main/c/clickhouse/$pkg"; $cmd="set -eu; curl -fL --retry 3 --connect-timeout 15 '$url' -o '/tmp/$pkg'; sha256sum '/tmp/$pkg'; dpkg-deb -f '/tmp/$pkg' Version; if clickhouse client --version 2>/dev/null | grep -F '$($script:ExpectedClickHouseVersion)' >/dev/null; then echo PACKAGE_INSTALL_SKIPPED_EXACT_VERSION; else dpkg -i '/tmp/$pkg' >/tmp/markorbit-clickhouse-prod-dpkg.log 2>&1; echo PACKAGE_INSTALL_PERFORMED; fi; clickhouse client --version"; $install=Invoke-RuntimeShell $cmd -AllowFailure; if ($install.exit_code -ne 0) { throw "Target ClickHouse install failed: $($install.lines -join [Environment]::NewLine)" }; $pkgSha=$null; foreach ($line in $install.lines) { if ($line -match '^([0-9a-fA-F]{64})\s+') { $pkgSha=$Matches[1].ToLowerInvariant() } }; if (-not $pkgSha) { throw 'Target ClickHouse package SHA256 not captured.' }; $ver=Invoke-RuntimeText @('clickhouse','client','--version') -AllowFailure; if ($ver.exit_code -ne 0 -or ((@($ver.lines)-join ' ') -notmatch [regex]::Escape($script:ExpectedClickHouseVersion))) { throw 'Target ClickHouse exact version not installed.' }; $journal.clickhouse_package_sha256=$pkgSha; $journal.clickhouse_installed=$true; Save-Journal $journal $journalPath
    }

    if (-not [bool]$journal.config_written) {
        $journal.stage='write_target_config'; $journal.config_write_started=$true; Save-Journal $journal $journalPath
        $source=Get-ProductionClickHouseHealth; $baseConfig=Join-Path $evidenceDir 'source-config.xml'; $cp=Invoke-NativeText 'docker' @('cp',"$($source.container_id):/etc/clickhouse-server/config.xml",$baseConfig) -AllowFailure; if ($cp.exit_code -ne 0 -or -not (Test-Path -LiteralPath $baseConfig -PathType Leaf)) { throw 'Unable to read source ClickHouse base config.' }
        $usersPath=Join-Path $evidenceDir 'target-users.xml'; $users=@'
<clickhouse>
  <profiles><default/></profiles>
  <users><default><password></password><networks><ip>127.0.0.1</ip><ip>::1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users>
  <quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas>
</clickhouse>
'@; Write-Utf8NoBom $usersPath $users
        $overridePath=Join-Path $evidenceDir 'target-override.xml'; $override=@"
<clickhouse>
  <listen_host replace="replace">127.0.0.1</listen_host>
  <http_port replace="replace">$($script:TargetHttpPort)</http_port>
  <tcp_port replace="replace">$($script:TargetNativePort)</tcp_port>
  <path replace="replace">$($script:RuntimeDataDir)/</path>
  <tmp_path replace="replace">$($script:RuntimeDataDir)/tmp/</tmp_path>
  <user_files_path replace="replace">$($script:RuntimeDataDir)/user_files/</user_files_path>
  <format_schema_path replace="replace">$($script:RuntimeDataDir)/format_schemas/</format_schema_path>
  <storage_configuration><disks><warm_cn><type>local</type><path>$($script:ExpectedWarmDiskPath)</path></warm_cn></disks><policies><warm_cn_only><volumes><main><disk>warm_cn</disk></main></volumes></warm_cn_only></policies></storage_configuration>
</clickhouse>
"@; Write-Utf8NoBom $overridePath $override
        $baseWsl=Convert-WindowsPathToWsl $baseConfig; $usersWsl=Convert-WindowsPathToWsl $usersPath; $overrideWsl=Convert-WindowsPathToWsl $overridePath; $prep=Invoke-RuntimeShell "set -eu; mkdir -p '$($script:RuntimeInstallDir)/etc/config.d' '$($script:RuntimeDataDir)/tmp' '$($script:RuntimeDataDir)/user_files' '$($script:RuntimeDataDir)/format_schemas' '/var/log/clickhouse-server'; cp '$baseWsl' '$($script:RuntimeInstallDir)/etc/config.xml'; cp '$usersWsl' '$($script:RuntimeInstallDir)/etc/users.xml'; cp '$overrideWsl' '$($script:RuntimeInstallDir)/etc/config.d/markorbit-production-warm.xml'; chmod 0644 '$($script:RuntimeInstallDir)/etc/config.xml' '$($script:RuntimeInstallDir)/etc/users.xml' '$($script:RuntimeInstallDir)/etc/config.d/markorbit-production-warm.xml'" -AllowFailure; if ($prep.exit_code -ne 0) { throw 'Unable to prepare target ClickHouse configuration.' }; $journal.config_written=$true; Save-Journal $journal $journalPath
    }

    if (-not [bool]$journal.server_started) {
        $journal.stage='start_target_clickhouse'; $journal.server_start_started=$true; Save-Journal $journal $journalPath
        $start=Invoke-RuntimeShell "set -eu; rm -f '$($script:RuntimeInstallDir)/server.pid'; nohup clickhouse server --config-file='$($script:RuntimeInstallDir)/etc/config.xml' >'$($script:RuntimeInstallDir)/console.log' 2>&1 & echo `"`$!`" >'$($script:RuntimeInstallDir)/server.pid'" -AllowFailure; if ($start.exit_code -ne 0) { throw 'Unable to start target ClickHouse.' }; $ready=$false; for ($i=0; $i -lt 45; $i++) { $q=Invoke-TargetSql 'SELECT 1' -AllowFailure; if ($q.exit_code -eq 0 -and ((@($q.lines)-join '').Trim() -eq '1') { $ready=$true; break }; Start-Sleep -Seconds 2 }; if (-not $ready) { $log=Invoke-RuntimeShell "tail -n 200 '$($script:RuntimeInstallDir)/console.log' 2>/dev/null || true" -AllowFailure; throw "Target ClickHouse did not become ready: $($log.lines -join [Environment]::NewLine)" }; $journal.server_started=$true; Save-Journal $journal $journalPath
    }

    $journal.stage='verify_empty_storage_foundation'; Save-Journal $journal $journalPath
    $version=Invoke-TargetSql 'SELECT version()' -AllowFailure; $targetVersion=(@($version.lines)-join '').Trim(); if ($version.exit_code -ne 0 -or $targetVersion -ne $script:ExpectedClickHouseVersion) { throw "Target ClickHouse SQL version mismatch: $targetVersion" }
    $disk=Invoke-TargetSql "SELECT name, path FROM system.disks WHERE name='warm_cn' FORMAT TSV" -AllowFailure; $diskText=(@($disk.lines)-join '').Trim(); if ($disk.exit_code -ne 0 -or $diskText -ne "warm_cn`t$($script:ExpectedWarmDiskPath)") { throw "Target warm_cn disk mismatch: $diskText" }
    $policy=Invoke-TargetSql "SELECT policy_name, volume_name, arrayStringConcat(disks, ',') FROM system.storage_policies WHERE policy_name='warm_cn_only' FORMAT TSV" -AllowFailure; $policyText=(@($policy.lines)-join '').Trim(); if ($policy.exit_code -ne 0 -or $policyText -ne "warm_cn_only`tmain`twarm_cn") { throw "Target warm_cn_only policy mismatch: $policyText" }
    $parts=Invoke-TargetSql "SELECT count() FROM system.parts WHERE disk_name='warm_cn'" -AllowFailure; $warmPartCount=if ($parts.exit_code -eq 0) { [int64]((@($parts.lines)-join '').Trim()) } else { [int64]-1 }; if ($warmPartCount -ne 0) { throw "Target Warm disk is not empty; active/all part rows observed: $warmPartCount" }
    $journal.storage_foundation_ready=$true; $journal.completed=$true; $journal.stage='complete'; Save-Journal $journal $journalPath

    Assert-ProtectedState; [void](Assert-SourceRuntimeReady); Assert-LiveSourceIdentity $authority; $capacityAfter=Assert-FreshCapacity; if ((Get-FileSha256 $envPath) -ne $envShaBefore) { throw '.env changed during Phase A.' }; if ((Get-DefaultWslDistro) -ne $defaultDistroBefore) { throw 'WSL default distro changed during Phase A.' }; Assert-ExactMain 'final'
    $receiptPath=Join-Path $evidenceDir 'production_cn_warm_phase_a_provisioning.json'; $receipt=[ordered]@{ receipt_version=$script:PhaseAReceiptVersion; engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant(); decision='PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE'; next_gate='PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE'; operator_authority=[ordered]@{ issue=$script:OperatorGoIssue; comment_id=$script:OperatorGoCommentId; token_sha256=(Get-StringSha256 $script:ExpectedOperatorGoToken); authority_review_path=$authority.path; authority_review_sha256=$authority.sha256 }; topology=[ordered]@{ warm_vhdx_path=$script:ExpectedWarmVhdxPath; warm_vhdx_max_bytes=$script:ExpectedWarmVhdxMaxBytes; ext4_label=$script:ExpectedWarmLabel; ext4_uuid=[string]$journal.ext4_uuid; mount_name=$script:ExpectedWarmMountName; mount_path=$script:ExpectedWarmMountPath; runtime_distro=$script:ExpectedRuntimeDistro; runtime_root=$script:ExpectedRuntimeRoot; clickhouse_version=$targetVersion; clickhouse_package_sha256=[string]$journal.clickhouse_package_sha256; http_port=$script:TargetHttpPort; native_port=$script:TargetNativePort; disk_name=$script:ExpectedWarmDiskName; disk_path=$script:ExpectedWarmDiskPath; storage_policy=$script:ExpectedWarmPolicyName }; capacity_before=$capacityBefore; capacity_after=$capacityAfter; storage_foundation=[ordered]@{ system_disk=$diskText; system_policy=$policyText; warm_part_count=$warmPartCount; empty_for_cn_migration=$true }; phase_a_apply_performed=$true; vhdx_create_performed=[bool]$journal.vhdx_created; ext4_format_performed=[bool]$journal.ext4_formatted; named_mount_performed=[bool]$journal.named_mount_ready; runtime_import_performed=[bool]$journal.runtime_imported; target_clickhouse_install_performed=[bool]$journal.clickhouse_installed; target_clickhouse_config_performed=[bool]$journal.config_written; target_clickhouse_start_performed=[bool]$journal.server_started; cn_data_transfer_performed=$false; cross_runtime_transfer_performed=$false; cn_warm_move_performed=$false; source_cleanup_performed=$false; accepted_volume_mutation_performed=$false; source_clickhouse_mutation_performed=$false; docker_mutation_performed=$false; cn_replay_performed=$false; us_bulk_performed=$false; no_arg_wsl_unmount_performed=$false; wsl_shutdown_performed=$false; runtime_distro_unregister_performed=$false; target_vhdx_delete_performed=$false; journal_path=$journalPath }
    Write-JsonFile $receipt $receiptPath; Assert-ExactMain 'exit'
    Write-Host '===== PRODUCTION CN WARM PHASE A PROVISIONING RESULT ====='; Write-Host 'decision=PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE'; Write-Host 'next_gate=PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE'; Write-Host 'phase_a_apply_performed=True'; Write-Host "warm_vhdx_path=$script:ExpectedWarmVhdxPath"; Write-Host "warm_vhdx_max_bytes=$script:ExpectedWarmVhdxMaxBytes"; Write-Host "ext4_uuid=$($journal.ext4_uuid)"; Write-Host "target_runtime_distro=$script:ExpectedRuntimeDistro"; Write-Host "target_clickhouse_version=$targetVersion"; Write-Host "target_clickhouse_package_sha256=$($journal.clickhouse_package_sha256)"; Write-Host "target_http_port=$script:TargetHttpPort"; Write-Host "target_native_port=$script:TargetNativePort"; Write-Host "warm_part_count=$warmPartCount"; Write-Host 'cn_data_transfer_performed=False'; Write-Host 'cross_runtime_transfer_performed=False'; Write-Host 'cn_warm_move_performed=False'; Write-Host 'source_cleanup_performed=False'; Write-Host 'accepted_volume_mutation_performed=False'; Write-Host 'source_clickhouse_mutation_performed=False'; Write-Host 'docker_mutation_performed=False'; Write-Host 'cn_replay_performed=False'; Write-Host 'us_bulk_performed=False'; Write-Host 'no_arg_wsl_unmount_performed=False'; Write-Host 'wsl_shutdown_performed=False'; Write-Host 'runtime_distro_unregister_performed=False'; Write-Host 'target_vhdx_delete_performed=False'; Write-Host "receipt_path=$receiptPath"; Write-Host "journal_path=$journalPath"; Write-Host "Evidence directory: $evidenceDir"; Write-Host 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_DONE'; exit 0
}
catch {
    if ($journal -and $journalPath) { try { $journal.last_error=$_.Exception.Message; $journal.stage='blocked'; Save-Journal $journal $journalPath } catch {} }
    Write-Host "PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_FAILED: $($_.Exception.Message)"; if ($evidenceDir) { Write-Host "Evidence directory: $evidenceDir" }; if ($journalPath) { Write-Host "journal_path=$journalPath" }; exit 2
}
finally { Pop-Location }
