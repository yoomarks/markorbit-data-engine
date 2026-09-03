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
    [string]$IncidentEvidenceDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OperatorGoToken,

    [string]$ToolingDistro = 'Ubuntu-24.04',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [switch]$Apply,
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:IncidentEngineSha = '111908335714292ae4d42e54b3664156d19d64ca'
$script:IncidentJournalVersion = 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_JOURNAL_V1'
$script:RemediationJournalVersion = 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_JOURNAL_V2'
$script:RemediationReceiptVersion = 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_V2'
$script:PhaseAReceiptVersion = 'PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_V3_REMEDIATED'
$script:OperatorGoIssue = 506
$script:OperatorGoCommentId = '5521853975'
$script:RemediationIssue = 510
$script:ExpectedOperatorGoToken = 'PHASE_A_CN_WARM_PROVISIONING_GO_ISSUE_506_COMMENT_5521853975'
$script:ExpectedIncidentError = 'Production runtime cannot see named Warm ext4 mount.'
$script:ExpectedIncidentEvidenceDirectory = 'D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_phase_a_provisioning_20260903_072812'
$script:ToolingOnlyMountState = 'TOOLING_ONLY_MOUNT_VISIBLE'
$script:AlreadyDetachedMountState = 'MOUNT_ALREADY_DETACHED'

$script:ExpectedReviewEngineSha = '4be4ef8615ed16ff8e3aafb962b476fe2605f5ef'
$script:ExpectedReviewVersion = 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_V1'
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
$script:ExpectedWarmMountName = 'markorbit_prod_warm_cn'
$script:ExpectedWarmMountPath = '/mnt/wsl/markorbit_prod_warm_cn'
$script:ExpectedWarmDiskPath = '/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/'
$script:ExpectedRuntimeDistro = 'MarkOrbit-ClickHouse'
$script:ExpectedRuntimeRoot = 'D:\MarkOrbitData\wsl-runtime\MarkOrbit-ClickHouse'
$script:ExpectedClickHouseVersion = '24.8.14.39'
$script:TargetHttpPort = 28123
$script:TargetNativePort = 29000
$script:RuntimeInstallDir = '/opt/markorbit-clickhouse-production'
$script:RuntimeDataDir = '/var/lib/markorbit-clickhouse-production'

$script:ExpectedFRecoveryVhdx = 'F:\MarkOrbitData\recovery\docker_data_precompact_20260828_023021.vhdx'
$script:ExpectedFRecoveryBytes = [int64]961542094848
$script:EBackupRoot = 'E:\DockerDataBackup\DockerDesktopWSL_20260901_before_recovery'
$script:ExpectedDesignReceiptPath = 'D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_migration_design_20260903_043222\production_cn_warm_migration_design.json'
$script:ExpectedChecksumReceiptPath = 'D:\yoomarks\markorbit-data-engine\reports\production_cn_warm_source_logical_checksum_v2_20260903_052306\production_cn_warm_source_logical_checksum_v2.json'

$script:AllowedRemediationFiles = @(
    'scripts/resume-production-cn-warm-phase-a-mount-remediation.ps1',
    'tests/test_production_cn_warm_phase_a_mount_remediation_contract.py',
    '.github/workflows/production-cn-warm-phase-a-mount-remediation-runtime.yml'
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

$script:ExpectedSourceIdentities = @(
    [ordered]@{ table='cn_observed_event'; rows=[int64]413031435; bytes=[int64]127856495167; parts=[int64]11; sha='59118b96ccd4e6ba728b36670becf6d45bc85eb007b8f9cffd2fcfd590dd63ab' },
    [ordered]@{ table='cn_goods_scope_lifecycle_current'; rows=[int64]158355910; bytes=[int64]4696234780; parts=[int64]11; sha='4ad4dbfc7b8527ea512ffca5b79dcf9c381e8b7fe45a750e0ac999be3dac862a' },
    [ordered]@{ table='cn_goods_item_observation'; rows=[int64]219463289; bytes=[int64]58772877234; parts=[int64]14; sha='c591139333260615687087caddcd9cc91378785d64658d2796218170e6279776' },
    [ordered]@{ table='cn_goods_item_current'; rows=[int64]1639720127; bytes=[int64]371274428493; parts=[int64]10; sha='5c1bf56661de5fbdb7cbfb4f3c9d0f797d7f23b5a55c9922e299fdbf6bf5eae3' }
)

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $lines = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command failed with exit code ${exitCode}: $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}

