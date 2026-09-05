param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMain,

    [Parameter(Mandatory = $true)]
    [ValidateSet('GO #526 Stage 2 bounded US Application canary')]
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
$Stage1AcceptedMain = 'd92f430913ef0684c386c2d7bcb767aa2d3284f8'
$Stage1ReadyDecision = 'BOUNDED_US_APPLICATION_CANARY_REVIEW_READY_FOR_OPERATOR_GO'
$Stage2Decision = 'BOUNDED_US_APPLICATION_CANARY_STAGE2_PACKAGE2_ACCEPTED'
$ExpectedPackageFile = 'apc18840407-20251231-02.zip'
$ExpectedPackagePath = 'F:\MarkOrbitData\raw\incoming\us\apc18840407-20251231-02.zip'
[long]$ExpectedPackageSize = 5997232
$ExpectedPackageSha = '96555bf13b6e8c2f2ede3433c88e4c600b7115ef3e4d7d22f28c8263cada60c7'
$ExpectedPackageId = 'aec9c8b5-f680-5881-94fb-71a1f8e44152'
$ExpectedSchemaManifestSha = 'ff801dea29e5f4b146e5e7ca24507abf4d7d498f977af64e1bc2e14267f63795'

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
        & wsl.exe -d $TargetDistro -u root -- clickhouse client --host $TargetHost --port $TargetPort --query $Query
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
    $device = Get-ExactSingleLine -Label "mount source $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount source $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root -- findmnt -n -o SOURCE --target $MountPoint
        }
    )
    $fstype = Get-ExactSingleLine -Label "mount filesystem $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount filesystem $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root -- findmnt -n -o FSTYPE --target $MountPoint
        }
    )
    $uuid = Get-ExactSingleLine -Label "mount UUID $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount UUID $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root -- blkid -s UUID -o value $device
        }
    )
    $sizeText = Get-ExactSingleLine -Label "mount size $MountPoint" -Lines @(
        Invoke-NativeCapture -Label "mount size $MountPoint" -Command {
            & wsl.exe -d $TargetDistro -u root -- blockdev --getsize64 $device
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
        & wsl.exe -d $TargetDistro -u root -- pgrep -f '[c]lickhouse server --config-file=/opt/markorbit-clickhouse-production/config.xml'
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

function Find-AcceptedStage1Evidence {
    param([string]$ReportsRoot)
    $dirs = @(Get-ChildItem -LiteralPath $ReportsRoot -Directory -Filter 'production_us_application_canary_stage1_*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)
    foreach ($dir in $dirs) {
        $reportPath = Join-Path $dir.FullName 'production_us_application_canary_stage1.json'
        $reviewPath = Join-Path $dir.FullName 'stage1_source_schema_review.json'
        if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf) -or -not (Test-Path -LiteralPath $reviewPath -PathType Leaf)) { continue }
        try {
            $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([string]$report.decision -ne $Stage1ReadyDecision) { continue }
            if ([string]$report.expected_main -ne $Stage1AcceptedMain) { continue }
            if ([int]$report.canary.package_sequence -ne 2) { continue }
            if ([string]$report.canary.package_file_name -ne $ExpectedPackageFile) { continue }
            if ([long]$report.canary.package_size_bytes -ne $ExpectedPackageSize) { continue }
            if ([string]$report.canary.package_sha256 -ne $ExpectedPackageSha) { continue }
            if ([string]$report.canary.package_id -ne $ExpectedPackageId) { continue }
            if ([string]$report.canary.schema_manifest_sha256 -ne $ExpectedSchemaManifestSha) { continue }
            if (-not [bool]$report.safety.read_only -or [bool]$report.safety.package_2_executed -or [bool]$report.safety.stage2_go_consumed) { continue }
            return [ordered]@{ directory = $dir.FullName; report = $reportPath; review = $reviewPath }
        }
        catch { continue }
    }
    throw 'No accepted #526 Stage 1 evidence directory matches the frozen Package 2 contract.'
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$ReportsRoot = Join-Path $RepoRoot 'reports'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd_HHmmss')
$EvidenceDir = Join-Path $ReportsRoot "production_us_application_canary_stage2_$stamp"
$StateDir = Join-Path $ReportsRoot ("production_us_application_canary_stage2_state\package2_" + $ExpectedPackageSha)
$JournalPath = Join-Path $StateDir 'canary_journal.json'
$ReceiptPath = Join-Path $EvidenceDir 'stage2_python_receipt.json'
$FinalReportPath = Join-Path $EvidenceDir 'production_us_application_canary_stage2.json'
$FailurePath = Join-Path $EvidenceDir 'stage2_failure.json'

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    Require-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'Run Stage 2 from an elevated Administrator PowerShell.'
    $branch = Get-ExactSingleLine -Label 'git branch' -Lines @(Invoke-NativeCapture -Label 'git branch' -Command { & git branch --show-current })
    Require-True ($branch -eq 'main') 'Stage 2 must run from local main.'
    Assert-ExactExecutionMain -Phase 'entry' -FetchOrigin $true

    $pythonCommand = Get-Command $PythonExe -ErrorAction Stop
    Require-True ([bool]$pythonCommand) "Python executable not found: $PythonExe"
    Require-True (Test-Path -LiteralPath $ExpectedPackagePath -PathType Leaf) "Frozen Package 2 source is missing: $ExpectedPackagePath"
    $packageItem = Get-Item -LiteralPath $ExpectedPackagePath
    Require-True ([long]$packageItem.Length -eq $ExpectedPackageSize) "Frozen Package 2 size drifted: $($packageItem.Length)"
    $packageShaBefore = (Get-FileHash -LiteralPath $ExpectedPackagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Require-True ($packageShaBefore -eq $ExpectedPackageSha) "Frozen Package 2 SHA-256 drifted before mutation: $packageShaBefore"

    $stage1 = Find-AcceptedStage1Evidence -ReportsRoot $ReportsRoot
    Assert-TargetRuntime -Phase 'pre-mutation'
    $storageBefore = Assert-StorageTopology -Phase 'pre-mutation'
    $dBefore = Get-DDriveFact
    Require-True ([bool]$dBefore.floor_satisfied) "D: free space is below the accepted 30% floor before Stage 2: $($dBefore.free_bytes)"

    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null

    Assert-ExactExecutionMain -Phase 'immediate-pre-mutation' -FetchOrigin $true
    $pythonLines = @(Invoke-NativeCapture -Label '#526 Stage 2 bounded Package 2 executor' -Command {
        & $PythonExe -m app.us.target_canary_stage2 `
            --stage1-report $stage1.report `
            --stage1-review $stage1.review `
            --journal-json $JournalPath `
            --receipt-json $ReceiptPath `
            --authority-token $Authority
    })
    Require-True ($pythonLines.Count -gt 0) 'Stage 2 Python executor returned no receipt output.'
    Require-True (Test-Path -LiteralPath $ReceiptPath -PathType Leaf) 'Stage 2 Python receipt was not written.'
    $receipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Require-True ([string]$receipt.decision -eq $Stage2Decision) "Unexpected Stage 2 receipt decision: $($receipt.decision)"
    Require-True ([string]$receipt.package.sha256 -eq $ExpectedPackageSha) 'Stage 2 receipt Package 2 SHA drifted.'
    Require-True ([string]$receipt.package.package_id -eq $ExpectedPackageId) 'Stage 2 receipt Package 2 id drifted.'
    Require-True ([string]$receipt.schema.manifest_sha256 -eq $ExpectedSchemaManifestSha) 'Stage 2 receipt schema SHA drifted.'
    Require-True ([string]$receipt.journal.state -eq 'COMPLETE') 'Stage 2 journal is not COMPLETE.'
    Require-True ([bool]$receipt.authority.consumed) 'Stage 2 authority was not consumed by the bounded executor.'
    Require-True (-not [bool]$receipt.safety.package_3_executed -and -not [bool]$receipt.safety.full_corpus_executed -and -not [bool]$receipt.safety.automatic_next_package) 'Stage 2 receipt violated the bounded stop point.'

    $packageShaAfter = (Get-FileHash -LiteralPath $ExpectedPackagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Require-True ($packageShaAfter -eq $ExpectedPackageSha) "Frozen Package 2 SHA-256 changed during Stage 2: $packageShaAfter"
    Assert-TargetRuntime -Phase 'post-commit'
    $storageAfter = Assert-StorageTopology -Phase 'post-commit'
    $nonHotParts = [long](Get-ExactSingleLine -Label 'Application non-hot active parts' -Lines @(Invoke-TargetQuery "SELECT count() FROM system.parts WHERE active AND database='markorbit_facts' AND disk_name!='hot_us' FORMAT TabSeparatedRaw"))
    Require-True ($nonHotParts -eq 0) "Application target parts escaped hot_us: $nonHotParts"
    $dAfter = Get-DDriveFact
    Require-True ([bool]$dAfter.floor_satisfied) "D: free space fell below the accepted 30% floor after Stage 2: $($dAfter.free_bytes)"

    $headAfter = Get-ExactSingleLine -Label 'git HEAD post-commit' -Lines @(Invoke-NativeCapture -Label 'git HEAD post-commit' -Command { & git rev-parse HEAD })
    Require-True ($headAfter.ToLowerInvariant() -eq $ExpectedMain.ToLowerInvariant()) 'Local execution code HEAD changed during Stage 2.'
    $dirtyAfter = @(Invoke-NativeCapture -Label 'git status post-commit' -Command { & git status --porcelain=v1 --untracked-files=normal })
    Require-True ($dirtyAfter.Count -eq 0) 'Working tree changed during Stage 2.'

    $final = [ordered]@{
        report_version = 'PRODUCTION_US_APPLICATION_CANARY_STAGE2_V1'
        decision = $Stage2Decision
        execution_main = $ExpectedMain.ToLowerInvariant()
        stage1_accepted_main = $Stage1AcceptedMain
        authority = $Authority
        stage1_evidence = $stage1
        package = [ordered]@{
            sequence = 2
            file_name = $ExpectedPackageFile
            path = $ExpectedPackagePath
            size_bytes = $ExpectedPackageSize
            sha256_before = $packageShaBefore
            sha256_after = $packageShaAfter
            package_id = $ExpectedPackageId
        }
        journal = [ordered]@{
            path = $JournalPath
            state = [string]$receipt.journal.state
            revision = [long]$receipt.journal.revision
        }
        target = [ordered]@{
            distro = $TargetDistro
            version = $TargetVersion
            config_sha256 = $ExpectedTargetConfigSha
            users_sha256 = $ExpectedTargetUsersSha
            storage_before = $storageBefore
            storage_after = $storageAfter
            application_non_hot_active_parts = $nonHotParts
        }
        capacity = [ordered]@{ d_before = $dBefore; d_after = $dAfter }
        receipt = $ReceiptPath
        safety = [ordered]@{
            stage2_go_consumed = $true
            package_2_executed = $true
            package_3_executed = $false
            full_corpus_executed = $false
            automatic_next_package = $false
            registry_write_performed = $false
            cn_write_performed = $false
            source_file_preserved = $true
            docker_lifecycle_change_performed = $false
            wsl_lifecycle_change_performed = $false
        }
    }
    $final | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $FinalReportPath -Encoding UTF8

    Write-Host "evidence_dir=$EvidenceDir"
    Write-Host "execution_main=$($ExpectedMain.ToLowerInvariant())"
    Write-Host 'package_sequence=2'
    Write-Host "package_file_name=$ExpectedPackageFile"
    Write-Host "package_sha256=$ExpectedPackageSha"
    Write-Host "package_id=$ExpectedPackageId"
    Write-Host "schema_manifest_sha256=$ExpectedSchemaManifestSha"
    Write-Host "journal_state=$($receipt.journal.state)"
    Write-Host 'stage2_go_consumed=True'
    Write-Host 'package_2_executed=True'
    Write-Host 'package_3_executed=False'
    Write-Host 'full_corpus_executed=False'
    Write-Host 'automatic_next_package=False'
    Write-Host "decision=$Stage2Decision"
    exit 0
}
catch {
    New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    $failure = [ordered]@{
        report_version = 'PRODUCTION_US_APPLICATION_CANARY_STAGE2_V1'
        decision = 'BLOCKED'
        execution_main = $ExpectedMain.ToLowerInvariant()
        authority = $Authority
        error = $_.Exception.Message
        journal_path = $JournalPath
        journal_exists = (Test-Path -LiteralPath $JournalPath -PathType Leaf)
        safety = [ordered]@{
            blind_retry_permitted = $false
            package_3_executed = $false
            full_corpus_executed = $false
            automatic_next_package = $false
        }
    }
    $failure | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $FailurePath -Encoding UTF8
    Write-Host "evidence_dir=$EvidenceDir"
    Write-Host 'decision=BLOCKED'
    Write-Host "journal_path=$JournalPath"
    Write-Host "journal_exists=$(Test-Path -LiteralPath $JournalPath -PathType Leaf)"
    Write-Host "error=$($_.Exception.Message)"
    Write-Host 'blind_retry_permitted=False'
    Write-Host 'package_3_executed=False'
    Write-Host 'full_corpus_executed=False'
    exit 2
}
