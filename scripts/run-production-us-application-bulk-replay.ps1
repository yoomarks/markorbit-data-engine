param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMain,

    [Parameter(Mandatory = $true)]
    [string]$PlanPath,

    [Parameter(Mandatory = $true)]
    [string]$Authority,

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
$Stage2Decision = 'BOUNDED_US_APPLICATION_CANARY_STAGE2_PACKAGE2_ACCEPTED'
$Package2Sha = '96555bf13b6e8c2f2ede3433c88e4c600b7115ef3e4d7d22f28c8263cada60c7'
$Package2Id = 'aec9c8b5-f680-5881-94fb-71a1f8e44152'
$SchemaSha = 'ff801dea29e5f4b146e5e7ca24507abf4d7d498f977af64e1bc2e14267f63795'
$RawRoot = 'F:\MarkOrbitData\raw'

function Require-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
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
    param([object[]]$Lines, [string]$Label)
    $materialized = @($Lines | ForEach-Object { [string]$_ })
    Require-True ($materialized.Count -eq 1) "$Label returned unexpected line count: $($materialized.Count)"
    return $materialized[0].Trim()
}

function Normalize-WslNames {
    param([string[]]$Lines)
    return @(
        $Lines |
            ForEach-Object { ([string]$_).Replace([string][char]0, [string]'').Trim() } |
            Where-Object { $_ }
    )
}

function Invoke-TargetQuery {
    param([string]$Query)
    return Invoke-NativeCapture -Label 'target ClickHouse read-only query' -Command {
        & wsl.exe -d $TargetDistro -u root --exec clickhouse client --host $TargetHost --port $TargetPort --query $Query
    }
}

function Get-TargetFileSha {
    param([string]$Path, [string]$Label)
    $line = Get-ExactSingleLine -Label $Label -Lines @(Invoke-NativeCapture -Label $Label -Command {
        & wsl.exe -d $TargetDistro -u root --exec sha256sum $Path
    })
    Require-True ($line -match '^([0-9a-fA-F]{64})\s+') "$Label returned an unparseable SHA-256 line."
    return $Matches[1].ToLowerInvariant()
}

function Get-MountFact {
    param([string]$MountPoint)
    $device = Get-ExactSingleLine -Label "mount source $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount source $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root --exec findmnt -n -o SOURCE --target $MountPoint
        }
    )
    $fstype = Get-ExactSingleLine -Label "mount filesystem $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount filesystem $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root --exec findmnt -n -o FSTYPE --target $MountPoint
        }
    )
    $uuid = Get-ExactSingleLine -Label "mount UUID $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount UUID $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root --exec blkid -s UUID -o value $device
        }
    )
    $sizeText = Get-ExactSingleLine -Label "mount size $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount size $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root --exec blockdev --getsize64 $device
        }
    )
    return [ordered]@{
        mountpoint = $MountPoint
        device = $device
        fstype = $fstype
        uuid = $uuid
        size_bytes = [long]$sizeText
    }
}

function Get-DDriveFact {
    $drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'" -ErrorAction Stop
    Require-True ($null -ne $drive) 'D: logical disk is unavailable.'
    [long]$total = $drive.Size
    [long]$free = $drive.FreeSpace
    Require-True ($total -gt 0) 'D: total size is invalid.'
    [long]$floor = [math]::Ceiling([double]$total * 0.30)
    return [ordered]@{
        total_bytes = $total
        free_bytes = $free
        free_percent = [math]::Round(($free / [double]$total) * 100.0, 6)
        floor_30pct_bytes = $floor
        floor_satisfied = ($free -ge $floor)
    }
}

