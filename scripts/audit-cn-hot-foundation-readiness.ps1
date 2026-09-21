[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$EvidenceRoot = 'reports',
    [switch]$ContractOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$script:ReceiptVersion = 'CN_HOT_FOUNDATION_READINESS_V1'
$script:Database = 'markorbit_facts'
$script:TargetDistro = 'MarkOrbit-ClickHouse'
$script:TargetHost = '127.0.0.1'
$script:TargetPort = '29000'
$script:HotCnDisk = 'hot_cn'
$script:HotCnPolicy = 'hot_cn_only'
$script:HotUsDisk = 'hot_us'
$script:HotUsPolicy = 'hot_us_only'
$script:WarmCnDisk = 'warm_cn'
$script:WarmCnPolicy = 'warm_cn_only'
$script:ProductionHotRoot = 'D:\MarkOrbitData\production\clickhouse'
$script:RecommendedReserveRatio = 0.30
$script:HardReserveRatio = 0.20
$script:HotRequiredTables = @(
    'cn_case_current',
    'cn_case_scope_current',
    'cn_case_party_current',
    'cn_goods_item_current',
    'cn_goods_scope_lifecycle_current',
    'cn_observed_event'
)
$script:WarmCandidateTables = @('cn_goods_item_observation')

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
    $lines = @($output | ForEach-Object { $_.ToString() })
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$Command failed with exit code $($exitCode): $($lines -join [Environment]::NewLine)"
    }
    return [ordered]@{ exit_code=$exitCode; lines=@($lines) }
}

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift during $Phase. expected=$expected head=$head origin_main=$originMain"
    }
    if (git status --porcelain=v1) {
        throw "Working tree must be clean during $Phase."
    }
}

function Assert-ReadOnlyMetadataSql([string]$Sql, [string]$Label) {
    $normalized = ' ' + (($Sql -replace '\s+', ' ').Trim().ToUpperInvariant()) + ' '
    if (-not $normalized.TrimStart().StartsWith('SELECT ')) { throw "$Label is not SELECT-only." }
    if ($normalized -notmatch ' SYSTEM\.(PARTS|TABLES|DISKS|STORAGE_POLICIES)') {
        throw "$Label may inspect ClickHouse system metadata only."
    }
    foreach ($token in @(
        ' INSERT ', ' DELETE ', ' UPDATE ', ' CREATE ', ' DROP ', ' TRUNCATE ',
        ' OPTIMIZE ', ' ALTER ', ' MOVE ', ' ATTACH ', ' DETACH ', ' RENAME ', ' KILL '
    )) {
        if ($normalized.Contains($token)) { throw "$Label contains forbidden token: $($token.Trim())" }
    }
}

function Convert-JsonLines([string[]]$Lines, [string]$Label) {
    $rows = @()
    foreach ($line in @($Lines)) {
        $text = ([string]$line).Trim()
        if (-not $text) { continue }
        try { $rows += ($text | ConvertFrom-Json) }
        catch { throw "$Label returned invalid JSONEachRow: $text" }
    }
    return @($rows)
}

function Invoke-SourceRows([string]$Sql, [string]$Label) {
    Assert-ReadOnlyMetadataSql $Sql $Label
    $probe = Invoke-NativeText 'docker' @(
        'compose','exec','-T','clickhouse','clickhouse-client',
        '--query',($Sql + ' FORMAT JSONEachRow')
    )
    return @(Convert-JsonLines $probe.lines $Label)
}

function Get-RunningWslNames {
    $probe = Invoke-NativeText 'wsl.exe' @('--list','--running','--quiet')
    return @(
        $probe.lines |
            ForEach-Object { ([string]$_).Replace([string][char]0, '').Trim() } |
            Where-Object { $_ }
    )
}

function Assert-TargetRunning {
    $running = @(Get-RunningWslNames)
    if ($running -notcontains $script:TargetDistro) {
        throw "Target distro $($script:TargetDistro) is not already running; refusing to start it."
    }
}

function Invoke-TargetRows([string]$Sql, [string]$Label) {
    Assert-ReadOnlyMetadataSql $Sql $Label
    Assert-TargetRunning
    $probe = Invoke-NativeText 'wsl.exe' @(
        '-d',$script:TargetDistro,'-u','root','--',
        'clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,
        '--query',($Sql + ' FORMAT JSONEachRow')
    )
    return @(Convert-JsonLines $probe.lines $Label)
}

