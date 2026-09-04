param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMain,

    [string]$PythonExe = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$TargetDistro = 'MarkOrbit-ClickHouse'
$KeeperPid = 27700
$TargetHost = '127.0.0.1'
$TargetPort = 29000
$TargetVersion = '24.8.14.39'
$TargetConfigPath = '/opt/markorbit-clickhouse-production/config.xml'
$TargetUsersPath = '/opt/markorbit-clickhouse-production/users.xml'
$ExpectedTargetConfigSha = 'c7240b6c05a96dff2dc4c9e5a801cd524065bd101b5d006f2e8610b63ca56a59'
$ExpectedTargetUsersSha = '16b281607c47f9ee1f1bd8e3d09c4fc556320e833f17d05b597dec78aa2eb233'
$TargetHotDisk = 'hot_us'
$TargetHotPolicy = 'hot_us_only'
$TargetHotMount = '/mnt/wsl/markorbit_prod_hot_us'
$TargetHotPath = '/mnt/wsl/markorbit_prod_hot_us/clickhouse-data/'
$TargetHotUuid = '521a7b20-4380-4d6a-8018-2bab78fc2c4b'
[long]$TargetHotBytes = 274877906944
$TargetHotVhdx = 'D:\MarkOrbitData\production\clickhouse\hot_us.vhdx'
$WarmDisk = 'warm_cn'
$WarmPolicy = 'warm_cn_only'
$WarmMount = '/mnt/wsl/markorbit_prod_warm_cn'
$WarmPath = '/mnt/wsl/markorbit_prod_warm_cn/clickhouse-data/'
$WarmUuid = '2ee74d16-f0bd-461b-ab6a-279603e6c570'
[long]$WarmBytes = 842887331840
$SourceContainerId = '619df97d2be192c1236ab2269f71daad09a828aa5c1169ea4a9ab07670f0d8f5'
$SourceContainerName = '/markorbit-data-engine-clickhouse-1'
$SourceVolume = 'markorbit-data-engine_clickhouse_data'
$SourceVolumeDestination = '/var/lib/clickhouse'
$ExpectedSourceConfigSha = 'baa0b2ff85869e066fa1f27087339c6d0648c87e64cae6ce49915bf345ab9b1f'
$ReadyDecision = 'BOUNDED_US_APPLICATION_CANARY_REVIEW_READY_FOR_OPERATOR_GO'

function Require-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-NativeCapture {
    param([scriptblock]$Command, [string]$Label)
    $lines = @(& $Command 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $joined = ($lines | ForEach-Object { [string]$_ }) -join "`n"
        throw "$Label failed with exit $code`n$joined"
    }
    return @($lines | ForEach-Object { [string]$_ })
}

function Get-ExactSingleLine {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Lines,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $materialized = @(
        $Lines |
            ForEach-Object { [string]$_ }
    )
    Require-True ($materialized.Count -eq 1) "$Label returned unexpected line count: $($materialized.Count)"
    return $materialized[0].Trim()
}

function Normalize-WslNames {
    param([string[]]$Lines)
    return @(
        $Lines |
            ForEach-Object { ([string]$_).Replace("$([char]0)", '').Trim() } |
            Where-Object { $_ }
    )
}

function Invoke-TargetQuery {
    param([string]$Query)
    return Invoke-NativeCapture -Label 'target ClickHouse read-only query' -Command {
        & wsl.exe -d $TargetDistro -u root -- clickhouse-client --host $TargetHost --port $TargetPort --query $Query
    }
}

function Get-TargetFileSha {
    param([string]$Path, [string]$Label)
    $line = Get-ExactSingleLine -Label $Label -Lines @(Invoke-NativeCapture -Label $Label -Command {
        & wsl.exe -d $TargetDistro -u root -- sha256sum $Path
    })
    Require-True ($line -match '^([0-9a-fA-F]{64})\s+') "$Label returned an unparseable SHA-256 line."
    return $Matches[1].ToLowerInvariant()
}