function Assert-ExactExecutionMain {
    param([string]$Phase, [bool]$FetchOrigin)
    if ($FetchOrigin) {
        Invoke-NativeCapture -Label "git fetch origin main ($Phase)" -Command { & git fetch origin main } | Out-Null
    }
    $branch = Get-ExactSingleLine -Label "git branch ($Phase)" -Lines @(Invoke-NativeCapture -Label "git branch ($Phase)" -Command { & git branch --show-current })
    Require-True ($branch -eq 'main') "Bulk replay must run from local main during $Phase."
    $head = Get-ExactSingleLine -Label "git HEAD ($Phase)" -Lines @(Invoke-NativeCapture -Label "git HEAD ($Phase)" -Command { & git rev-parse HEAD })
    $originMain = Get-ExactSingleLine -Label "git origin/main ($Phase)" -Lines @(Invoke-NativeCapture -Label "git origin/main ($Phase)" -Command { & git rev-parse origin/main })
    $expected = $ExpectedMain.ToLowerInvariant()
    Require-True ($head.ToLowerInvariant() -eq $expected) "HEAD mismatch during ${Phase}: expected=$expected actual=$head"
    Require-True ($originMain.ToLowerInvariant() -eq $expected) "origin/main mismatch during ${Phase}: expected=$expected actual=$originMain"
    $dirty = @(Invoke-NativeCapture -Label "git status ($Phase)" -Command { & git status --porcelain=v1 --untracked-files=normal })
    Require-True ($dirty.Count -eq 0) "Working tree is not exactly clean during $Phase."
}

function Assert-TargetRuntime {
    param([string]$Phase)
    $runningRaw = @(Invoke-NativeCapture -Label "WSL running-list inspection ($Phase)" -Command { & wsl.exe --list --running --quiet })
    $running = Normalize-WslNames $runningRaw
    Require-True ($running -contains $TargetDistro) "Target distro is not already running during $Phase; refusing any command that could start it."

    $keeper = Get-CimInstance Win32_Process -Filter "ProcessId=$KeeperPid" -ErrorAction SilentlyContinue
    Require-True ($null -ne $keeper) "Accepted target keeper PID $KeeperPid is missing during $Phase."
    Require-True ([string]$keeper.Name -eq 'wsl.exe') "Accepted target keeper PID $KeeperPid is no longer wsl.exe during $Phase."
    Require-True ([string]$keeper.CommandLine -match [regex]::Escape($TargetDistro)) "Accepted target keeper distro binding drifted during $Phase."
    Require-True ([string]$keeper.CommandLine -match 'tail\s+-f\s+/dev/null') "Accepted target keeper command drifted during $Phase."

    $serverPids = @(Invoke-NativeCapture -Label "target server process inspection ($Phase)" -Command {
        & wsl.exe -d $TargetDistro -u root --exec pgrep -f '[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml'
    })
    $serverPids = @($serverPids | Where-Object { ([string]$_).Trim() -match '^\d+$' })
    Require-True ($serverPids.Count -eq 1) "Expected exactly one target ClickHouse server during $Phase; observed=$($serverPids.Count)"

    $version = Get-ExactSingleLine -Label "target ClickHouse version ($Phase)" -Lines @(Invoke-TargetQuery 'SELECT version() FORMAT TabSeparatedRaw')
    Require-True ($version -eq $TargetVersion) "Target ClickHouse version mismatch during ${Phase}: $version"
    $configSha = Get-TargetFileSha -Path $TargetConfigPath -Label "target config SHA-256 ($Phase)"
    $usersSha = Get-TargetFileSha -Path $TargetUsersPath -Label "target users SHA-256 ($Phase)"
    Require-True ($configSha -eq $ExpectedTargetConfigSha) "Target config SHA-256 drifted during ${Phase}: $configSha"
    Require-True ($usersSha -eq $ExpectedTargetUsersSha) "Target users SHA-256 drifted during ${Phase}: $usersSha"
}