function Get-SourceHealth {
    $ids = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q','clickhouse')
    $containers = @($ids.lines | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($containers.Count -ne 1) {
        throw "Expected exactly one running source ClickHouse container; observed=$($containers.Count)"
    }
    $health = Invoke-NativeText 'docker' @(
        'inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',
        $containers[0]
    )
    $healthText = ((@($health.lines) -join '').Trim().ToLowerInvariant())
    if ($healthText -ne 'healthy') { throw "Source ClickHouse is not healthy: $healthText" }
    $version = Invoke-NativeText 'docker' @(
        'compose','exec','-T','clickhouse','clickhouse-client',
        '--query','SELECT version() FORMAT TabSeparatedRaw'
    )
    $mount = Invoke-NativeText 'docker' @(
        'inspect','--format',
        '{{range .Mounts}}{{if eq .Destination "/var/lib/clickhouse"}}{{.Type}}|{{.Name}}|{{.Source}}{{end}}{{end}}',
        $containers[0]
    )
    $mountText = ((@($mount.lines) -join '').Trim())
    if (-not $mountText) { throw 'Source ClickHouse /var/lib/clickhouse mount identity is missing.' }
    $fields = @($mountText.Split('|'))
    if ($fields.Count -lt 3) { throw "Source ClickHouse mount identity is malformed: $mountText" }
    return [ordered]@{
        status='healthy'
        version=((@($version.lines) -join '').Trim())
        container_id=$containers[0]
        data_mount=[ordered]@{ type=$fields[0]; name=$fields[1]; source=$fields[2] }
    }
}

function Get-ServiceClickHouseBinding([string]$Service) {
    $ids = Invoke-NativeText 'docker' @('compose','ps','--status','running','-q',$Service) -AllowFailure
    if ($ids.exit_code -ne 0) {
        return [ordered]@{ service=$Service; state='INSPECTION_FAILED'; clickhouse_host=$null }
    }
    $containers = @($ids.lines | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($containers.Count -eq 0) {
        return [ordered]@{ service=$Service; state='NOT_RUNNING'; clickhouse_host=$null }
    }
    if ($containers.Count -ne 1) {
        return [ordered]@{ service=$Service; state='AMBIGUOUS'; clickhouse_host=$null }
    }
    $probe = Invoke-NativeText 'docker' @(
        'compose','exec','-T',$Service,'python','-c',
        'from app.config import get_settings; print(get_settings().clickhouse_host)'
    )
    $host = ((@($probe.lines) -join '').Trim())
    return [ordered]@{ service=$Service; state='RUNNING'; clickhouse_host=$host }
}

function Get-TargetVersion {
    Assert-TargetRunning
    $server = Invoke-NativeText 'wsl.exe' @(
        '-d',$script:TargetDistro,'-u','root','--',
        'pgrep','-f','[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml'
    )
    $pids = @($server.lines | Where-Object { $_.Trim() -match '^\d+$' })
    if ($pids.Count -ne 1) {
        throw "Expected one accepted target ClickHouse server; observed=$($pids.Count)"
    }
    $version = Invoke-NativeText 'wsl.exe' @(
        '-d',$script:TargetDistro,'-u','root','--',
        'clickhouse','client','--host',$script:TargetHost,'--port',$script:TargetPort,
        '--query','SELECT version() FORMAT TabSeparatedRaw'
    )
    return ((@($version.lines) -join '').Trim())
}

function Get-DriveFact([string]$DriveLetter) {
    $volume = Get-Volume -DriveLetter $DriveLetter -ErrorAction Stop
    $partition = Get-Partition -DriveLetter $DriveLetter -ErrorAction Stop
    $disk = $partition | Get-Disk -ErrorAction Stop
    return [ordered]@{
        drive=$DriveLetter
        file_system=[string]$volume.FileSystem
        total_bytes=[int64]$volume.Size
        free_bytes=[int64]$volume.SizeRemaining
        disk_number=[int]$disk.Number
        friendly_name=[string]$disk.FriendlyName
        bus_type=[string]$disk.BusType
        media_type=[string]$disk.MediaType
    }
}

function Get-ReserveState([int64]$Total,[int64]$Free) {
    [int64]$recommended=[math]::Ceiling([double]$Total * $script:RecommendedReserveRatio)
    [int64]$hard=[math]::Ceiling([double]$Total * $script:HardReserveRatio)
    $state = if ($Free -lt $hard) {
        'BLOCKED_BELOW_HARD_RESERVE'
    }
    elseif ($Free -lt $recommended) {
        'BELOW_RECOMMENDED_RESERVE'
    }
    else {
        'READY'
    }
    return [ordered]@{
        state=$state
        recommended_30pct_floor_bytes=$recommended
        hard_20pct_floor_bytes=$hard
    }
}

function Get-VhdxInventory {
    if (-not (Test-Path -LiteralPath $script:ProductionHotRoot -PathType Container)) {
        return @()
    }
    return @(
        Get-ChildItem -LiteralPath $script:ProductionHotRoot -Filter '*.vhdx' -File -ErrorAction Stop |
            Sort-Object FullName |
            ForEach-Object {
                [pscustomobject]@{
                    path=$_.FullName
                    base_name=$_.BaseName
                    file_length_bytes=[int64]$_.Length
                    last_write_utc=$_.LastWriteTimeUtc.ToString('o')
                }
            }
    )
}

function Normalize-IdentityToken([string]$Value) {
    if (-not $Value) { return '' }
    return (($Value.ToLowerInvariant()) -replace '[^a-z0-9]', '')
}

function Resolve-HotCnBacking([object[]]$Inventory,[string]$DiskPath) {
    $mountToken=''
    if ($DiskPath -match '^/mnt/wsl/([^/]+)/') { $mountToken=[string]$Matches[1] }
    $normalizedMount=Normalize-IdentityToken $mountToken
    $matches=@(
        $Inventory | Where-Object {
            $name=Normalize-IdentityToken ([string]$_.base_name)
            $name -match 'hotcn' -or
            ($normalizedMount -and ($normalizedMount.Contains($name) -or $name.Contains($normalizedMount)))
        }
    )
    $state = if ($matches.Count -eq 0) { 'ABSENT' }
        elseif ($matches.Count -eq 1) { 'EXACT_ONE' }
        else { 'AMBIGUOUS' }
    return [ordered]@{ state=$state; mount_token=$mountToken; candidates=@($matches) }
}

function Get-Ext4Fact([string]$Path) {
    if (-not $Path) {
        return [ordered]@{ checked=$false; fstype=$null; source=$null; target=$null }
    }
    $probe = Invoke-NativeText 'wsl.exe' @(
        '-d',$script:TargetDistro,'-u','root','--',
        'findmnt','-T',$Path,'-n','-o','FSTYPE,SOURCE,TARGET'
    ) -AllowFailure
    if ($probe.exit_code -ne 0 -or @($probe.lines).Count -eq 0) {
        return [ordered]@{ checked=$true; fstype=$null; source=$null; target=$null }
    }
    $text=((@($probe.lines) -join ' ').Trim() -replace '\s+',' ')
    $parts=@($text.Split(' '))
    return [ordered]@{
        checked=$true
        fstype=if($parts.Count -ge 1){$parts[0]}else{$null}
        source=if($parts.Count -ge 2){$parts[1]}else{$null}
        target=if($parts.Count -ge 3){$parts[2]}else{$null}
    }
}

function Get-CnPlacementClass([string]$Table) {
    if ($script:HotRequiredTables -contains $Table) { return 'HOT_REQUIRED_CURRENT_SERVING' }
    if ($script:WarmCandidateTables -contains $Table) { return 'WARM_AFTER_SUMMARY_EQUIVALENCE' }
    return 'UNCLASSIFIED_RETAIN_AS_IS'
}

function Summarize-CnPlacement([object[]]$Rows) {
    $summary=[ordered]@{
        hot_required=[ordered]@{ rows=0L; bytes=0L; active_parts=0L }
        warm_candidate=[ordered]@{ rows=0L; bytes=0L; active_parts=0L }
        unclassified=[ordered]@{ rows=0L; bytes=0L; active_parts=0L }
    }
    $enriched=@()
    foreach($row in @($Rows)) {
        $class=Get-CnPlacementClass ([string]$row.table)
        $bucket = if($class -eq 'HOT_REQUIRED_CURRENT_SERVING'){'hot_required'}
            elseif($class -eq 'WARM_AFTER_SUMMARY_EQUIVALENCE'){'warm_candidate'}
            else{'unclassified'}
        $summary[$bucket].rows += [int64]$row.rows
        $summary[$bucket].bytes += [int64]$row.bytes
        $summary[$bucket].active_parts += [int64]$row.active_parts
        $enriched += [ordered]@{
            table=[string]$row.table
            placement_contract=$class
            storage_policy=[string]$row.storage_policy
            disk_name=[string]$row.disk_name
            active_parts=[int64]$row.active_parts
            rows=[int64]$row.rows
            bytes=[int64]$row.bytes
        }
    }
    return [ordered]@{ totals=$summary; rows=@($enriched) }
}

function Get-Decision(
    [object]$SourceHealth,
    [object]$ApiBinding,
    [object]$DReserve,
    [object[]]$TargetDisks,
    [object[]]$TargetPolicies,
    [object]$Backing,
    [object]$HotCnExt4,
    [object]$WarmCnExt4,
    [object]$SourcePlacement
) {
    if ([string]$SourceHealth.status -ne 'healthy') {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='RESTORE_CN_SOURCE_SERVING_HEALTH'; reason='CN_SOURCE_UNHEALTHY' }
    }
    if ([string]$ApiBinding.state -ne 'RUNNING' -or [string]$ApiBinding.clickhouse_host -ne 'clickhouse') {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='REVIEW_CN_SERVING_BINDING'; reason='CN_API_BINDING_UNEXPECTED' }
    }
    if ([string]$DReserve.state -ne 'READY') {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='REVIEW_D_PHYSICAL_CAPACITY'; reason=[string]$DReserve.state }
    }
    if ([int64]$SourcePlacement.totals.hot_required.bytes -le 0) {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='REVIEW_CN_PLACEMENT_EVIDENCE'; reason='NO_HOT_REQUIRED_SOURCE_BYTES' }
    }

    $warmDisks=@($TargetDisks | Where-Object { [string]$_.name -eq $script:WarmCnDisk })
    $warmPolicies=@($TargetPolicies | Where-Object { [string]$_.policy_name -eq $script:WarmCnPolicy })
    if ($warmDisks.Count -ne 1 -or $warmPolicies.Count -ne 1) {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='RESTORE_ACCEPTED_WARM_CN_BASELINE'; reason='WARM_CN_BASELINE_MISSING_OR_AMBIGUOUS' }
    }
    $warmPolicyDisks=@($warmPolicies[0].disks)
    if ($warmPolicyDisks.Count -ne 1 -or [string]$warmPolicyDisks[0] -ne $script:WarmCnDisk -or [string]$WarmCnExt4.fstype -ne 'ext4') {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='RESTORE_ACCEPTED_WARM_CN_BASELINE'; reason='WARM_CN_BASELINE_DRIFTED' }
    }

    $hotDisks=@($TargetDisks | Where-Object { [string]$_.name -eq $script:HotCnDisk })
    $hotPolicies=@($TargetPolicies | Where-Object { [string]$_.policy_name -eq $script:HotCnPolicy })
    if ($hotDisks.Count -gt 1 -or $hotPolicies.Count -gt 1 -or [string]$Backing.state -eq 'AMBIGUOUS') {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='REVIEW_CN_HOT_IDENTITY_DRIFT'; reason='AMBIGUOUS_HOT_CN_IDENTITY' }
    }
    if ($hotDisks.Count -eq 0 -and $hotPolicies.Count -eq 0 -and [string]$Backing.state -eq 'ABSENT') {
        return [ordered]@{ decision='CN_HOT_PROVISIONING_PLAN_REQUIRED'; next_gate='FREEZE_CN_HOT_PROVISIONING_AND_MIGRATION_PLAN'; reason='HOT_CN_FOUNDATION_ABSENT' }
    }
    if ($hotDisks.Count -ne 1 -or $hotPolicies.Count -ne 1 -or [string]$Backing.state -ne 'EXACT_ONE') {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='REVIEW_PARTIAL_CN_HOT_STATE'; reason='PARTIAL_OR_UNRESOLVED_HOT_CN_STATE' }
    }
    $policyDisks=@($hotPolicies[0].disks)
    if ($policyDisks.Count -ne 1 -or [string]$policyDisks[0] -ne $script:HotCnDisk -or [string]$HotCnExt4.fstype -ne 'ext4') {
        return [ordered]@{ decision='CN_HOT_PLACEMENT_REVIEW_REQUIRED'; next_gate='REVIEW_CN_HOT_IDENTITY_DRIFT'; reason='HOT_CN_DISK_POLICY_OR_EXT4_DRIFT' }
    }
    return [ordered]@{
        decision='CN_HOT_FOUNDATION_ACCEPTED'
        next_gate='REVIEW_CN_HOT_DATA_PARITY_AND_SERVING_CUTOVER'
        reason='HOT_CN_FOUNDATION_IDENTITY_ACCEPTED'
    }
}