function Get-MountFact {
    param([string]$MountPoint)
    $shell = @'
set -eu
mountpoint="$1"
dev="$(findmnt -n -o SOURCE --target "$mountpoint")"
fstype="$(findmnt -n -o FSTYPE --target "$mountpoint")"
uuid="$(blkid -s UUID -o value "$dev")"
size="$(blockdev --getsize64 "$dev")"
printf '%s\t%s\t%s\t%s\n' "$dev" "$fstype" "$uuid" "$size"
'@
    $lines = @(Invoke-NativeCapture -Label "mount fact $MountPoint" -Command {
        & wsl.exe -d $TargetDistro -u root -- sh -lc $shell sh $MountPoint
    })
    Require-True ($lines.Count -eq 1) "mount fact returned unexpected line count for $MountPoint"
    $parts = ([string]$lines[0]).Split("`t")
    Require-True ($parts.Count -eq 4) "mount fact returned unexpected shape for $MountPoint"
    return [ordered]@{
        mountpoint = $MountPoint
        device = $parts[0]
        fstype = $parts[1]
        uuid = $parts[2]
        size_bytes = [long]$parts[3]
    }
}

function Get-DDriveFact {
    $drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'" -ErrorAction Stop
    Require-True ($null -ne $drive) 'D: logical disk is unavailable.'
    [long]$total = $drive.Size
    [long]$free = $drive.FreeSpace
    [long]$floor = [math]::Ceiling([double]$total * 0.30)
    Require-True ($total -gt 0) 'D: total size is invalid.'
    return [ordered]@{
        total_bytes = $total
        free_bytes = $free
        free_percent = [math]::Round(($free / [double]$total) * 100.0, 6)
        floor_30pct_bytes = $floor
        floor_satisfied = ($free -ge $floor)
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
$EvidenceDir = Join-Path $RepoRoot "reports\production_us_application_canary_stage1_$stamp"
$failurePath = Join-Path $EvidenceDir 'stage1_failure.json'

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    Require-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'Run this Stage 1 operator from an elevated Administrator PowerShell.'

    $head = Get-ExactSingleLine -Label 'git HEAD' -Lines @(Invoke-NativeCapture -Label 'git HEAD' -Command { & git rev-parse HEAD })
    $head = $head.ToLowerInvariant()
    $originMain = Get-ExactSingleLine -Label 'git origin/main' -Lines @(Invoke-NativeCapture -Label 'git origin/main' -Command { & git rev-parse origin/main })
    $originMain = $originMain.ToLowerInvariant()
    $expected = $ExpectedMain.ToLowerInvariant()
    Require-True ($head -eq $expected) "HEAD mismatch: expected=$expected actual=$head"
    Require-True ($originMain -eq $expected) "origin/main mismatch: expected=$expected actual=$originMain"
    $dirty = @(Invoke-NativeCapture -Label 'git status' -Command { & git status --porcelain=v1 --untracked-files=normal })
    Require-True ($dirty.Count -eq 0) 'Working tree is not exactly clean before Stage 1 evidence creation.'

    $pythonCommand = Get-Command $PythonExe -ErrorAction Stop
    Require-True ([bool]$pythonCommand) "Python executable not found: $PythonExe"

    $runningRaw = @(Invoke-NativeCapture -Label 'WSL running-list inspection' -Command { & wsl.exe --list --running --quiet })
    $running = Normalize-WslNames $runningRaw
    Require-True ($running -contains $TargetDistro) 'Target distro is not already running; refusing any command that could start it.'

    $keeper = Get-CimInstance Win32_Process -Filter "ProcessId=$KeeperPid" -ErrorAction SilentlyContinue
    Require-True ($null -ne $keeper) "Accepted target keeper PID $KeeperPid is missing."
    Require-True ([string]$keeper.Name -eq 'wsl.exe') "Accepted target keeper PID $KeeperPid is no longer wsl.exe."
    Require-True ([string]$keeper.CommandLine -match [regex]::Escape($TargetDistro)) 'Accepted target keeper distro binding drifted.'
    Require-True ([string]$keeper.CommandLine -match 'tail\s+-f\s+/dev/null') 'Accepted target keeper command drifted.'

    $serverPids = @(Invoke-NativeCapture -Label 'target server process inspection' -Command {
        & wsl.exe -d $TargetDistro -u root -- sh -lc "pgrep -f '[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml' || true"
    })
    $serverPids = @($serverPids | Where-Object { ([string]$_).Trim() -match '^\d+$' })
    Require-True ($serverPids.Count -eq 1) "Expected exactly one accepted target clickhouse server process; observed=$($serverPids.Count)"

    $targetVersionObserved = Get-ExactSingleLine -Label 'target ClickHouse version' -Lines @(Invoke-TargetQuery 'SELECT version() FORMAT TabSeparatedRaw')
    Require-True ($targetVersionObserved -eq $TargetVersion) "Target ClickHouse version mismatch: $targetVersionObserved"

    $targetConfigSha = Get-TargetFileSha -Path $TargetConfigPath -Label 'target config SHA-256'
    $targetUsersSha = Get-TargetFileSha -Path $TargetUsersPath -Label 'target users SHA-256'
    Require-True ($targetConfigSha -eq $ExpectedTargetConfigSha) "Target config SHA-256 drifted: $targetConfigSha"
    Require-True ($targetUsersSha -eq $ExpectedTargetUsersSha) "Target users SHA-256 drifted: $targetUsersSha"

    Require-True (Test-Path -LiteralPath $TargetHotVhdx -PathType Leaf) "Accepted production hot_us VHDX is missing: $TargetHotVhdx"
    $hotVhdxItem = Get-Item -LiteralPath $TargetHotVhdx

    $hotMountFact = Get-MountFact $TargetHotMount
    Require-True ($hotMountFact.fstype -eq 'ext4') "hot_us filesystem is not ext4: $($hotMountFact.fstype)"
    Require-True ($hotMountFact.uuid -eq $TargetHotUuid) "hot_us UUID mismatch: $($hotMountFact.uuid)"
    Require-True ($hotMountFact.size_bytes -eq $TargetHotBytes) "hot_us block size mismatch: $($hotMountFact.size_bytes)"

    $warmMountFact = Get-MountFact $WarmMount
    Require-True ($warmMountFact.fstype -eq 'ext4') "Warm filesystem is not ext4: $($warmMountFact.fstype)"
    Require-True ($warmMountFact.uuid -eq $WarmUuid) "Warm UUID mismatch: $($warmMountFact.uuid)"
    Require-True ($warmMountFact.size_bytes -eq $WarmBytes) "Warm block size mismatch: $($warmMountFact.size_bytes)"

    $diskLines = @(Invoke-TargetQuery "SELECT name,path,total_space,free_space,keep_free_space FROM system.disks WHERE name IN ('$TargetHotDisk','$WarmDisk') ORDER BY name FORMAT JSONEachRow")
    $disks = @($diskLines | Where-Object { ([string]$_).Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    Require-True ($disks.Count -eq 2) "Expected exact hot_us + warm_cn system.disks rows; observed=$($disks.Count)"
    $hotDiskRow = @($disks | Where-Object { $_.name -eq $TargetHotDisk })
    $warmDiskRow = @($disks | Where-Object { $_.name -eq $WarmDisk })
    Require-True ($hotDiskRow.Count -eq 1) 'hot_us disk row is not exact-one.'
    Require-True ($warmDiskRow.Count -eq 1) 'warm_cn disk row is not exact-one.'
    Require-True ([string]$hotDiskRow[0].path -eq $TargetHotPath) "hot_us ClickHouse path mismatch: $($hotDiskRow[0].path)"
    Require-True ([string]$warmDiskRow[0].path -eq $WarmPath) "warm_cn ClickHouse path mismatch: $($warmDiskRow[0].path)"

    $policyLines = @(Invoke-TargetQuery "SELECT policy_name,volume_name,disks FROM system.storage_policies WHERE policy_name IN ('$TargetHotPolicy','$WarmPolicy') ORDER BY policy_name,volume_priority FORMAT JSONEachRow")
    $policies = @($policyLines | Where-Object { ([string]$_).Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    $hotPolicyRows = @($policies | Where-Object { $_.policy_name -eq $TargetHotPolicy })
    $warmPolicyRows = @($policies | Where-Object { $_.policy_name -eq $WarmPolicy })
    Require-True ($hotPolicyRows.Count -eq 1) 'hot_us_only policy must have exactly one volume row.'
    Require-True ($warmPolicyRows.Count -eq 1) 'warm_cn_only policy must have exactly one volume row.'
    Require-True (@($hotPolicyRows[0].disks).Count -eq 1 -and [string]$hotPolicyRows[0].disks[0] -eq $TargetHotDisk) 'hot_us_only policy no longer maps only to hot_us.'
    Require-True (@($warmPolicyRows[0].disks).Count -eq 1 -and [string]$warmPolicyRows[0].disks[0] -eq $WarmDisk) 'warm_cn_only policy no longer maps only to warm_cn.'

    $hotPartCountText = Get-ExactSingleLine -Label 'hot_us active part count' -Lines @(Invoke-TargetQuery "SELECT count() FROM system.parts WHERE active AND disk_name='$TargetHotDisk' FORMAT TabSeparatedRaw")
    $hotPartCount = [long]$hotPartCountText
    Require-True ($hotPartCount -eq 0) "hot_us already contains active parts: $hotPartCount"
    $warmPartCountText = Get-ExactSingleLine -Label 'warm_cn active part count' -Lines @(Invoke-TargetQuery "SELECT count() FROM system.parts WHERE active AND disk_name='$WarmDisk' FORMAT TabSeparatedRaw")
    $warmPartCount = [long]$warmPartCountText
    Require-True ($warmPartCount -eq 0) "warm_cn unexpectedly contains active parts: $warmPartCount"

    $dBefore = Get-DDriveFact
    Require-True ([bool]$dBefore.floor_satisfied) "D: free space is below the accepted 30% floor: $($dBefore.free_bytes)"

    $sourceInspectRaw = (@(Invoke-NativeCapture -Label 'source container inspect' -Command { & docker inspect $SourceContainerId })) -join "`n"
    $sourceInspectArray = @($sourceInspectRaw | ConvertFrom-Json)
    Require-True ($sourceInspectArray.Count -eq 1) 'Source Docker inspect did not return exactly one container.'
    $sourceInspect = $sourceInspectArray[0]
    Require-True ([string]$sourceInspect.Id -eq $SourceContainerId) 'Source container ID drifted.'
    Require-True ([string]$sourceInspect.Name -eq $SourceContainerName) "Source container name drifted: $($sourceInspect.Name)"
    Require-True ([string]$sourceInspect.State.Status -eq 'running') 'Source container is not running.'
    Require-True ([string]$sourceInspect.State.Health.Status -eq 'healthy') 'Source container is not healthy.'
    $sourceMounts = @(
        $sourceInspect.Mounts |
            Where-Object { $_.Type -eq 'volume' -and $_.Name -eq $SourceVolume -and $_.Destination -eq $SourceVolumeDestination }
    )
    Require-True ($sourceMounts.Count -eq 1) 'Accepted source volume mount is not exact-one.'
    $sourceConsumers = @(Invoke-NativeCapture -Label 'source volume consumer inspection' -Command { & docker ps -a --filter "volume=$SourceVolume" --format '{{.Names}}' })
    $sourceConsumers = @($sourceConsumers | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    Require-True ($sourceConsumers.Count -eq 1 -and $sourceConsumers[0] -eq $SourceContainerName.TrimStart('/')) 'Accepted source volume has an unexpected consumer set.'
    $sourceVersion = Get-ExactSingleLine -Label 'source ClickHouse version' -Lines @(Invoke-NativeCapture -Label 'source ClickHouse version query' -Command {
        & docker exec $SourceContainerId clickhouse-client --query 'SELECT version() FORMAT TabSeparatedRaw'
    })
    Require-True ($sourceVersion -eq $TargetVersion) "Source ClickHouse version mismatch: $sourceVersion"
    $sourceConfigShaLine = Get-ExactSingleLine -Label 'source config SHA-256' -Lines @(Invoke-NativeCapture -Label 'source config SHA-256' -Command {
        & docker exec $SourceContainerId sha256sum /etc/clickhouse-server/config.xml
    })
    Require-True ($sourceConfigShaLine -match '^([0-9a-fA-F]{64})\s+') 'Source config SHA-256 line is unparseable.'
    $sourceConfigSha = $Matches[1].ToLowerInvariant()
    Require-True ($sourceConfigSha -eq $ExpectedSourceConfigSha) "Source config SHA-256 drifted: $sourceConfigSha"

    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    $planPath = Join-Path $EvidenceDir 'us_replay_plan_dry_run.json'
    $sourceSchemaPath = Join-Path $EvidenceDir 'source_application_schema.jsonl'
    $reviewPath = Join-Path $EvidenceDir 'stage1_source_schema_review.json'
    $summaryPath = Join-Path $EvidenceDir 'stage1_source_schema_summary.json'

    $tableNames = @(Invoke-NativeCapture -Label 'Application table contract load' -Command {
        & $PythonExe -c "from app.us.target_canary import APPLICATION_CANARY_TABLES; print(chr(10).join(t.split('.',1)[1] for t in APPLICATION_CANARY_TABLES))"
    })
    $tableNames = @($tableNames | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    Require-True ($tableNames.Count -gt 0) 'Application table contract is empty.'
    $quotedNames = ($tableNames | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ','

    $sourceSchemaQuery = "SELECT name,create_table_query FROM system.tables WHERE database='markorbit_facts' AND name IN ($quotedNames) ORDER BY name FORMAT JSONEachRow"
    $sourceSchemaLines = @(Invoke-NativeCapture -Label 'source Application SHOW CREATE freeze query' -Command {
        & docker exec $SourceContainerId clickhouse-client --query $sourceSchemaQuery
    })
    $sourceSchemaLines | Set-Content -LiteralPath $sourceSchemaPath -Encoding UTF8

    $planLines = @(Invoke-NativeCapture -Label 'deterministic US replay DRY_RUN' -Command {
        & $PythonExe -m app.us.replay_executor --expected-history-parts 91 --max-packages 1
    })
    ($planLines -join "`n") | Set-Content -LiteralPath $planPath -Encoding UTF8

    $helperLines = @(Invoke-NativeCapture -Label 'Stage 1 source/schema freeze helper' -Command {
        & $PythonExe -m app.us.target_canary_review --plan-json $planPath --source-schema-jsonl $sourceSchemaPath --output-json $reviewPath --summary-json $summaryPath
    })
    Require-True (Test-Path -LiteralPath $reviewPath -PathType Leaf) 'Stage 1 full review evidence was not written.'
    Require-True (Test-Path -LiteralPath $summaryPath -PathType Leaf) 'Stage 1 flat summary evidence was not written.'
    $summary = (Get-Content -LiteralPath $summaryPath -Raw -Encoding UTF8) | ConvertFrom-Json
    Require-True ([string]$summary.decision -eq 'US_APPLICATION_CANARY_SOURCE_AND_SCHEMA_FROZEN') "Unexpected source/schema freeze decision: $($summary.decision)"
    Require-True ([bool]$summary.read_only_review) 'Source/schema helper did not report read_only_review=true.'
    Require-True ([int]$summary.package_sequence -eq 2) "Frozen package sequence is not 2: $($summary.package_sequence)"
    Require-True ([string]$summary.target_storage_policy -eq $TargetHotPolicy) 'Frozen target storage policy is not hot_us_only.'
    Require-True ([int]$summary.required_table_count -eq $tableNames.Count) 'Frozen required-table count differs from runtime contract.'
    Require-True ([bool]$summary.first_canary_requires_all_required_tables_absent) 'First-canary table-absence contract is not active.'

    $targetRequiredTableCountText = Get-ExactSingleLine -Label 'target required Application table count' -Lines @(Invoke-TargetQuery "SELECT count() FROM system.tables WHERE database='markorbit_facts' AND name IN ($quotedNames) FORMAT TabSeparatedRaw")
    $targetRequiredTableCount = [long]$targetRequiredTableCountText
    Require-True ($targetRequiredTableCount -eq 0) "First target canary requires all Application final tables absent; observed=$targetRequiredTableCount"

    $packagePath = [string]$summary.package_path
    Require-True (Test-Path -LiteralPath $packagePath -PathType Leaf) "Frozen package disappeared: $packagePath"
    $packageFile = Get-Item -LiteralPath $packagePath
    Require-True ([long]$packageFile.Length -eq [long]$summary.package_size_bytes) 'Frozen package size changed after helper review.'
    $packageShaAfter = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Require-True ($packageShaAfter -eq [string]$summary.package_sha256) 'Frozen package SHA-256 changed after helper review.'

    $sourceVersionAfter = Get-ExactSingleLine -Label 'source ClickHouse post-review version' -Lines @(Invoke-NativeCapture -Label 'source ClickHouse post-review version query' -Command {
        & docker exec $SourceContainerId clickhouse-client --query 'SELECT version() FORMAT TabSeparatedRaw'
    })
    Require-True ($sourceVersionAfter -eq $TargetVersion) 'Source ClickHouse version changed during read-only review.'
    $sourceConfigShaAfterLine = Get-ExactSingleLine -Label 'source config post-review SHA-256' -Lines @(Invoke-NativeCapture -Label 'source config post-review SHA-256' -Command {
        & docker exec $SourceContainerId sha256sum /etc/clickhouse-server/config.xml
    })
    Require-True ($sourceConfigShaAfterLine -match '^([0-9a-fA-F]{64})\s+') 'Source post-review config SHA-256 line is unparseable.'
    $sourceConfigShaAfter = $Matches[1].ToLowerInvariant()
    Require-True ($sourceConfigShaAfter -eq $ExpectedSourceConfigSha) 'Source ClickHouse config changed during read-only review.'

    $targetVersionAfter = Get-ExactSingleLine -Label 'target ClickHouse post-review version' -Lines @(Invoke-TargetQuery 'SELECT version() FORMAT TabSeparatedRaw')
    Require-True ($targetVersionAfter -eq $TargetVersion) 'Target ClickHouse version changed during read-only review.'
    $targetConfigShaAfter = Get-TargetFileSha -Path $TargetConfigPath -Label 'target post-review config SHA-256'
    Require-True ($targetConfigShaAfter -eq $ExpectedTargetConfigSha) 'Target config changed during read-only review.'

    $hotPartCountAfterText = Get-ExactSingleLine -Label 'hot_us post-review active part count' -Lines @(Invoke-TargetQuery "SELECT count() FROM system.parts WHERE active AND disk_name='$TargetHotDisk' FORMAT TabSeparatedRaw")
    $hotPartCountAfter = [long]$hotPartCountAfterText
    Require-True ($hotPartCountAfter -eq 0) "hot_us active parts appeared during Stage 1: $hotPartCountAfter"
    $warmPartCountAfterText = Get-ExactSingleLine -Label 'warm_cn post-review active part count' -Lines @(Invoke-TargetQuery "SELECT count() FROM system.parts WHERE active AND disk_name='$WarmDisk' FORMAT TabSeparatedRaw")
    $warmPartCountAfter = [long]$warmPartCountAfterText
    Require-True ($warmPartCountAfter -eq 0) "warm_cn active parts appeared during Stage 1: $warmPartCountAfter"

    $hotMountFactAfter = Get-MountFact $TargetHotMount
    $warmMountFactAfter = Get-MountFact $WarmMount
    Require-True ($hotMountFactAfter.uuid -eq $TargetHotUuid -and $hotMountFactAfter.fstype -eq 'ext4' -and $hotMountFactAfter.size_bytes -eq $TargetHotBytes) 'hot_us identity changed during Stage 1.'
    Require-True ($warmMountFactAfter.uuid -eq $WarmUuid -and $warmMountFactAfter.fstype -eq 'ext4' -and $warmMountFactAfter.size_bytes -eq $WarmBytes) 'warm_cn identity changed during Stage 1.'

    $dAfter = Get-DDriveFact
    Require-True ([bool]$dAfter.floor_satisfied) 'D: fell below the accepted 30% floor during Stage 1.'

    $report = [ordered]@{
        report_version = 'PRODUCTION_US_APPLICATION_CANARY_STAGE1_V1'
        mode = 'READ_ONLY'
        decision = $ReadyDecision
        expected_main = $expected
        git = [ordered]@{
            head = $head
            origin_main = $originMain
            clean_before = $true
        }
        target = [ordered]@{
            distro_already_running = $true
            keeper_pid = $KeeperPid
            server_process_count = $serverPids.Count
            server_linux_pids = @($serverPids | ForEach-Object { [int]([string]$_).Trim() })
            version_before = $targetVersionObserved
            version_after = $targetVersionAfter
            config_sha256_before = $targetConfigSha
            config_sha256_after = $targetConfigShaAfter
            users_sha256 = $targetUsersSha
            hot_us_vhdx = $TargetHotVhdx
            hot_us_vhdx_physical_length = [long]$hotVhdxItem.Length
            hot_us_mount_before = $hotMountFact
            hot_us_mount_after = $hotMountFactAfter
            warm_mount_before = $warmMountFact
            warm_mount_after = $warmMountFactAfter
            disks = $disks
            policies = $policies
            hot_us_active_parts_before = $hotPartCount
            hot_us_active_parts_after = $hotPartCountAfter
            warm_cn_active_parts_before = $warmPartCount
            warm_cn_active_parts_after = $warmPartCountAfter
            required_application_tables_existing = $targetRequiredTableCount
        }
        source = [ordered]@{
            container_id = $SourceContainerId
            container_name = $SourceContainerName
            version_before = $sourceVersion
            version_after = $sourceVersionAfter
            config_sha256_before = $sourceConfigSha
            config_sha256_after = $sourceConfigShaAfter
            accepted_volume = $SourceVolume
            accepted_volume_mount_count = $sourceMounts.Count
            accepted_volume_consumer_names = $sourceConsumers
        }
        capacity = [ordered]@{
            d_before = $dBefore
            d_after = $dAfter
        }
        canary = [ordered]@{
            package_sequence = [int]$summary.package_sequence
            package_file_name = [string]$summary.package_file_name
            package_path = [string]$summary.package_path
            package_size_bytes = [long]$summary.package_size_bytes
            package_sha256 = [string]$summary.package_sha256
            package_id = [string]$summary.package_id
            package_kind = [string]$summary.package_kind
            source_rank = [long]$summary.source_rank
            schema_manifest_sha256 = [string]$summary.schema_manifest_sha256
            storage_policy = [string]$summary.target_storage_policy
        }
        evidence = [ordered]@{
            plan = $planPath
            source_schema = $sourceSchemaPath
            source_schema_review = $reviewPath
            source_schema_summary = $summaryPath
        }
        safety = [ordered]@{
            read_only = $true
            target_write_performed = $false
            source_data_write_performed = $false
            registry_write_performed = $false
            docker_lifecycle_change_performed = $false
            wsl_lifecycle_change_performed = $false
            cn_write_performed = $false
            package_2_executed = $false
            stage2_go_consumed = $false
            full_corpus_authorized = $false
        }
    }
    $reportPath = Join-Path $EvidenceDir 'production_us_application_canary_stage1.json'
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host "evidence_dir=$EvidenceDir"
    Write-Host "package_sequence=$($summary.package_sequence)"
    Write-Host "package_file_name=$($summary.package_file_name)"
    Write-Host "package_size_bytes=$($summary.package_size_bytes)"
    Write-Host "package_sha256=$($summary.package_sha256)"
    Write-Host "package_id=$($summary.package_id)"
    Write-Host "schema_manifest_sha256=$($summary.schema_manifest_sha256)"
    Write-Host "target_config_sha256=$targetConfigShaAfter"
    Write-Host "source_config_sha256=$sourceConfigShaAfter"
    Write-Host "hot_us_uuid=$($hotMountFactAfter.uuid)"
    Write-Host "hot_us_size_bytes=$($hotMountFactAfter.size_bytes)"
    Write-Host "hot_us_active_parts_before=$hotPartCount"
    Write-Host "hot_us_active_parts_after=$hotPartCountAfter"
    Write-Host "warm_cn_active_parts_before=$warmPartCount"
    Write-Host "warm_cn_active_parts_after=$warmPartCountAfter"
    Write-Host "target_required_application_tables_existing=$targetRequiredTableCount"
    Write-Host "source_volume_consumer_count=$($sourceConsumers.Count)"
    Write-Host "d_free_bytes_before=$($dBefore.free_bytes)"
    Write-Host "d_free_bytes_after=$($dAfter.free_bytes)"
    Write-Host "d_30pct_floor_satisfied=$($dAfter.floor_satisfied)"
    Write-Host "decision=$ReadyDecision"
    Write-Host 'read_only=True'
    Write-Host 'package_2_executed=False'
    Write-Host 'stage2_go_consumed=False'
    exit 0
}
catch {
    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    $failure = [ordered]@{
        report_version = 'PRODUCTION_US_APPLICATION_CANARY_STAGE1_V1'
        mode = 'READ_ONLY'
        decision = 'BLOCKED'
        expected_main = $ExpectedMain.ToLowerInvariant()
        error = $_.Exception.Message
        safety = [ordered]@{
            read_only = $true
            target_write_performed = $false
            source_data_write_performed = $false
            registry_write_performed = $false
            docker_lifecycle_change_performed = $false
            wsl_lifecycle_change_performed = $false
            cn_write_performed = $false
            package_2_executed = $false
            stage2_go_consumed = $false
            full_corpus_authorized = $false
        }
    }
    $failure | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $failurePath -Encoding UTF8
    Write-Host "evidence_dir=$EvidenceDir"
    Write-Host 'decision=BLOCKED'
    Write-Host "error=$($_.Exception.Message)"
    Write-Host 'read_only=True'
    Write-Host 'package_2_executed=False'
    Write-Host 'stage2_go_consumed=False'
    exit 2
}
