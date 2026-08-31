[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$CurrentHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$RetainedVolume = "markorbit-data-engine_clickhouse_data",
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545",
    [ValidateRange(32, 512)]
    [int]$ReserveGiB = 128,
    [ValidateRange(10, 300)]
    [int]$MergeTreeProbeTimeoutSeconds = 30,
    [switch]$Execute,
    [string]$EvidenceRoot = "reports"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-DockerText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& docker @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $rendered = @($output | ForEach-Object { $_.ToString() })
    if ($exitCode -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode`: $($rendered -join [Environment]::NewLine)"
    }
    return $rendered
}

function ConvertTo-Base64Utf8([string]$Text) {
    $normalized = $Text.Replace("`r`n", "`n").Replace("`r", "`n")
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalized))
}

function New-QuoteSafeShellRunner([string]$Script) {
    $payload = ConvertTo-Base64Utf8 $Script
    return "printf %s $payload | base64 -d | sh"
}

function Invoke-DockerRunScript([string[]]$RunArguments, [string]$Image, [string]$Script) {
    $runner = New-QuoteSafeShellRunner $Script
    return Invoke-DockerText -Arguments (@('run') + $RunArguments + @('--entrypoint','sh',$Image,'-c',$runner))
}

function Invoke-ContainerScript([string]$ContainerId, [string]$Script) {
    $runner = New-QuoteSafeShellRunner $Script
    return Invoke-DockerText -Arguments @('exec',$ContainerId,'sh','-c',$runner)
}

function Get-SingleMountByDestination([object]$Mounts, [string]$Destination) {
    $result = $null
    foreach ($mount in $Mounts) {
        if ([string]$mount.Destination -ne $Destination) { continue }
        if ($null -ne $result) { throw "Multiple $Destination mounts found." }
        $result = $mount
    }
    if ($null -eq $result) { throw "Required mount missing: $Destination" }
    return $result
}

function Normalize-WindowsPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Windows path is required.' }
    $candidate = $Path.Replace('/', '\')
    if ($candidate -notmatch '^[A-Za-z]:\\') { throw "Expected absolute Windows path: $Path" }
    return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
}