try {
    if ($ContractOnly) {
        Assert-ReadOnlyMetadataSql 'SELECT table FROM system.parts' 'parts contract'
        Assert-ReadOnlyMetadataSql 'SELECT name FROM system.disks' 'disk contract'
        if ((Get-CnPlacementClass 'cn_case_current') -ne 'HOT_REQUIRED_CURRENT_SERVING') {
            throw 'CN Hot table classification drifted.'
        }
        if ((Get-CnPlacementClass 'cn_goods_item_observation') -ne 'WARM_AFTER_SUMMARY_EQUIVALENCE') {
            throw 'CN Warm candidate classification drifted.'
        }
        $source=[pscustomobject]@{ status='healthy' }
        $api=[pscustomobject]@{ state='RUNNING'; clickhouse_host='clickhouse' }
        $reserve=[pscustomobject]@{ state='READY' }
        $placement=[pscustomobject]@{
            totals=[pscustomobject]@{
                hot_required=[pscustomobject]@{ bytes=100L }
            }
        }
        $baseDisks=@([pscustomobject]@{ name='warm_cn'; path='/warm-cn' })
        $basePolicies=@([pscustomobject]@{ policy_name='warm_cn_only'; disks=@('warm_cn') })
        $absent=Get-Decision $source $api $reserve $baseDisks $basePolicies ([pscustomobject]@{ state='ABSENT' }) ([pscustomobject]@{ fstype=$null }) ([pscustomobject]@{ fstype='ext4' }) $placement
        if ($absent.decision -ne 'CN_HOT_PROVISIONING_PLAN_REQUIRED') {
            throw 'Absent CN Hot decision contract drifted.'
        }
        $acceptedDisks=@($baseDisks + [pscustomobject]@{ name='hot_cn'; path='/mnt/wsl/markorbit_prod_hot_cn/clickhouse-data/' })
        $acceptedPolicies=@($basePolicies + [pscustomobject]@{ policy_name='hot_cn_only'; disks=@('hot_cn') })
        $accepted=Get-Decision $source $api $reserve $acceptedDisks $acceptedPolicies ([pscustomobject]@{ state='EXACT_ONE' }) ([pscustomobject]@{ fstype='ext4' }) ([pscustomobject]@{ fstype='ext4' }) $placement
        if ($accepted.decision -ne 'CN_HOT_FOUNDATION_ACCEPTED') {
            throw 'Accepted CN Hot decision contract drifted.'
        }
        Write-Host 'CN_HOT_FOUNDATION_READINESS_CONTRACT_PASS'
        return
    }

    Assert-ExactMain 'entry'
    $sourceHealth=Get-SourceHealth
    $apiBinding=Get-ServiceClickHouseBinding 'api'
    $workerBinding=Get-ServiceClickHouseBinding 'worker'
    $targetVersion=Get-TargetVersion
    $dDrive=Get-DriveFact 'D'
    $dReserve=Get-ReserveState $dDrive.total_bytes $dDrive.free_bytes
    $vhdxInventory=@(Get-VhdxInventory)

    $placementSql=@"
SELECT
    p.table AS table,
    t.storage_policy AS storage_policy,
    p.disk_name AS disk_name,
    count() AS active_parts,
    sum(p.rows) AS rows,
    sum(p.bytes_on_disk) AS bytes
FROM system.parts AS p
INNER JOIN system.tables AS t
    ON p.database=t.database AND p.table=t.name
WHERE p.active
  AND p.database='$($script:Database)'
  AND startsWith(p.table,'cn_')
GROUP BY p.table,t.storage_policy,p.disk_name
ORDER BY p.table,p.disk_name
"@
    $sourcePlacementRows=@(Invoke-SourceRows $placementSql 'source CN placement')
    $sourcePlacement=Summarize-CnPlacement $sourcePlacementRows
    $targetPlacementRows=@(Invoke-TargetRows $placementSql 'target CN placement')
    $targetPlacement=Summarize-CnPlacement $targetPlacementRows

    $targetDisks=@(Invoke-TargetRows @"
SELECT name,path,total_space,free_space
FROM system.disks
WHERE name IN ('hot_cn','hot_us','warm_cn')
ORDER BY name
"@ 'target governed CN/US disks')
    $targetPolicies=@(Invoke-TargetRows @"
SELECT policy_name,volume_name,volume_priority,disks
FROM system.storage_policies
WHERE policy_name IN ('hot_cn_only','hot_us_only','warm_cn_only')
ORDER BY policy_name,volume_priority
"@ 'target governed CN/US policies')

    $hotCnRows=@($targetDisks | Where-Object { [string]$_.name -eq $script:HotCnDisk })
    $hotCnPath=if($hotCnRows.Count -eq 1){[string]$hotCnRows[0].path}else{''}
    $hotCnBacking=Resolve-HotCnBacking $vhdxInventory $hotCnPath
    $hotCnExt4=if($hotCnRows.Count -eq 1){Get-Ext4Fact $hotCnPath}else{[ordered]@{checked=$false;fstype=$null;source=$null;target=$null}}

    $warmCnRows=@($targetDisks | Where-Object { [string]$_.name -eq $script:WarmCnDisk })
    $warmCnPath=if($warmCnRows.Count -eq 1){[string]$warmCnRows[0].path}else{''}
    $warmCnExt4=if($warmCnRows.Count -eq 1){Get-Ext4Fact $warmCnPath}else{[ordered]@{checked=$false;fstype=$null;source=$null;target=$null}}

    $decision=Get-Decision $sourceHealth $apiBinding $dReserve $targetDisks $targetPolicies $hotCnBacking $hotCnExt4 $warmCnExt4 $sourcePlacement
    Assert-ExactMain 'post-query'
    $head=(git rev-parse HEAD).Trim().ToLowerInvariant()

    $receipt=[ordered]@{
        receipt_version=$script:ReceiptVersion
        issue=807
        generated_at=[DateTimeOffset]::UtcNow.ToString('o')
        main_sha=$head
        decision=[string]$decision.decision
        next_gate=[string]$decision.next_gate
        reason=[string]$decision.reason
        read_only=$true
        mutation_performed=$false
        serving=[ordered]@{
            api=$apiBinding
            worker=$workerBinding
            source_clickhouse=$sourceHealth
        }
        host=[ordered]@{
            d_drive=$dDrive
            d_reserve=$dReserve
            production_hot_root=$script:ProductionHotRoot
            d_vhdx_inventory=@($vhdxInventory)
        }
        source_cn_placement=$sourcePlacement
        target=[ordered]@{
            distro=$script:TargetDistro
            clickhouse_version=$targetVersion
            disks=@($targetDisks)
            policies=@($targetPolicies)
            cn_placement=$targetPlacement
            hot_cn_backing=$hotCnBacking
            hot_cn_ext4=$hotCnExt4
            warm_cn_ext4=$warmCnExt4
        }
        placement_contract=[ordered]@{
            source='#262 CN_HOT_WARM_CAPACITY_PROFILE_V1'
            hot_required_tables=@($script:HotRequiredTables)
            warm_candidate_tables=@($script:WarmCandidateTables)
            unclassified_behavior='RETAIN_AS_IS'
            all_cn_bytes_must_move_claimed=$false
        }
        constraints=[ordered]@{
            cn_replay_or_rebuild_authorized=$false
            data_copy_authorized=$false
            vhdx_mutation_authorized=$false
            wsl_lifecycle_change_authorized=$false
            docker_lifecycle_change_authorized=$false
            clickhouse_mutation_authorized=$false
            api_cutover_authorized=$false
        }
    }

    $root=if([IO.Path]::IsPathRooted($EvidenceRoot)){[IO.Path]::GetFullPath($EvidenceRoot)}
        else{[IO.Path]::GetFullPath((Join-Path $repoRoot $EvidenceRoot))}
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
    $receiptPath=Join-Path $root "cn_hot_foundation_readiness_$stamp.json"
    $json=$receipt | ConvertTo-Json -Depth 14
    $utf8=New-Object System.Text.UTF8Encoding($false)
    $tmp="$receiptPath.tmp"
    [IO.File]::WriteAllText($tmp,$json,$utf8)
    Move-Item -LiteralPath $tmp -Destination $receiptPath -Force

    Write-Host "receipt_version=$($script:ReceiptVersion)"
    Write-Host "decision=$($decision.decision)"
    Write-Host "next_gate=$($decision.next_gate)"
    Write-Host "reason=$($decision.reason)"
    Write-Host "source_cn_hot_required_bytes=$($sourcePlacement.totals.hot_required.bytes)"
    Write-Host "target_cn_hot_required_bytes=$($targetPlacement.totals.hot_required.bytes)"
    Write-Host "hot_cn_backing_state=$($hotCnBacking.state)"
    Write-Host "d_reserve_state=$($dReserve.state)"
    Write-Host "receipt_path=$receiptPath"
}
finally {
    Pop-Location
}