function Get-StringSha256([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-Json([string]$Path,[string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "$Label JSON invalid: $($_.Exception.Message)" }
}

function Write-Json([object]$Value,[string]$Path) {
    $Value | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Write-Utf8NoBom([string]$Path,[string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path,$Content,$encoding)
}

function Normalize-WindowsPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $value = $Path.Trim()
    if ($value.StartsWith('\\?\')) { $value = $value.Substring(4) }
    if ($value.StartsWith('\??\')) { $value = $value.Substring(4) }
    if ($value -notmatch '^[A-Za-z]:[\\/]') { return '' }
    return [IO.Path]::GetFullPath($value).TrimEnd('\')
}

function Test-SameWindowsPath([string]$A,[string]$B) {
    return (Normalize-WindowsPath $A).ToLowerInvariant() -eq (Normalize-WindowsPath $B).ToLowerInvariant()
}

function Convert-WindowsPathToWsl([string]$Path) {
    $value = Normalize-WindowsPath $Path
    if ($value -notmatch '^([A-Za-z]):\\(.*)$') { throw "Cannot convert Windows path: $Path" }
    return "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2].Replace('\','/'))"
}

function Escape-Sql([string]$Value) { return $Value.Replace("'","''") }

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw "Exact main drift during $Phase." }
    if (git status --porcelain) { throw "Working tree not clean during $Phase." }
}

function Assert-RemediationProvenance {
    $ancestor = Invoke-NativeText 'git' @('merge-base','--is-ancestor',$script:IncidentEngineSha,$ExpectedMainSha) -AllowFailure
    if ($ancestor.exit_code -ne 0) { throw 'Incident engine is not ancestor of remediation main.' }
    $diff = Invoke-NativeText 'git' @('diff','--name-only',"$($script:IncidentEngineSha)..$ExpectedMainSha")
    $changed = @($diff.lines | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().Replace('\','/') })
    $unexpected = @($changed | Where-Object { $_ -notin $script:AllowedRemediationFiles })
    $missing = @($script:AllowedRemediationFiles | Where-Object { $_ -notin $changed })
    Write-Host "incident_to_current_changed_file_count=$($changed.Count)"
    Write-Host "incident_to_current_unexpected_changed_file_count=$($unexpected.Count)"
    Write-Host "incident_to_current_missing_remediation_file_count=$($missing.Count)"
    if ($changed.Count -ne 3 -or $unexpected.Count -ne 0 -or $missing.Count -ne 0) {
        throw 'Remediation tooling changed outside exact 3-file boundary.'
    }
}

function Get-WslDistros {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $rows = @()
    foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.DistributionName) { continue }
        $rows += [ordered]@{
            name = [string]$item.DistributionName
            version = if ($null -ne $item.Version) { [int]$item.Version } else { $null }
            base_path = if ($item.BasePath) { Normalize-WindowsPath ([string]$item.BasePath) } else { '' }
        }
    }
    return @($rows)
}

function Get-DefaultWslDistro {
    $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    $item = Get-ItemProperty -LiteralPath $root -ErrorAction SilentlyContinue
    if (-not $item -or -not $item.DefaultDistribution) { return $null }
    $distro = Get-ItemProperty -LiteralPath (Join-Path $root ([string]$item.DefaultDistribution)) -ErrorAction SilentlyContinue
    if ($distro -and $distro.DistributionName) { return [string]$distro.DistributionName }
    return $null
}

function Get-MountProbe([string]$Distro,[string]$MountName) {
    $target = "/mnt/wsl/$MountName"
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','findmnt','-n','-o','FSTYPE,SOURCE,TARGET',$target) -AllowFailure
    $text = (@($probe.lines) -join ' ').Trim()
    return [ordered]@{
        ready = [bool]($probe.exit_code -eq 0 -and $text -match '^ext4\s')
        exit_code = $probe.exit_code
        target = $target
        output = $text
    }
}

function Invoke-RuntimeText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments,[switch]$AllowFailure)
    return Invoke-NativeText 'wsl.exe' (@('-d',$script:ExpectedRuntimeDistro,'-u','root','--') + $Arguments) -AllowFailure:$AllowFailure
}

function Invoke-RuntimeShell([string]$Command,[switch]$AllowFailure) {
    return Invoke-RuntimeText @('sh','-lc',$Command) -AllowFailure:$AllowFailure
}

function Invoke-TargetSql([string]$Query,[switch]$AllowFailure) {
    return Invoke-RuntimeText @('clickhouse','client','--host','127.0.0.1','--port',$script:TargetNativePort.ToString(),'--query',$Query) -AllowFailure:$AllowFailure
}

function Test-PortListening([int]$Port) {
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return $false }
    return [bool](@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0)
}

function Dismount-ExactWarmVhdx {
    $probe = Invoke-NativeText 'wsl.exe' @('--unmount',$script:ExpectedWarmVhdxPath) -AllowFailure
    return [bool]($probe.exit_code -eq 0)
}

function Assert-RawConsumersStopped {
    $total = 0
    foreach ($service in @('api','worker','mark-image-worker','qcc-acquisition')) {
        $probe = Invoke-NativeText 'docker' @('compose','--profile','mark-image','--profile','qcc','ps','-a','-q',$service) -AllowFailure
        if ($probe.exit_code -ne 0) { throw "Unable to inspect $service." }
        $running = 0
        foreach ($container in @($probe.lines | Where-Object { $_.Trim() })) {
            $state = Invoke-NativeText 'docker' @('inspect','--format','{{.State.Running}}',$container.Trim()) -AllowFailure
            if ($state.exit_code -ne 0) { throw "Unable to inspect $service container state." }
            if (((@($state.lines) -join '').Trim().ToLowerInvariant()) -eq 'true') { $running++ }
        }
        $total += $running
        Write-Host "raw_consumer_service=$service running_count=$running"
    }
    Write-Host "running_raw_consumer_count=$total"
    if ($total -ne 0) { throw 'Consumers must remain stopped.' }
}

function Get-SourceHealth {
    $idProbe = Invoke-NativeText 'docker' @('compose','ps','-q','clickhouse') -AllowFailure
    $containerId = (@($idProbe.lines) -join '').Trim()
    if (-not $containerId) { throw 'Source ClickHouse container missing.' }
    $health = Invoke-NativeText 'docker' @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$containerId) -AllowFailure
    $one = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT 1') -AllowFailure
    $version = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query','SELECT version()') -AllowFailure
    $ready = [bool](
        $health.exit_code -eq 0 -and
        ((@($health.lines) -join '').Trim() -eq 'healthy') -and
        $one.exit_code -eq 0 -and
        ((@($one.lines) -join '').Trim() -eq '1') -and
        $version.exit_code -eq 0 -and
        ((@($version.lines) -join '').Trim() -eq $script:ExpectedClickHouseVersion)
    )
    if (-not $ready) { throw 'Source ClickHouse is not exact-version healthy.' }
    $mountProbe = Invoke-NativeText 'docker' @('inspect','--format','{{json .Mounts}}',$containerId) -AllowFailure
    if ($mountProbe.exit_code -ne 0) { throw 'Unable to inspect source mount.' }
    try { $mounts = ((@($mountProbe.lines) -join "`n") | ConvertFrom-Json) }
    catch { throw 'Source mount JSON invalid.' }
    $matches = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    $mountReady = [bool]($matches.Count -eq 1 -and [string]$matches[0].Type -eq 'volume' -and [string]$matches[0].Name -eq $AcceptedVolume)
    Write-Host "accepted_production_mount_ready=$mountReady"
    if (-not $mountReady) { throw 'Accepted source volume identity changed.' }
    return [ordered]@{ container_id=$containerId; ready=$true }
}

function Invoke-ChJson([string]$Sql) {
    $probe = Invoke-NativeText 'docker' @('compose','exec','-T','clickhouse','clickhouse-client','--query',($Sql + ' FORMAT JSONEachRow'))
    $rows = @()
    foreach ($line in @($probe.lines | Where-Object { $_.Trim() })) { $rows += ($line | ConvertFrom-Json) }
    return @($rows)
}

function Get-SchemaSha([object]$Row) {
    return Get-StringSha256 (@(
        [string]$Row.table,
        [string]$Row.engine,
        [string]$Row.sorting_key,
        [string]$Row.primary_key,
        [string]$Row.partition_key,
        [string]$Row.create_table_query
    ) -join "`n")
}

function Get-PartSha([object[]]$Parts) {
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

function Get-ResidencySha([object[]]$Parts) {
    $lines = @()
    foreach ($part in @($Parts | Sort-Object name)) { $lines += "$([string]$part.name)|$([string]$part.disk_name)" }
    return Get-StringSha256 ($lines -join "`n")
}

function Get-SourceIdentity([object]$Plan) {
    $table = [string]$Plan.table
    $literal = Escape-Sql $table
    $tableRows = @(Invoke-ChJson "SELECT name AS table,engine,sorting_key,primary_key,partition_key,create_table_query FROM system.tables WHERE database='markorbit_facts' AND name='$literal'")
    if ($tableRows.Count -ne 1) { throw "Source table missing: $table" }
    $parts = @(Invoke-ChJson "SELECT partition_id,name,rows,bytes_on_disk,disk_name,hash_of_all_files,hash_of_uncompressed_files,uncompressed_hash_of_compressed_files FROM system.parts WHERE database='markorbit_facts' AND active AND table='$literal' ORDER BY partition_id,name")
    $rows = [int64]0
    $bytes = [int64]0
    foreach ($part in $parts) { $rows += [int64]$part.rows; $bytes += [int64]$part.bytes_on_disk }
    $schemaSha = Get-SchemaSha $tableRows[0]
    $partSha = Get-PartSha $parts
    $residencySha = Get-ResidencySha $parts
    $identity = Get-StringSha256 (@(
        $table,
        'all',
        $schemaSha,
        [string]$rows,
        [string]$bytes,
        [string][int64]$parts.Count,
        $partSha,
        $residencySha,
        [string]$rows,
        [string]$bytes,
        [string][int64]$parts.Count,
        $partSha,
        $residencySha,
        [string]$Plan.source_disk
    ) -join "`n")
    return [ordered]@{ rows=$rows; bytes=$bytes; parts=[int64]$parts.Count; sha=$identity }
}

function Resolve-Authority {
    $path = [IO.Path]::GetFullPath($AuthorityReviewReceiptPath)
    $sha = Get-FileSha256 $path
    if ($sha -ne $ExpectedAuthorityReviewReceiptSha256.Trim().ToLowerInvariant()) { throw 'Authority review SHA mismatch.' }
    $review = Read-Json $path 'Authority review'
    if ([string]$review.receipt_version -ne $script:ExpectedReviewVersion -or [string]$review.engine_sha -ne $script:ExpectedReviewEngineSha) { throw 'Authority review identity drift.' }
    if ([string]$review.decision -ne 'PRODUCTION_CN_WARM_PROVISIONING_AUTHORITY_REVIEW_READY_FOR_OPERATOR_GO' -or @($review.blockers).Count -ne 0) { throw 'Authority review not READY.' }
    if ([string]$review.accepted_design.receipt_sha256 -ne $script:ExpectedDesignReceiptSha256 -or [string]$review.accepted_design.warm_candidate_manifest_sha256 -ne $script:ExpectedWarmManifestSha256) { throw 'Design provenance drift.' }
    if ([string]$review.accepted_checksum.receipt_sha256 -ne $script:ExpectedChecksumReceiptSha256 -or [string]$review.accepted_checksum.checksum_manifest_sha256 -ne $script:ExpectedChecksumManifestSha256) { throw 'Checksum provenance drift.' }
    if ([int64]$review.frozen_workload.rows -ne $script:ExpectedWarmRows -or [int64]$review.frozen_workload.bytes -ne $script:ExpectedWarmBytes -or [int64]$review.frozen_workload.candidate_count -ne $script:ExpectedWarmCandidateCount) { throw 'Frozen workload drift.' }
    $designPath = [string]$review.accepted_design.receipt_path
    if (-not (Test-SameWindowsPath $designPath $script:ExpectedDesignReceiptPath) -or (Get-FileSha256 $designPath) -ne $script:ExpectedDesignReceiptSha256) { throw 'Design receipt file drift.' }
    $checksumPath = [string]$review.accepted_checksum.receipt_path
    if (-not (Test-SameWindowsPath $checksumPath $script:ExpectedChecksumReceiptPath) -or (Get-FileSha256 $checksumPath) -ne $script:ExpectedChecksumReceiptSha256) { throw 'Checksum receipt file drift.' }
    return [ordered]@{ path=$path; sha=$sha; receipt=$review; design=(Read-Json $designPath 'Design receipt') }
}

function Assert-LiveSource([object]$Authority) {
    $plans = @($Authority.design.candidates | Sort-Object migration_order)
    if ($plans.Count -ne 4) { throw 'Design candidates drift.' }
    foreach ($plan in $plans) {
        $live = Get-SourceIdentity $plan
        $expected = @($script:ExpectedSourceIdentities | Where-Object { $_.table -eq [string]$plan.table })
        if ($expected.Count -ne 1 -or $live.rows -ne $expected[0].rows -or $live.bytes -ne $expected[0].bytes -or $live.parts -ne $expected[0].parts -or $live.sha -ne $expected[0].sha) {
            throw "SOURCE_IDENTITY_DRIFT:$($plan.table)"
        }
        Write-Host "source_identity_ready=$($plan.table)|all|rows=$($live.rows)|parts=$($live.parts)|source_identity_sha256=$($live.sha)"
    }
}

function Assert-Capacity {
    $drive = New-Object IO.DriveInfo('E:\')
    if (-not $drive.IsReady -or [int64]$drive.TotalSize -ne $script:ExpectedETotalBytes) { throw 'E capacity identity drift.' }
    $reserve = [int64][math]::Ceiling([double]$drive.TotalSize * 0.30)
    $margin = [int64]($drive.AvailableFreeSpace - $script:ExpectedWarmVhdxMaxBytes - $reserve)
    Write-Host "e_total_bytes=$($drive.TotalSize)"
    Write-Host "e_free_bytes=$($drive.AvailableFreeSpace)"
    Write-Host "e_margin_after_proposed_max_bytes=$margin"
    if ($margin -lt 0) { throw 'E_30_PERCENT_RESERVE_ADMISSION_FAILED' }
}

function Assert-Protected {
    foreach ($path in $script:ProtectedPaths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Protected path missing: $path" }
    }
    $recovery = New-Object IO.FileInfo($script:ExpectedFRecoveryVhdx)
    if ([int64]$recovery.Length -ne $script:ExpectedFRecoveryBytes) { throw 'F recovery VHDX changed.' }
    if (Test-Path -LiteralPath $script:EBackupRoot) { throw 'Superseded E backup root reappeared.' }
}

function Get-MountUuid([string]$Distro) {
    $command = "src=`$(findmnt -n -o SOURCE '$($script:ExpectedWarmMountPath)') || exit 10; blkid -s UUID -o value `"`$src`""
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','sh','-lc',$command) -AllowFailure
    if ($probe.exit_code -ne 0) { return '' }
    return (@($probe.lines) -join '').Trim()
}

function Assert-WarmEmpty([string]$Distro) {
    $command = "test -d '$($script:ExpectedWarmDiskPath)' || exit 10; if find '$($script:ExpectedWarmDiskPath)' -mindepth 1 -print -quit | grep -q .; then exit 20; fi"
    $probe = Invoke-NativeText 'wsl.exe' @('-d',$Distro,'-u','root','--','sh','-lc',$command) -AllowFailure
    if ($probe.exit_code -ne 0) { throw 'Warm clickhouse-data is not empty.' }
}

function Resolve-Incident([object]$Authority) {
    $directory = [IO.Path]::GetFullPath($IncidentEvidenceDirectory)
    if (-not (Test-SameWindowsPath $directory $script:ExpectedIncidentEvidenceDirectory)) { throw 'Incident evidence directory mismatch.' }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw 'Incident evidence directory missing.' }
    $journalPath = Join-Path $directory 'production_cn_warm_phase_a_provisioning_journal.json'
    $journalSha = Get-FileSha256 $journalPath
    $journal = Read-Json $journalPath 'Incident journal'
    if ([string]$journal.receipt_version -ne $script:IncidentJournalVersion -or
        [string]$journal.engine_sha -ne $script:IncidentEngineSha -or
        [string]$journal.authority_review_sha256 -ne $Authority.sha -or
        [string]$journal.operator_go_comment_id -ne $script:OperatorGoCommentId -or
        [string]$journal.operator_go_token_sha256 -ne (Get-StringSha256 $script:ExpectedOperatorGoToken)) {
        throw 'Incident journal provenance mismatch.'
    }
    if ([string]$journal.stage -ne 'blocked' -or [string]$journal.last_error -ne $script:ExpectedIncidentError) { throw 'Incident journal is not exact mount-visibility failure.' }
    foreach ($name in @('vhdx_create_started','vhdx_created','ext4_format_started','ext4_formatted','named_mount_started','named_mount_ready','tooling_export_started','tooling_exported','runtime_import_started')) {
        if (-not [bool]($journal.$name)) { throw "Incident journal prerequisite false: $name" }
    }
    foreach ($name in @('runtime_imported','clickhouse_install_started','clickhouse_installed','config_write_started','config_written','server_start_started','server_started','storage_foundation_ready','completed','cn_data_transfer_performed','cross_runtime_transfer_performed','cn_warm_move_performed','source_cleanup_performed','accepted_volume_mutation_performed','source_clickhouse_mutation_performed','docker_mutation_performed','cn_replay_performed','us_bulk_performed','no_arg_wsl_unmount_performed','wsl_shutdown_performed','runtime_distro_unregister_performed','target_vhdx_delete_performed')) {
        if ([bool]($journal.$name)) { throw "Incident journal forbidden/unexpected flag true: $name" }
    }
    if ([string]::IsNullOrWhiteSpace([string]$journal.ext4_uuid)) { throw 'Incident ext4 UUID missing.' }
    $exportTar = Join-Path $directory 'tooling-rootfs.tar'
    if ((Get-FileSha256 $exportTar) -ne [string]$journal.tooling_export_tar_sha256) { throw 'Incident tooling export SHA drift.' }
    return [ordered]@{ dir=$directory; journal_path=$journalPath; journal_sha256=$journalSha; journal=$journal }
}

function Get-IncidentPhysicalState([object]$Incident) {
    if (-not (Test-Path -LiteralPath $script:ExpectedWarmVhdxPath -PathType Leaf)) { throw 'Incident Warm VHDX missing.' }
    $distros = @(Get-WslDistros)
    $runtime = @($distros | Where-Object { $_.name -eq $script:ExpectedRuntimeDistro })
    if ($runtime.Count -ne 1 -or $runtime[0].version -ne 2 -or -not (Test-SameWindowsPath $runtime[0].base_path $script:ExpectedRuntimeRoot)) { throw 'Incident runtime identity mismatch.' }
    $runtimeProbe = Invoke-RuntimeShell 'printf RUNTIME_OK' -AllowFailure
    if ($runtimeProbe.exit_code -ne 0 -or ((@($runtimeProbe.lines) -join '').Trim() -ne 'RUNTIME_OK')) { throw 'Incident runtime cannot start.' }

    $toolingMount = Get-MountProbe $ToolingDistro $script:ExpectedWarmMountName
    $runtimeMount = Get-MountProbe $script:ExpectedRuntimeDistro $script:ExpectedWarmMountName

    if ($runtimeMount.exit_code -eq 0) {
        if (-not $runtimeMount.ready) { throw "Incident runtime mount name is present but not ext4: $($runtimeMount.output)" }
        throw 'Incident mount visibility failure is no longer present; refusing blind remediation.'
    }
    if ($toolingMount.exit_code -eq 0 -and -not $toolingMount.ready) { throw "Incident tooling mount name is present but not ext4: $($toolingMount.output)" }

    $state = $null
    $uuid = ''
    if ($toolingMount.ready) {
        $state = $script:ToolingOnlyMountState
        $uuid = Get-MountUuid $ToolingDistro
        if ($uuid -ne [string]$Incident.journal.ext4_uuid) { throw 'Incident ext4 UUID mismatch.' }
        Assert-WarmEmpty $ToolingDistro
    }
    else {
        $state = $script:AlreadyDetachedMountState
    }

    if ((Test-PortListening $script:TargetHttpPort) -or (Test-PortListening $script:TargetNativePort)) { throw 'Target port collision before remediation.' }
    Write-Host "incident_mount_state=$state"
    if ($uuid) { Write-Host "incident_ext4_uuid=$uuid" }
    Write-Host 'incident_partial_state_ready=True'
    return [ordered]@{
        state=$state
        tooling_mount_ready=[bool]$toolingMount.ready
        runtime_mount_ready=[bool]$runtimeMount.ready
        ext4_uuid=$uuid
    }
}

function New-RemediationJournal([object]$Incident,[object]$Authority,[object]$PhysicalState) {
    $detached = [bool]([string]$PhysicalState.state -eq $script:AlreadyDetachedMountState)
    return [ordered]@{
        receipt_version=$script:RemediationJournalVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        incident_engine_sha=$script:IncidentEngineSha
        incident_journal_path=$Incident.journal_path
        incident_journal_sha256=$Incident.journal_sha256
        authority_review_sha256=$Authority.sha
        initial_mount_state=[string]$PhysicalState.state
        stage='ready'
        exact_path_unmount_performed=$false
        exact_path_unmount_skipped_already_detached=$detached
        named_remount_performed=$false
        tooling_mount_ready=$false
        runtime_mount_ready=$false
        ext4_uuid_verified=$false
        warm_empty_verified=$false
        clickhouse_installed=$false
        clickhouse_package_sha256=$null
        config_written=$false
        server_started=$false
        storage_foundation_ready=$false
        completed=$false
        last_error=$null
        no_arg_wsl_unmount_performed=$false
        wsl_shutdown_performed=$false
        runtime_distro_unregister_performed=$false
        target_vhdx_delete_performed=$false
        vhdx_create_performed=$false
        vhdx_format_performed=$false
        runtime_import_performed=$false
        docker_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        source_clickhouse_mutation_performed=$false
        cn_data_transfer_performed=$false
        cross_runtime_transfer_performed=$false
        cn_warm_move_performed=$false
        source_cleanup_performed=$false
        cn_replay_performed=$false
        us_bulk_performed=$false
    }
}

function Assert-RemediationJournal([object]$Journal,[object]$Incident,[object]$Authority) {
    if ([string]$Journal.receipt_version -ne $script:RemediationJournalVersion -or
        [string]$Journal.engine_sha -ne $ExpectedMainSha.Trim().ToLowerInvariant() -or
        [string]$Journal.incident_engine_sha -ne $script:IncidentEngineSha -or
        [string]$Journal.incident_journal_sha256 -ne $Incident.journal_sha256 -or
        [string]$Journal.authority_review_sha256 -ne $Authority.sha) {
        throw 'Remediation journal provenance drift.'
    }
    $initialState = [string]$Journal.initial_mount_state
    if ($initialState -notin @($script:ToolingOnlyMountState,$script:AlreadyDetachedMountState)) { throw 'Remediation journal initial mount state invalid.' }
    if ($initialState -eq $script:AlreadyDetachedMountState -and -not [bool]$Journal.exact_path_unmount_skipped_already_detached) { throw 'Detached remediation journal must record skipped exact-path unmount.' }
    if ($initialState -eq $script:ToolingOnlyMountState -and [bool]$Journal.exact_path_unmount_skipped_already_detached) { throw 'Tooling-only remediation journal cannot skip exact-path unmount.' }
    if ([bool]$Journal.exact_path_unmount_performed -and [bool]$Journal.exact_path_unmount_skipped_already_detached) { throw 'Remediation journal cannot both perform and skip exact-path unmount.' }
    foreach ($name in @('no_arg_wsl_unmount_performed','wsl_shutdown_performed','runtime_distro_unregister_performed','target_vhdx_delete_performed','vhdx_create_performed','vhdx_format_performed','runtime_import_performed','docker_mutation_performed','accepted_volume_mutation_performed','source_clickhouse_mutation_performed','cn_data_transfer_performed','cross_runtime_transfer_performed','cn_warm_move_performed','source_cleanup_performed','cn_replay_performed','us_bulk_performed')) {
        if ([bool]($Journal.$name)) { throw "Remediation journal forbidden flag true: $name" }
    }
}

function Assert-RemountedState([object]$Incident) {
    $toolingMount = Get-MountProbe $ToolingDistro $script:ExpectedWarmMountName
    $runtimeMount = Get-MountProbe $script:ExpectedRuntimeDistro $script:ExpectedWarmMountName
    if (-not $toolingMount.ready -or -not $runtimeMount.ready) { throw "Warm ext4 not visible in both distros. tooling=$($toolingMount.output) runtime=$($runtimeMount.output)" }
    $uuid = Get-MountUuid $script:ExpectedRuntimeDistro
    if ($uuid -ne [string]$Incident.journal.ext4_uuid) { throw 'Remounted ext4 UUID mismatch.' }
    Assert-WarmEmpty $script:ExpectedRuntimeDistro
    return $uuid
}

function Invoke-ContractFixture {
    if ($script:IncidentEngineSha -ne '111908335714292ae4d42e54b3664156d19d64ca') { throw 'Incident engine contract drift.' }
    if ($script:ExpectedIncidentError -ne 'Production runtime cannot see named Warm ext4 mount.') { throw 'Incident error contract drift.' }
    if ($script:RemediationIssue -ne 510) { throw 'Remediation issue contract drift.' }
    if ($script:ToolingOnlyMountState -ne 'TOOLING_ONLY_MOUNT_VISIBLE' -or $script:AlreadyDetachedMountState -ne 'MOUNT_ALREADY_DETACHED') { throw 'Remediation mount-state contract drift.' }
    if ($script:AllowedRemediationFiles.Count -ne 3) { throw 'Remediation file boundary drift.' }
    Write-Host 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_CONTRACT_OK'
}

$remediationJournal = $null
$remediationJournalPath = $null
$incident = $null
$physicalState = $null
try {
    Write-Host '===== PRODUCTION CN WARM PHASE A MOUNT REMEDIATION ====='
    Write-Host "remediation_issue=$script:RemediationIssue"
    Write-Host "incident_engine_sha=$script:IncidentEngineSha"
    Write-Host "apply_requested=$([bool]$Apply)"
    foreach ($marker in @(
        'fresh_provisioning_authorized=False',
        'vhdx_create_authorized=False',
        'vhdx_format_authorized=False',
        'vhdx_delete_authorized=False',
        'runtime_import_authorized=False',
        'runtime_unregister_authorized=False',
        'wsl_shutdown_authorized=False',
        'no_arg_wsl_unmount_authorized=False',
        'docker_mutation_authorized=False',
        'accepted_volume_mutation_authorized=False',
        'source_clickhouse_mutation_authorized=False',
        'cn_data_transfer_authorized=False',
        'cross_runtime_transfer_authorized=False',
        'cn_warm_move_authorized=False',
        'source_cleanup_authorized=False',
        'cn_replay_authorized=False',
        'us_bulk_authorized=False'
    )) { Write-Host $marker }

    if ($ContractOnly) { Invoke-ContractFixture; exit 0 }
    if ($OperatorGoToken -ne $script:ExpectedOperatorGoToken) { throw 'Original Phase A GO token mismatch.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Remediation must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'
    Assert-RemediationProvenance

    $authority = Resolve-Authority
    Assert-RawConsumersStopped
    [void](Get-SourceHealth)
    Assert-LiveSource $authority
    Assert-Protected
    Assert-Capacity
    $defaultDistroBefore = Get-DefaultWslDistro
    $envPath = Join-Path $repoRoot '.env'
    $envShaBefore = Get-FileSha256 $envPath
    $incident = Resolve-Incident $authority

    $remediationJournalPath = Join-Path $incident.dir 'production_cn_warm_phase_a_mount_remediation_journal.json'
    if (Test-Path -LiteralPath $remediationJournalPath -PathType Leaf) {
        $remediationJournal = Read-Json $remediationJournalPath 'Remediation journal'
        Assert-RemediationJournal $remediationJournal $incident $authority
        Write-Host "remediation_resume_stage=$($remediationJournal.stage)"
        Write-Host "initial_mount_state=$($remediationJournal.initial_mount_state)"
    }
    else {
        $physicalState = Get-IncidentPhysicalState $incident
        if (-not $Apply) {
            Write-Host "Incident evidence directory: $($incident.dir)"
            Write-Host "incident_journal_sha256=$($incident.journal_sha256)"
            Write-Host 'decision=PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_READY_FOR_APPLY'
            Write-Host 'mutation_performed=False'
            Write-Host 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_DONE'
            exit 0
        }
        $remediationJournal = New-RemediationJournal $incident $authority $physicalState
        Write-Json $remediationJournal $remediationJournalPath
    }

    Write-Host "Incident evidence directory: $($incident.dir)"
    Write-Host "incident_journal_sha256=$($incident.journal_sha256)"
    Write-Host "remediation_journal_path=$remediationJournalPath"

    if (-not $Apply) { throw 'Existing remediation journal requires -Apply resume.' }
    $admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Remediation Apply requires Administrator PowerShell.' }

    if (-not [bool]$remediationJournal.exact_path_unmount_performed -and -not [bool]$remediationJournal.exact_path_unmount_skipped_already_detached) {
        $physicalState = Get-IncidentPhysicalState $incident
        if ([string]$physicalState.state -ne $script:ToolingOnlyMountState) { throw 'Exact-path unmount is allowed only from tooling-only incident mount state.' }
        $remediationJournal.stage = 'exact_path_unmount'
        Write-Json $remediationJournal $remediationJournalPath
        if (-not (Dismount-ExactWarmVhdx)) { throw 'Exact-path Warm VHDX unmount failed.' }
        $remediationJournal.exact_path_unmount_performed = $true
        Write-Json $remediationJournal $remediationJournalPath
        Start-Sleep -Seconds 1
    }

    if (-not [bool]$remediationJournal.named_remount_performed) {
        $toolingBefore = Get-MountProbe $ToolingDistro $script:ExpectedWarmMountName
        $runtimeBefore = Get-MountProbe $script:ExpectedRuntimeDistro $script:ExpectedWarmMountName
        if ($toolingBefore.exit_code -eq 0 -or $runtimeBefore.exit_code -eq 0) { throw "Warm mount name unexpectedly present before recorded remount. tooling=$($toolingBefore.output) runtime=$($runtimeBefore.output)" }
        $runtimeProbe = Invoke-RuntimeShell 'printf RUNTIME_OK' -AllowFailure
        if ($runtimeProbe.exit_code -ne 0 -or ((@($runtimeProbe.lines) -join '').Trim() -ne 'RUNTIME_OK')) { throw 'Existing production runtime is not startable before remount.' }
        $remediationJournal.stage = 'named_remount_after_runtime_start'
        Write-Json $remediationJournal $remediationJournalPath
        $mount = Invoke-NativeText 'wsl.exe' @('--mount','--vhd',$script:ExpectedWarmVhdxPath,'--name',$script:ExpectedWarmMountName) -AllowFailure
        if ($mount.exit_code -ne 0) { throw "Exact Warm remount failed: $($mount.lines -join [Environment]::NewLine)" }
        $remediationJournal.named_remount_performed = $true
        Write-Json $remediationJournal $remediationJournalPath
        Start-Sleep -Seconds 1
    }

    $uuid = Assert-RemountedState $incident
    $remediationJournal.tooling_mount_ready = $true
    $remediationJournal.runtime_mount_ready = $true
    $remediationJournal.ext4_uuid_verified = $true
    $remediationJournal.warm_empty_verified = $true
    Write-Json $remediationJournal $remediationJournalPath

    if (-not [bool]$remediationJournal.clickhouse_installed) {
        $remediationJournal.stage = 'install_clickhouse'
        Write-Json $remediationJournal $remediationJournalPath
        $packageName = "clickhouse-common-static_$($script:ExpectedClickHouseVersion)_amd64.deb"
        $packageUrl = "https://packages.clickhouse.com/deb/pool/main/c/clickhouse/$packageName"
        $command = "set -eu; curl -fL --retry 3 --connect-timeout 15 '$packageUrl' -o '/tmp/$packageName'; sha256sum '/tmp/$packageName'; dpkg-deb -f '/tmp/$packageName' Version | grep -Fx '$($script:ExpectedClickHouseVersion)' >/dev/null; if clickhouse client --version 2>/dev/null | grep -F '$($script:ExpectedClickHouseVersion)' >/dev/null; then echo PACKAGE_INSTALL_SKIPPED_EXACT_VERSION; else dpkg -i '/tmp/$packageName' >/tmp/markorbit-clickhouse-prod-dpkg.log 2>&1; echo PACKAGE_INSTALL_PERFORMED; fi; clickhouse client --version"
        $install = Invoke-RuntimeShell $command -AllowFailure
        if ($install.exit_code -ne 0) { throw "ClickHouse install failed: $($install.lines -join [Environment]::NewLine)" }
        $packageSha = $null
        foreach ($line in $install.lines) {
            if ($line -match '^([0-9a-fA-F]{64})\s+') { $packageSha = $Matches[1].ToLowerInvariant() }
        }
        if (-not $packageSha) { throw 'Package SHA not captured.' }
        $versionProbe = Invoke-RuntimeText @('clickhouse','client','--version') -AllowFailure
        if ($versionProbe.exit_code -ne 0 -or ((@($versionProbe.lines) -join ' ') -notmatch [regex]::Escape($script:ExpectedClickHouseVersion))) { throw 'Exact ClickHouse version not installed.' }
        $remediationJournal.clickhouse_package_sha256 = $packageSha
        $remediationJournal.clickhouse_installed = $true
        Write-Json $remediationJournal $remediationJournalPath
    }
    else {
        $packageSha = [string]$remediationJournal.clickhouse_package_sha256
        if ([string]::IsNullOrWhiteSpace($packageSha)) { throw 'Recorded package SHA missing.' }
    }

    if (-not [bool]$remediationJournal.config_written) {
        $remediationJournal.stage = 'write_target_config'
        Write-Json $remediationJournal $remediationJournalPath
        $source = Get-SourceHealth
        $baseConfig = Join-Path $incident.dir 'remediated-source-config.xml'
        $copy = Invoke-NativeText 'docker' @('cp',"$($source.container_id):/etc/clickhouse-server/config.xml",$baseConfig) -AllowFailure
        if ($copy.exit_code -ne 0 -or -not (Test-Path -LiteralPath $baseConfig -PathType Leaf)) { throw 'Unable to copy source ClickHouse config.' }
        $usersPath = Join-Path $incident.dir 'remediated-target-users.xml'
        $usersXml = @'
<clickhouse>
  <profiles><default/></profiles>
  <users><default><password></password><networks><ip>127.0.0.1</ip><ip>::1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default></users>
  <quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas>
</clickhouse>
'@
        Write-Utf8NoBom $usersPath $usersXml
        $overridePath = Join-Path $incident.dir 'remediated-target-override.xml'
        $overrideXml = @"
<clickhouse>
  <listen_host replace="replace">127.0.0.1</listen_host>
  <http_port replace="replace">$($script:TargetHttpPort)</http_port>
  <tcp_port replace="replace">$($script:TargetNativePort)</tcp_port>
  <path replace="replace">$($script:RuntimeDataDir)/</path>
  <tmp_path replace="replace">$($script:RuntimeDataDir)/tmp/</tmp_path>
  <user_files_path replace="replace">$($script:RuntimeDataDir)/user_files/</user_files_path>
  <format_schema_path replace="replace">$($script:RuntimeDataDir)/format_schemas/</format_schema_path>
  <storage_configuration>
    <disks><warm_cn><type>local</type><path>$($script:ExpectedWarmDiskPath)</path></warm_cn></disks>
    <policies><warm_cn_only><volumes><main><disk>warm_cn</disk></main></volumes></warm_cn_only></policies>
  </storage_configuration>
</clickhouse>
"@
        Write-Utf8NoBom $overridePath $overrideXml
        $baseWsl = Convert-WindowsPathToWsl $baseConfig
        $usersWsl = Convert-WindowsPathToWsl $usersPath
        $overrideWsl = Convert-WindowsPathToWsl $overridePath
        $prepareCommand = "set -eu; mkdir -p '$($script:RuntimeInstallDir)/etc/config.d' '$($script:RuntimeDataDir)/tmp' '$($script:RuntimeDataDir)/user_files' '$($script:RuntimeDataDir)/format_schemas' '/var/log/clickhouse-server'; cp '$baseWsl' '$($script:RuntimeInstallDir)/etc/config.xml'; cp '$usersWsl' '$($script:RuntimeInstallDir)/etc/users.xml'; cp '$overrideWsl' '$($script:RuntimeInstallDir)/etc/config.d/markorbit-production-warm.xml'; chmod 0644 '$($script:RuntimeInstallDir)/etc/config.xml' '$($script:RuntimeInstallDir)/etc/users.xml' '$($script:RuntimeInstallDir)/etc/config.d/markorbit-production-warm.xml'"
        $prepare = Invoke-RuntimeShell $prepareCommand -AllowFailure
        if ($prepare.exit_code -ne 0) { throw 'Target config write failed.' }
        $remediationJournal.config_written = $true
        Write-Json $remediationJournal $remediationJournalPath
    }

    if (-not [bool]$remediationJournal.server_started) {
        $existing = Invoke-TargetSql 'SELECT 1' -AllowFailure
        if ($existing.exit_code -eq 0 -and ((@($existing.lines) -join '').Trim() -eq '1')) {
            $remediationJournal.server_started = $true
            Write-Json $remediationJournal $remediationJournalPath
        }
        else {
            $remediationJournal.stage = 'start_target_clickhouse'
            Write-Json $remediationJournal $remediationJournalPath
            $startCommand = "set -eu; rm -f '$($script:RuntimeInstallDir)/server.pid'; nohup clickhouse server --config-file='$($script:RuntimeInstallDir)/etc/config.xml' >'$($script:RuntimeInstallDir)/console.log' 2>&1 & echo `"`$!`" >'$($script:RuntimeInstallDir)/server.pid'"
            $start = Invoke-RuntimeShell $startCommand -AllowFailure
            if ($start.exit_code -ne 0) { throw 'Target ClickHouse start failed.' }
            $ready = $false
            for ($attempt = 0; $attempt -lt 45; $attempt++) {
                $query = Invoke-TargetSql 'SELECT 1' -AllowFailure
                if ($query.exit_code -eq 0 -and ((@($query.lines) -join '').Trim() -eq '1')) {
                    $ready = $true
                    break
                }
                Start-Sleep -Seconds 2
            }
            if (-not $ready) {
                $log = Invoke-RuntimeShell "tail -n 200 '$($script:RuntimeInstallDir)/console.log' 2>/dev/null || true" -AllowFailure
                throw "Target ClickHouse not ready: $($log.lines -join [Environment]::NewLine)"
            }
            $remediationJournal.server_started = $true
            Write-Json $remediationJournal $remediationJournalPath
        }
    }

    $version = Invoke-TargetSql 'SELECT version()'
    $targetVersion = (@($version.lines) -join '').Trim()
    if ($targetVersion -ne $script:ExpectedClickHouseVersion) { throw 'Target version mismatch.' }

    $disk = Invoke-TargetSql "SELECT name,path FROM system.disks WHERE name='warm_cn' FORMAT TSV"
    $diskText = (@($disk.lines) -join '').Trim()
    $diskFields = @($diskText -split "`t",2)
    if ($diskFields.Count -ne 2 -or $diskFields[0] -ne 'warm_cn' -or $diskFields[1].TrimEnd('/') -ne $script:ExpectedWarmDiskPath.TrimEnd('/')) { throw "Target warm_cn disk mismatch: $diskText" }

    $policy = Invoke-TargetSql "SELECT policy_name,volume_name,arrayStringConcat(disks, ',') FROM system.storage_policies WHERE policy_name='warm_cn_only' FORMAT TSV"
    $policyText = (@($policy.lines) -join '').Trim()
    if ($policyText -ne "warm_cn_only`tmain`twarm_cn") { throw "Policy mismatch: $policyText" }

    $parts = Invoke-TargetSql "SELECT count() FROM system.parts WHERE disk_name='warm_cn'"
    $warmParts = [int64]((@($parts.lines) -join '').Trim())
    if ($warmParts -ne 0) { throw "Warm parts not empty: $warmParts" }
    Assert-WarmEmpty $script:ExpectedRuntimeDistro

    $remediationJournal.storage_foundation_ready = $true
    $remediationJournal.completed = $true
    $remediationJournal.stage = 'complete'
    Write-Json $remediationJournal $remediationJournalPath

    Assert-RawConsumersStopped
    [void](Get-SourceHealth)
    Assert-LiveSource $authority
    Assert-Protected
    Assert-Capacity
    if ((Get-FileSha256 $envPath) -ne $envShaBefore) { throw '.env changed.' }
    if ((Get-DefaultWslDistro) -ne $defaultDistroBefore) { throw 'Default WSL distro changed.' }
    Assert-ExactMain 'final'

    $receiptPath = Join-Path $incident.dir 'production_cn_warm_phase_a_mount_remediation.json'
    $receipt = [ordered]@{
        receipt_version=$script:RemediationReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_COMPLETE'
        next_gate='PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE'
        incident=[ordered]@{
            engine_sha=$script:IncidentEngineSha
            journal_path=$incident.journal_path
            journal_sha256=$incident.journal_sha256
            failure=$script:ExpectedIncidentError
            initial_mount_state=[string]$remediationJournal.initial_mount_state
        }
        authority_review_sha256=$authority.sha
        topology=[ordered]@{
            warm_vhdx_path=$script:ExpectedWarmVhdxPath
            ext4_uuid=$uuid
            mount_name=$script:ExpectedWarmMountName
            runtime_distro=$script:ExpectedRuntimeDistro
            runtime_root=$script:ExpectedRuntimeRoot
            clickhouse_version=$targetVersion
            clickhouse_package_sha256=$packageSha
            disk='warm_cn'
            policy='warm_cn_only'
            http_port=$script:TargetHttpPort
            native_port=$script:TargetNativePort
        }
        storage_foundation=[ordered]@{
            system_disk=$diskText
            system_policy=$policyText
            warm_part_count=$warmParts
            empty_for_cn_migration=$true
        }
        exact_path_unmount_performed=[bool]$remediationJournal.exact_path_unmount_performed
        exact_path_unmount_skipped_already_detached=[bool]$remediationJournal.exact_path_unmount_skipped_already_detached
        named_remount_performed=[bool]$remediationJournal.named_remount_performed
        no_arg_wsl_unmount_performed=$false
        wsl_shutdown_performed=$false
        runtime_distro_unregister_performed=$false
        target_vhdx_delete_performed=$false
        vhdx_create_performed=$false
        vhdx_format_performed=$false
        runtime_import_performed=$false
        docker_mutation_performed=$false
        accepted_volume_mutation_performed=$false
        source_clickhouse_mutation_performed=$false
        cn_data_transfer_performed=$false
        cross_runtime_transfer_performed=$false
        cn_warm_move_performed=$false
        source_cleanup_performed=$false
        cn_replay_performed=$false
        us_bulk_performed=$false
        remediation_journal_path=$remediationJournalPath
    }
    Write-Json $receipt $receiptPath

    $phaseAPath = Join-Path $incident.dir 'production_cn_warm_phase_a_provisioning_remediated.json'
    $phaseA = [ordered]@{
        receipt_version=$script:PhaseAReceiptVersion
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        decision='PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE'
        next_gate='PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE'
        incident_journal_sha256=$incident.journal_sha256
        initial_mount_state=[string]$remediationJournal.initial_mount_state
        remediation_receipt_path=$receiptPath
        remediation_receipt_sha256=(Get-FileSha256 $receiptPath)
        warm_vhdx_path=$script:ExpectedWarmVhdxPath
        ext4_uuid=$uuid
        target_runtime_distro=$script:ExpectedRuntimeDistro
        target_clickhouse_version=$targetVersion
        target_clickhouse_package_sha256=$packageSha
        target_http_port=$script:TargetHttpPort
        target_native_port=$script:TargetNativePort
        warm_part_count=$warmParts
        exact_path_unmount_performed=[bool]$remediationJournal.exact_path_unmount_performed
        exact_path_unmount_skipped_already_detached=[bool]$remediationJournal.exact_path_unmount_skipped_already_detached
        named_remount_performed=[bool]$remediationJournal.named_remount_performed
        cn_data_transfer_performed=$false
        cross_runtime_transfer_performed=$false
        cn_warm_move_performed=$false
        source_cleanup_performed=$false
        accepted_volume_mutation_performed=$false
        source_clickhouse_mutation_performed=$false
        docker_mutation_performed=$false
    }
    Write-Json $phaseA $phaseAPath
    Assert-ExactMain 'exit'

    Write-Host '===== PRODUCTION CN WARM PHASE A MOUNT REMEDIATION RESULT ====='
    Write-Host 'decision=PRODUCTION_CN_WARM_PHASE_A_PROVISIONING_APPLY_COMPLETE'
    Write-Host 'next_gate=PRODUCTION_CN_WARM_EMPTY_DISK_STORAGE_POLICY_ACCEPTANCE'
    Write-Host 'mount_remediation_performed=True'
    Write-Host "initial_mount_state=$($remediationJournal.initial_mount_state)"
    Write-Host "exact_path_unmount_performed=$([bool]$remediationJournal.exact_path_unmount_performed)"
    Write-Host "exact_path_unmount_skipped_already_detached=$([bool]$remediationJournal.exact_path_unmount_skipped_already_detached)"
    Write-Host "named_remount_performed=$([bool]$remediationJournal.named_remount_performed)"
    Write-Host "ext4_uuid=$uuid"
    Write-Host "target_clickhouse_version=$targetVersion"
    Write-Host "warm_part_count=$warmParts"
    foreach ($marker in @(
        'no_arg_wsl_unmount_performed=False',
        'wsl_shutdown_performed=False',
        'runtime_distro_unregister_performed=False',
        'target_vhdx_delete_performed=False',
        'vhdx_create_performed=False',
        'vhdx_format_performed=False',
        'runtime_import_performed=False',
        'docker_mutation_performed=False',
        'accepted_volume_mutation_performed=False',
        'source_clickhouse_mutation_performed=False',
        'cn_data_transfer_performed=False',
        'cross_runtime_transfer_performed=False',
        'cn_warm_move_performed=False',
        'source_cleanup_performed=False',
        'cn_replay_performed=False',
        'us_bulk_performed=False'
    )) { Write-Host $marker }
    Write-Host "remediation_receipt_path=$receiptPath"
    Write-Host "phase_a_receipt_path=$phaseAPath"
    Write-Host "remediation_journal_path=$remediationJournalPath"
    Write-Host 'PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_DONE'
    exit 0
}
catch {
    if ($remediationJournal -and $remediationJournalPath) {
        try {
            $remediationJournal.last_error = $_.Exception.Message
            $remediationJournal.stage = 'blocked'
            Write-Json $remediationJournal $remediationJournalPath
        }
        catch {}
    }
    Write-Host "PRODUCTION_CN_WARM_PHASE_A_MOUNT_REMEDIATION_FAILED: $($_.Exception.Message)"
    if ($incident) { Write-Host "Incident evidence directory: $($incident.dir)" }
    if ($remediationJournalPath) { Write-Host "remediation_journal_path=$remediationJournalPath" }
    exit 2
}
finally {
    Pop-Location
}