function Get-RunningClickHouseId {
    $ids = @(Invoke-DockerText -Arguments @('compose','ps','--status','running','-q','clickhouse') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($ids.Count -ne 1) { throw 'Exactly one running ClickHouse container is required.' }
    return $ids[0]
}

function Get-LogicalBaseline([string]$ContainerId) {
    $script = @'
set -eu
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL"
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT countDistinct(tuple(database, table)), count(), coalesce(sum(rows), 0), coalesce(sum(bytes_on_disk), 0) FROM system.parts WHERE active AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')"
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT database, table, sum(rows) FROM system.parts WHERE active AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') GROUP BY database, table ORDER BY database, table" | sha256sum | awk '{print $1}'
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT database, name, toString(uuid) FROM system.tables WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') ORDER BY database, name" | sha256sum | awk '{print $1}'
clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --format TSVRaw --query "SELECT count() FROM system.tables WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') AND storage_policy != 'default'"
'@
    $lines = @(Invoke-ContainerScript $ContainerId $script | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    if ($lines.Count -lt 5) { throw "Unexpected logical baseline output: $($lines -join ' | ')" }
    $parts = $lines[1] -split "`t"
    if ($parts.Count -ne 4) { throw "Unexpected system.parts baseline: $($lines[1])" }
    if ($lines[2] -notmatch '^[0-9a-f]{64}$' -or $lines[3] -notmatch '^[0-9a-f]{64}$' -or $lines[4] -notmatch '^\d+$') {
        throw 'Unexpected logical digest/storage-policy output.'
    }
    return [ordered]@{
        schema_snapshot = $lines[0]
        active_table_count = [int64]$parts[0]
        active_part_count = [int64]$parts[1]
        active_rows = [int64]$parts[2]
        active_bytes_on_disk = [int64]$parts[3]
        table_rows_sha256 = $lines[2]
        table_uuid_sha256 = $lines[3]
        nondefault_storage_policy_count = [int64]$lines[4]
    }
}

function Get-StructuralManifest([string]$MountSpec, [string]$Image, [string]$Label) {
    $script = @'
set -eu
stats="$(find /root -type f -printf '%s\n' | awk '{bytes += $1; count += 1} END {printf "%.0f\t%.0f", bytes, count}')"
symlinks="$(find /root -type l -printf '.\n' | wc -l | tr -d ' ')"
dirs="$(find /root -mindepth 1 -type d -printf '.\n' | wc -l | tr -d ' ')"
digest="$(find /root -mindepth 1 \( -type d -printf 'D\t%P\n' -o -type f -printf 'F\t%P\t%s\t%n\n' -o -type l -printf 'L\t%P\t%l\n' \) | LC_ALL=C sort | sha256sum | awk '{print $1}')"
printf '%s\t%s\t%s\t%s\n' "$stats" "$symlinks" "$dirs" "$digest"
'@
    $line = @(Invoke-DockerRunScript @('--rm','--user','0:0','--mount',$MountSpec) $Image $script |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })[-1]
    $parts = $line -split "`t"
    if ($parts.Count -ne 5 -or $parts[4] -notmatch '^[0-9a-f]{64}$') { throw "Unexpected $Label manifest: $line" }
    return [ordered]@{
        regular_file_bytes = [int64]$parts[0]
        regular_file_count = [int64]$parts[1]
        symlink_count = [int64]$parts[2]
        directory_count = [int64]$parts[3]
        manifest_sha256 = $parts[4]
    }
}

function Assert-ManifestEqual($Source, $Target) {
    foreach ($field in @('regular_file_bytes','regular_file_count','symlink_count','directory_count')) {
        if ([int64]$Source[$field] -ne [int64]$Target[$field]) {
            throw "Structural copy mismatch $field`: source=$($Source[$field]) target=$($Target[$field])"
        }
    }
    if ([string]$Source.manifest_sha256 -ne [string]$Target.manifest_sha256) {
        throw 'Structural manifest SHA mismatch after Linux-volume copy.'
    }
}

function Assert-LogicalEqual($Before, $After) {
    foreach ($field in @('schema_snapshot','active_table_count','active_rows','table_rows_sha256','table_uuid_sha256','nondefault_storage_policy_count')) {
        if ([string]$Before[$field] -ne [string]$After[$field]) {
            throw "Logical baseline mismatch $field`: before=$($Before[$field]) after=$($After[$field])"
        }
    }
}

function Get-VolumeCapacity([string]$Volume, [string]$Image) {
    $script = @'
set -eu
regular="$(find /root -type f -printf '%s\n' | awk '{bytes += $1; count += 1} END {printf "%.0f\t%.0f", bytes, count}')"
allocated="$(du -s -B1 /root | awk '{print $1}')"
free="$(df -B1 /root | awk 'NR==2 {print $4}')"
printf '%s\t%s\t%s\n' "$regular" "$allocated" "$free"
'@
    $line = @(Invoke-DockerRunScript @('--rm','--user','0:0','--mount',"type=volume,source=$Volume,target=/root,readonly") $Image $script |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })[-1]
    $parts = $line -split "`t"
    if ($parts.Count -ne 4) { throw "Unexpected retained-volume capacity output: $line" }
    return [ordered]@{
        regular_file_bytes = [int64]$parts[0]
        regular_file_count = [int64]$parts[1]
        allocated_bytes = [int64]$parts[2]
        free_bytes = [int64]$parts[3]
    }
}

function Get-ColdUsage([string]$ColdPath, [string]$Image) {
    $script = @'
set -eu
bytes="$(find /cold -type f -printf '%s\n' | awk '{s += $1; c += 1} END {printf "%.0f\t%.0f", s, c}')"
printf '%s\n' "$bytes"
'@
    $line = @(Invoke-DockerRunScript @('--rm','--user','0:0','--mount',"type=bind,source=$ColdPath,target=/cold,readonly") $Image $script |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })[-1]
    $parts = $line -split "`t"
    if ($parts.Count -ne 2) { throw "Unexpected cold usage output: $line" }
    return [ordered]@{ regular_file_bytes = [int64]$parts[0]; regular_file_count = [int64]$parts[1] }
}