function Assert-StorageTopology {
    param([string]$Phase)
    Require-True (Test-Path -LiteralPath $TargetHotVhdx -PathType Leaf) "Accepted production hot_us VHDX is missing during $Phase."
    $hot = Get-MountFact $TargetHotMount
    $warm = Get-MountFact $WarmMount
    Require-True ($hot.fstype -eq 'ext4' -and $hot.uuid -eq $TargetHotUuid -and $hot.size_bytes -eq $TargetHotBytes) "hot_us identity drifted during $Phase."
    Require-True ($warm.fstype -eq 'ext4' -and $warm.uuid -eq $WarmUuid -and $warm.size_bytes -eq $WarmBytes) "warm_cn identity drifted during $Phase."

    $diskLines = @(Invoke-TargetQuery "SELECT name,path FROM system.disks WHERE name IN ('$TargetHotDisk','$WarmDisk') ORDER BY name FORMAT JSONEachRow")
    $disks = @($diskLines | Where-Object { ([string]$_).Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    Require-True ($disks.Count -eq 2) "Expected hot_us + warm_cn disks during $Phase."
    $hotDiskRows = @($disks | Where-Object { $_.name -eq $TargetHotDisk -and $_.path -eq $TargetHotPath })
    $warmDiskRows = @($disks | Where-Object { $_.name -eq $WarmDisk -and $_.path -eq $WarmPath })
    Require-True ($hotDiskRows.Count -eq 1) "hot_us ClickHouse disk path drifted during $Phase."
    Require-True ($warmDiskRows.Count -eq 1) "warm_cn ClickHouse disk path drifted during $Phase."

    $policyLines = @(Invoke-TargetQuery "SELECT policy_name,disks FROM system.storage_policies WHERE policy_name IN ('$TargetHotPolicy','$WarmPolicy') ORDER BY policy_name,volume_priority FORMAT JSONEachRow")
    $policies = @($policyLines | Where-Object { ([string]$_).Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
    $hotPolicies = @($policies | Where-Object { $_.policy_name -eq $TargetHotPolicy })
    $warmPolicies = @($policies | Where-Object { $_.policy_name -eq $WarmPolicy })
    Require-True ($hotPolicies.Count -eq 1 -and @($hotPolicies[0].disks).Count -eq 1 -and [string]$hotPolicies[0].disks[0] -eq $TargetHotDisk) "hot_us_only policy drifted during $Phase."
    Require-True ($warmPolicies.Count -eq 1 -and @($warmPolicies[0].disks).Count -eq 1 -and [string]$warmPolicies[0].disks[0] -eq $WarmDisk) "warm_cn_only policy drifted during $Phase."

    $warmParts = [long](Get-ExactSingleLine -Label "warm_cn active parts ($Phase)" -Lines @(Invoke-TargetQuery "SELECT count() FROM system.parts WHERE active AND disk_name='$WarmDisk' FORMAT TabSeparatedRaw"))
    Require-True ($warmParts -eq 0) "warm_cn unexpectedly contains active parts during ${Phase}: $warmParts"
    return [ordered]@{ hot = $hot; warm = $warm; warm_cn_active_parts = $warmParts }
}

function Find-AcceptedStage2Receipt {
    param([string]$ReportsRoot)
    $dirs = @(Get-ChildItem -LiteralPath $ReportsRoot -Directory -Filter 'production_us_application_canary_stage2_*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
    foreach ($dir in $dirs) {
        $path = Join-Path $dir.FullName 'stage2_python_receipt.json'
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try {
            $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([string]$receipt.decision -ne $Stage2Decision) { continue }
            if ([int]$receipt.package.sequence -ne 2) { continue }
            if ([string]$receipt.package.sha256 -ne $Package2Sha) { continue }
            if ([string]$receipt.package.package_id -ne $Package2Id) { continue }
            if ([string]$receipt.schema.manifest_sha256 -ne $SchemaSha) { continue }
            if ([string]$receipt.journal.state -ne 'COMPLETE') { continue }
            if (-not [bool]$receipt.authority.consumed) { continue }
            if ([bool]$receipt.safety.package_3_executed -or [bool]$receipt.safety.full_corpus_executed -or [bool]$receipt.safety.automatic_next_package) { continue }
            return $path
        }
        catch { continue }
    }
    throw 'No accepted Package 2 Stage 2 Python receipt matches the frozen #526 contract.'
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$ReportsRoot = Join-Path $RepoRoot 'reports'
$ResolvedPlanPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $PlanPath))
$ReportsPrefix = [System.IO.Path]::GetFullPath($ReportsRoot).TrimEnd('\') + '\'
Require-True ($ResolvedPlanPath.StartsWith($ReportsPrefix, [System.StringComparison]::OrdinalIgnoreCase)) 'Bulk PlanPath must be a repo-local reports artifact.'
Require-True (Test-Path -LiteralPath $ResolvedPlanPath -PathType Leaf) "Bulk PlanPath does not exist: $ResolvedPlanPath"

$plan = Get-Content -LiteralPath $ResolvedPlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$PlanSha = [string]$plan.plan_sha256
Require-True ($PlanSha -match '^[0-9a-f]{64}$') 'Bulk plan SHA-256 is invalid.'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
$EvidenceDir = Join-Path $ReportsRoot "production_us_application_bulk_$stamp"
$StateDir = Join-Path $ReportsRoot 'production_us_application_bulk_state'
$BulkJournalPath = Join-Path $StateDir ("bulk_" + $PlanSha + '.journal.json')
$PythonReceiptPath = Join-Path $EvidenceDir 'bulk_python_receipt.json'
$FinalReportPath = Join-Path $EvidenceDir 'production_us_application_bulk.json'
$FailurePath = Join-Path $EvidenceDir 'bulk_failure.json'

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    Require-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'Run bulk replay from an elevated Administrator PowerShell.'
    Require-True (Test-Path -LiteralPath $RawRoot -PathType Container) "Accepted F: Raw root is missing: $RawRoot"
    Require-True ([string]$plan.execution_main -eq $ExpectedMain.ToLowerInvariant()) 'Bulk plan execution_main does not match ExpectedMain.'
    Require-True ([bool]$plan.read_only -and -not [bool]$plan.production_mutation_authorized) 'Frozen plan must remain read-only and non-authorizing.'
    Require-True ([string]$Authority -eq [string]$plan.required_authority_token) 'Authority does not exactly bind the frozen bulk plan SHA.'
    Require-True ([int]$plan.bridge_sequence -eq 1) 'Bulk plan Package 1 target bridge contract drifted.'
    Require-True ([int]$plan.accepted_existing_target_sequence -eq 2) 'Bulk plan accepted Package 2 anchor contract drifted.'
    Require-True ([int]$plan.start_sequence -ge 3 -and [int]$plan.end_sequence -ge [int]$plan.start_sequence -and [int]$plan.end_sequence -le 310) 'Bulk plan range is not an explicit bounded sequence-3+ range.'
    Require-True ([int]$plan.package_count -eq (1 + [int]$plan.suffix_package_count)) 'Bulk plan package count drifted.'
    Require-True ([string]$plan.accepted_schema_manifest_sha256 -eq $SchemaSha) 'Bulk plan accepted target schema SHA drifted.'

    $pythonCommand = Get-Command $PythonExe -ErrorAction Stop
    Require-True ($null -ne $pythonCommand) "Python executable not found: $PythonExe"
    $stage2Receipt = Find-AcceptedStage2Receipt -ReportsRoot $ReportsRoot

    Assert-ExactExecutionMain -Phase 'entry' -FetchOrigin $true
    Assert-TargetRuntime -Phase 'entry'
    $storageBefore = Assert-StorageTopology -Phase 'entry'
    $dBefore = Get-DDriveFact
    Require-True ([bool]$dBefore.floor_satisfied) "D: free space is below the accepted 30% floor before bulk replay: $($dBefore.free_bytes)"

    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null

    Assert-ExactExecutionMain -Phase 'immediate-pre-mutation' -FetchOrigin $true
    Invoke-NativeCapture -Label '#545 bounded US Application target bulk executor' -Command {
        & $PythonExe -m app.us.target_bulk_cli execute `
            --plan $ResolvedPlanPath `
            --stage2-receipt $stage2Receipt `
            --journal $BulkJournalPath `
            --state-dir $StateDir `
            --authority-token $Authority `
            --receipt $PythonReceiptPath
    } | Out-Null

    Require-True (Test-Path -LiteralPath $PythonReceiptPath -PathType Leaf) 'Bulk Python receipt was not written.'
    $receipt = Get-Content -LiteralPath $PythonReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Require-True ([string]$receipt.decision -eq 'BOUNDED_US_APPLICATION_BULK_REPLAY_RANGE_COMPLETE') "Unexpected bulk receipt decision: $($receipt.decision)"
    Require-True ([string]$receipt.plan_sha256 -eq $PlanSha) 'Bulk receipt plan SHA drifted.'
    Require-True ([string]$receipt.inventory_sha256 -eq [string]$plan.inventory_sha256) 'Bulk receipt inventory SHA drifted.'
    Require-True ([string]$receipt.execution_main -eq $ExpectedMain.ToLowerInvariant()) 'Bulk receipt execution_main drifted.'
    Require-True ([string]$receipt.journal_state -eq 'COMPLETE') 'Bulk journal is not COMPLETE.'
    Require-True (-not [bool]$receipt.automatic_next_package) 'Bulk receipt escaped the frozen plan through automatic next-package behavior.'

    Assert-TargetRuntime -Phase 'post-execution'
    $storageAfter = Assert-StorageTopology -Phase 'post-execution'
    $dAfter = Get-DDriveFact
    Require-True ([bool]$dAfter.floor_satisfied) "D: free space is below the accepted 30% floor after bulk replay: $($dAfter.free_bytes)"
    Assert-ExactExecutionMain -Phase 'exit' -FetchOrigin $true

    $final = [ordered]@{
        report_version = 'PRODUCTION_US_APPLICATION_BULK_WRAPPER_V1'
        decision = [string]$receipt.decision
        execution_main = $ExpectedMain.ToLowerInvariant()
        plan_path = $ResolvedPlanPath
        plan_sha256 = $PlanSha
        inventory_sha256 = [string]$plan.inventory_sha256
        stage2_receipt = $stage2Receipt
        authority = $Authority
        bridge_sequence = 1
        accepted_existing_target_sequence = 2
        start_sequence = [int]$plan.start_sequence
        end_sequence = [int]$plan.end_sequence
        suffix_package_count = [int]$plan.suffix_package_count
        bulk_journal = $BulkJournalPath
        bulk_python_receipt = $PythonReceiptPath
        target = [ordered]@{
            storage_before = $storageBefore
            storage_after = $storageAfter
            hot_us_headroom = $receipt.target_audit.hot_us_headroom
        }
        capacity = [ordered]@{
            d_before = $dBefore
            d_after = $dAfter
        }
        safety = [ordered]@{
            source_files_preserved = $true
            automatic_next_package = $false
            resume_only_from_durable_journal = $true
            accepted_volume_mutation_performed = $false
            docker_lifecycle_change_performed = $false
            wsl_lifecycle_change_performed = $false
            warm_cn_write_performed = $false
            final_table_delete_performed = $false
            final_table_drop_performed = $false
        }
    }
    $final | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $FinalReportPath -Encoding UTF8

    Write-Host "decision=$($receipt.decision)"
    Write-Host "evidence_dir=$EvidenceDir"
    Write-Host "execution_main=$($ExpectedMain.ToLowerInvariant())"
    Write-Host "plan_sha256=$PlanSha"
    Write-Host "inventory_sha256=$($plan.inventory_sha256)"
    Write-Host 'bridge_sequence=1'
    Write-Host 'accepted_existing_target_sequence=2'
    Write-Host "start_sequence=$($plan.start_sequence)"
    Write-Host "end_sequence=$($plan.end_sequence)"
    Write-Host "suffix_package_count=$($plan.suffix_package_count)"
    Write-Host "journal_state=$($receipt.journal_state)"
    Write-Host "next_sequence=$($receipt.next_sequence)"
    Write-Host "full_accepted_source_corpus_on_target=$($receipt.full_accepted_source_corpus_on_target)"
    Write-Host 'automatic_next_package=False'
    exit 0
}
catch {
    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    $failure = [ordered]@{
        report_version = 'PRODUCTION_US_APPLICATION_BULK_WRAPPER_V1'
        decision = 'BLOCKED'
        execution_main = $ExpectedMain.ToLowerInvariant()
        plan_path = $ResolvedPlanPath
        plan_sha256 = $PlanSha
        authority = $Authority
        error = $_.Exception.Message
        bulk_journal = $BulkJournalPath
        journal_exists = (Test-Path -LiteralPath $BulkJournalPath -PathType Leaf)
        safety = [ordered]@{
            blind_retry_permitted = $false
            resume_only_from_durable_journal = $true
            automatic_next_package = $false
            accepted_volume_mutation_performed = $false
            docker_lifecycle_change_performed = $false
            wsl_lifecycle_change_performed = $false
        }
    }
    $failure | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $FailurePath -Encoding UTF8
    Write-Host "decision=BLOCKED"
    Write-Host "evidence_dir=$EvidenceDir"
    Write-Host "plan_sha256=$PlanSha"
    Write-Host "journal_path=$BulkJournalPath"
    Write-Host "journal_exists=$(Test-Path -LiteralPath $BulkJournalPath -PathType Leaf)"
    Write-Host "error=$($_.Exception.Message)"
    Write-Host 'blind_retry_permitted=False'
    Write-Host 'resume_only_from_durable_journal=True'
    Write-Host 'automatic_next_package=False'
    exit 2
}