function Start-BindClickHouse([string]$Hot, [string]$Cold, [string]$Logs) {
    $env:CLICKHOUSE_HOT_DATA_PATH = $Hot.Replace('\','/')
    $env:CLICKHOUSE_COLD_DATA_PATH = $Cold.Replace('\','/')
    $env:CLICKHOUSE_LOG_PATH = $Logs.Replace('\','/')
    Invoke-DockerText -Arguments @('compose','-f','docker-compose.yml','-f','docker-compose.hot-cold-storage.yml','up','-d','--wait','--no-deps','--force-recreate','clickhouse') | Out-Null
}

function Start-LinuxVolumeClickHouse {
    Invoke-DockerText -Arguments @('compose','-f','docker-compose.yml','up','-d','--wait','--no-deps','--force-recreate','clickhouse') | Out-Null
}

function Restart-Services([string[]]$Services) {
    if ($Services.Count -gt 0) { Invoke-DockerText -Arguments (@('compose','start') + $Services) | Out-Null }
}

function Invoke-MergeTreeAcceptance([string]$ContainerId, [int]$TimeoutSeconds) {
    $db = "markorbit_linux_volume_probe_$((Get-Date).ToString('yyyyMMdd_HHmmssfff'))"
    $queryId = $db
    $sql = "CREATE DATABASE $db; CREATE TABLE $db.t (id UInt64) ENGINE=MergeTree ORDER BY id; INSERT INTO $db.t VALUES (1); SELECT count() FROM $db.t; DROP DATABASE $db SYNC;"
    $payload = ConvertTo-Base64Utf8 $sql
    $script = "set -eu; printf %s $payload | timeout ${TimeoutSeconds}s clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --query_id $queryId --multiquery"
    try {
        $out = @(Invoke-ContainerScript $ContainerId $script | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
        if ($out -notcontains '1') { throw "MergeTree probe did not return row_count=1: $($out -join ' | ')" }
    }
    catch {
        $cleanup = "timeout 15s clickhouse-client --user `"`$CLICKHOUSE_USER`" --password `"`$CLICKHOUSE_PASSWORD`" --multiquery --query `"KILL QUERY WHERE query_id = '$queryId' SYNC; DROP DATABASE IF EXISTS $db SYNC;`""
        try { Invoke-ContainerScript $ContainerId $cleanup | Out-Null } catch {}
        throw
    }
    return [ordered]@{ database = $db; query_id = $queryId; passed = $true }
}

try {
    Write-Host '===== EXACT-MAIN LINUX-VOLUME RECOVERY GATE ====='
    if (git status --porcelain) { throw 'Working tree must be clean.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Recovery must run from main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed.' }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw 'Exact-main mismatch.' }

    Write-Host 'recovery_stage=global_idle_zero_worker'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-idle-worker.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Global idle gate failed.' }
    $workerAll = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($workerAll.Count -ne 0) { throw 'Worker containers must be absent.' }

    $cid = Get-RunningClickHouseId
    $health = ((Invoke-DockerText -Arguments @('inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}',$cid)) -join '').Trim()
    if ($health -ne 'healthy') { throw "ClickHouse must be healthy; observed=$health" }
    $image = ((Invoke-DockerText -Arguments @('inspect','--format','{{.Config.Image}}',$cid)) -join '').Trim()
    if ($image -notmatch ':24\.8(?:$|[.-])') { throw "Unexpected ClickHouse image: $image" }
    $mounts = (((Invoke-DockerText -Arguments @('inspect','--format','{{json .Mounts}}',$cid)) -join '').Trim() | ConvertFrom-Json)
    $hotMount = Get-SingleMountByDestination $mounts '/var/lib/clickhouse'
    $coldMount = Get-SingleMountByDestination $mounts '/var/lib/clickhouse-cold'
    $logMount = Get-SingleMountByDestination $mounts '/var/log/clickhouse-server'

    if ([string]$hotMount.Type -eq 'volume' -and [string]$hotMount.Name -eq $RetainedVolume) {
        Write-Host 'recovery_mode=ALREADY_ON_LINUX_VOLUME'
        $baseline = Get-LogicalBaseline $cid
        if ($baseline.schema_snapshot -ne $ExpectedSchemaSnapshot) { throw "schema snapshot drifted: $($baseline.schema_snapshot)" }
        if ([int64]$baseline.nondefault_storage_policy_count -ne 0) { throw 'Non-default ClickHouse storage policies remain active.' }
        $tmpCount = [int64](@(Invoke-ContainerScript $cid "find /var/lib/clickhouse/store/771/7716c662-1886-4e4b-a7e2-631c80ac8dd2 -maxdepth 1 -type d -name 'tmp_insert_*' -printf '.\\n' | wc -l")[-1].Trim())
        if ($tmpCount -ne 0) { throw "schema_version tmp_insert dirs remain after Linux-volume activation: $tmpCount" }
        $probe = Invoke-MergeTreeAcceptance $cid $MergeTreeProbeTimeoutSeconds
        Write-Host 'linux_volume_mount_verified=True'
        Write-Host 'schema_version_tmp_insert_dirs=0'
        Write-Host 'native_mergetree_commit_verified=True'
        Write-Host 'source_bind_mutation_performed=False'
        Write-Host 'corpus_replay_performed=False'
        Write-Host 'CLICKHOUSE_LINUX_VOLUME_RECOVERY_PASS'
        return
    }

    if ([string]$hotMount.Type -ne 'bind' -or -not [bool]$hotMount.RW) { throw 'Current ClickHouse Hot root must be an rw bind for this recovery.' }
    $source = Normalize-WindowsPath ([string]$hotMount.Source)
    $expectedSource = Normalize-WindowsPath $CurrentHotPath
    if (-not [string]::Equals($source,$expectedSource,[System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Current Hot source drifted: actual=$source expected=$expectedSource"
    }
    if ([string]$coldMount.Type -ne 'bind' -or [string]$logMount.Type -ne 'bind') { throw 'Expected current cold/log bind mounts are missing.' }
    $coldSource = Normalize-WindowsPath ([string]$coldMount.Source)
    $logSource = Normalize-WindowsPath ([string]$logMount.Source)

    $volumeInfo = @(Invoke-DockerText -Arguments @('volume','inspect',$RetainedVolume))
    if ($volumeInfo.Count -eq 0) { throw "Retained Linux volume missing: $RetainedVolume" }
    $volumeRefs = @(Invoke-DockerText -Arguments @('ps','-a','--filter',"volume=$RetainedVolume",'-q') | Where-Object { $_.Trim() -ne '' })
    if ($volumeRefs.Count -ne 0) { throw "Retained volume is still referenced by container(s): $($volumeRefs -join ',')" }

    Write-Host 'recovery_stage=logical_and_capacity_preflight'
    $before = Get-LogicalBaseline $cid
    if ($before.schema_snapshot -ne $ExpectedSchemaSnapshot) { throw "schema snapshot drifted: $($before.schema_snapshot)" }
    if ([int64]$before.nondefault_storage_policy_count -ne 0) { throw 'Non-default storage policy detected; cold removal is not safe.' }
    $coldUsage = Get-ColdUsage $coldSource $image
    if ($coldUsage.regular_file_count -ne 0 -or $coldUsage.regular_file_bytes -ne 0) { throw 'Cold disk contains regular files; base-volume recovery would orphan cold parts.' }

    $sourceObserved = Get-StructuralManifest "type=bind,source=$source,target=/root,readonly" $image 'running-source'
    $volumeCapacity = Get-VolumeCapacity $RetainedVolume $image
    $reserveBytes = [int64]$ReserveGiB * 1GB
    $projectedFreeAfterWipe = [int64]$volumeCapacity.free_bytes + [int64]$volumeCapacity.allocated_bytes
    $requiredAfterWipe = [int64]$sourceObserved.regular_file_bytes + $reserveBytes
    $headroomOk = $projectedFreeAfterWipe -ge $requiredAfterWipe
    Write-Host "source_regular_file_bytes_observed=$($sourceObserved.regular_file_bytes)"
    Write-Host "retained_volume_regular_file_bytes=$($volumeCapacity.regular_file_bytes)"
    Write-Host "retained_volume_allocated_bytes=$($volumeCapacity.allocated_bytes)"
    Write-Host "docker_volume_free_bytes_before=$($volumeCapacity.free_bytes)"
    Write-Host "docker_volume_projected_free_after_wipe=$projectedFreeAfterWipe"
    Write-Host "recovery_required_bytes=$requiredAfterWipe"
    Write-Host "recovery_headroom_ok=$headroomOk"
    if (-not $headroomOk) { throw 'Docker Linux-volume headroom is insufficient; no service was stopped.' }

    $runningServices = @(Invoke-DockerText -Arguments @('compose','ps','--services','--status','running') |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    $writerCandidates = @('api','worker','mark-image-worker','qcc-acquisition')
    $writersToStop = @($writerCandidates | Where-Object { $runningServices -contains $_ })

    if (-not $Execute) {
        Write-Host 'execute=False'
        Write-Host 'source_bind_mutation_performed=False'
        Write-Host 'retained_volume_mutation_performed=False'
        Write-Host 'clickhouse_stop_performed=False'
        Write-Host 'schema_apply_performed=False'
        Write-Host 'corpus_replay_performed=False'
        Write-Host 'CLICKHOUSE_LINUX_VOLUME_RECOVERY_READY'
        return
    }

    $writersStopped = @()
    $clickhouseStopped = $false
    $linuxActivated = $false
    try {
        Write-Host 'recovery_stage=quiesce_writers'
        if ($writersToStop.Count -gt 0) {
            Invoke-DockerText -Arguments (@('compose','stop') + $writersToStop) | Out-Null
            $writersStopped = @($writersToStop)
        }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-idle-worker.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Global idle gate drifted after writer stop.' }
        & git fetch origin main | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Unable to refresh origin/main before storage mutation.' }
        $head2 = (git rev-parse HEAD).Trim().ToLowerInvariant()
        $origin2 = (git rev-parse origin/main).Trim().ToLowerInvariant()
        if ($head2 -ne $expected -or $origin2 -ne $expected -or (git status --porcelain)) { throw 'Exact-main drift before storage mutation.' }

        Write-Host 'recovery_stage=stop_clickhouse_and_freeze_source'
        Invoke-DockerText -Arguments @('compose','-f','docker-compose.yml','stop','clickhouse') | Out-Null
        $clickhouseStopped = $true
        $stillRunning = @(Invoke-DockerText -Arguments @('ps','--filter',"id=$cid",'--filter','status=running','-q') | Where-Object { $_.Trim() -ne '' })
        if ($stillRunning.Count -ne 0) { throw 'ClickHouse did not stop cleanly.' }
        $sourceFrozen = Get-StructuralManifest "type=bind,source=$source,target=/root,readonly" $image 'frozen-source'
        Write-Host "frozen_source_regular_file_bytes=$($sourceFrozen.regular_file_bytes)"
        Write-Host "frozen_source_manifest_sha256=$($sourceFrozen.manifest_sha256)"

        $volumeCapacityFrozen = Get-VolumeCapacity $RetainedVolume $image
        $projectedFrozen = [int64]$volumeCapacityFrozen.free_bytes + [int64]$volumeCapacityFrozen.allocated_bytes
        $requiredFrozen = [int64]$sourceFrozen.regular_file_bytes + $reserveBytes
        if ($projectedFrozen -lt $requiredFrozen) { throw 'Frozen source no longer fits retained Linux volume plus reserve.' }

        Write-Host 'recovery_stage=repopulate_retained_linux_volume'
        $copyScript = @'
set -eu
test -d /source/metadata
test -d /source/store
find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a /source/. /target/
sync
'@
        Invoke-DockerRunScript @('--rm','--user','0:0','--mount',"type=bind,source=$source,target=/source,readonly",'--mount',"type=volume,source=$RetainedVolume,target=/target") $image $copyScript | Out-Null
        $targetFrozen = Get-StructuralManifest "type=volume,source=$RetainedVolume,target=/root,readonly" $image 'linux-volume-target'
        Assert-ManifestEqual $sourceFrozen $targetFrozen
        Write-Host "linux_volume_manifest_sha256=$($targetFrozen.manifest_sha256)"
        Write-Host 'STRUCTURAL_COPY_PARITY_OK'

        Write-Host 'recovery_stage=activate_linux_volume'
        Start-LinuxVolumeClickHouse
        $linuxActivated = $true
        $newCid = Get-RunningClickHouseId
        $newMounts = (((Invoke-DockerText -Arguments @('inspect','--format','{{json .Mounts}}',$newCid)) -join '').Trim() | ConvertFrom-Json)
        $newHot = Get-SingleMountByDestination $newMounts '/var/lib/clickhouse'
        if ([string]$newHot.Type -ne 'volume' -or [string]$newHot.Name -ne $RetainedVolume -or -not [bool]$newHot.RW) {
            throw "Linux-volume activation mismatch: type=$($newHot.Type) name=$($newHot.Name) rw=$($newHot.RW)"
        }
        $after = Get-LogicalBaseline $newCid
        Assert-LogicalEqual $before $after
        $tmpRaw = @(Invoke-ContainerScript $newCid "find /var/lib/clickhouse/store/771/7716c662-1886-4e4b-a7e2-631c80ac8dd2 -maxdepth 1 -type d -name 'tmp_insert_*' -printf '.\\n' | wc -l")
        $tmpCount = [int64]$tmpRaw[-1].Trim()
        if ($tmpCount -ne 0) { throw "schema_version tmp_insert dirs remain after Linux-volume startup: $tmpCount" }

        Write-Host 'recovery_stage=native_mergetree_acceptance'
        $probe = Invoke-MergeTreeAcceptance $newCid $MergeTreeProbeTimeoutSeconds
        $afterProbe = Get-LogicalBaseline $newCid
        Assert-LogicalEqual $before $afterProbe

        Restart-Services $writersStopped
        Write-Host '===== CLICKHOUSE LINUX-VOLUME RECOVERY RESULT ====='
        Write-Host "active_clickhouse_data_mount_type=$($newHot.Type)"
        Write-Host "active_clickhouse_data_volume=$($newHot.Name)"
        Write-Host "schema_version_snapshot=$($after.schema_snapshot)"
        Write-Host "active_table_count=$($after.active_table_count)"
        Write-Host "active_rows=$($after.active_rows)"
        Write-Host "table_rows_sha256=$($after.table_rows_sha256)"
        Write-Host "table_uuid_sha256=$($after.table_uuid_sha256)"
        Write-Host 'schema_version_tmp_insert_dirs=0'
        Write-Host 'native_mergetree_commit_verified=True'
        Write-Host "native_mergetree_probe_database=$($probe.database)"
        Write-Host 'source_bind_mutation_performed=False'
        Write-Host 'rollback_source_retained=True'
        Write-Host 'retained_volume_repopulated=True'
        Write-Host 'windows_hot_bind_active=False'
        Write-Host 'schema_apply_performed=False'
        Write-Host 'corpus_replay_performed=False'
        Write-Host 'CLICKHOUSE_LINUX_VOLUME_RECOVERY_PASS'
    }
    catch {
        Write-Host 'CLICKHOUSE_LINUX_VOLUME_RECOVERY_FAILURE'
        Write-Host "exception_type=$($_.Exception.GetType().FullName)"
        Write-Host "exception_message=$($_.Exception.Message)"
        if ($clickhouseStopped) {
            try {
                if ($linuxActivated) { Invoke-DockerText -Arguments @('compose','-f','docker-compose.yml','stop','clickhouse') | Out-Null }
                Start-BindClickHouse $source $coldSource $logSource
                Restart-Services $writersStopped
                Write-Host 'ROLLBACK_TO_UNTOUCHED_WINDOWS_BIND_PASS'
            }
            catch {
                Write-Host "ROLLBACK_FAILURE=$($_.Exception.Message)"
            }
        }
        elseif ($writersStopped.Count -gt 0) {
            try { Restart-Services $writersStopped } catch {}
        }
        throw
    }
}
finally {
    Pop-Location
}