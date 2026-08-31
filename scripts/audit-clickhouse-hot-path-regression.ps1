param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$AcceptedHotPath = "E:\MarkOrbitData\hot\clickhouse-cs",
    [string]$RejectedLegacyHotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$ExpectedSchemaSnapshot = "5|5|2026-08-10 12:58:08.545",
    [string]$EvidenceRoot = "reports"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    function Normalize-WindowsPath([string]$Path, [string]$Name) {
        if ([string]::IsNullOrWhiteSpace($Path)) { throw "$Name is required." }
        $candidate = $Path.Replace('/', '\')
        if ($candidate -notmatch '^[A-Za-z]:\\') { throw "$Name is not an absolute Windows path: $Path" }
        return [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
    }

    function Resolve-ExistingDir([string]$Path, [string]$Name) {
        $normalized = Normalize-WindowsPath $Path $Name
        if (-not (Test-Path -LiteralPath $normalized -PathType Container)) {
            throw "$Name missing or invalid: $normalized"
        }
        return (Resolve-Path -LiteralPath $normalized).Path.TrimEnd('\')
    }

    function Get-OptionalDirectoryState([string]$Path, [string]$Name) {
        $normalized = Normalize-WindowsPath $Path $Name
        $exists = Test-Path -LiteralPath $normalized -PathType Container
        $resolved = if ($exists) { (Resolve-Path -LiteralPath $normalized).Path.TrimEnd('\') } else { $null }
        return [ordered]@{
            expected_path = $normalized
            exists = [bool]$exists
            resolved_path = $resolved
        }
    }

    function Get-CaseSensitivity([string]$Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            return [ordered]@{ exists = $false; exit_code = $null; enabled = $null; output = @() }
        }
        $lines = @(& fsutil.exe file queryCaseSensitiveInfo $Path 2>&1 | ForEach-Object { $_.ToString() })
        $exit = $LASTEXITCODE
        $text = ($lines -join ' ').Trim()
        $disabled = $text -match '(?i)\bdisabled\b|已禁用|未启用|禁用'
        $enabled = (-not $disabled) -and ($text -match '(?i)\benabled\b|已启用|启用')
        return [ordered]@{ exists = $true; exit_code = $exit; enabled = $enabled; output = $lines }
    }

    function Get-StoreUuidIndex([string]$Root) {
        if ([string]::IsNullOrWhiteSpace($Root)) { return @() }
        $store = Join-Path $Root 'store'
        if (-not (Test-Path -LiteralPath $store -PathType Container)) { return @() }
        $rows = New-Object System.Collections.Generic.List[string]
        foreach ($prefix in @(Get-ChildItem -LiteralPath $store -Directory -Force -ErrorAction Stop)) {
            foreach ($uuid in @(Get-ChildItem -LiteralPath $prefix.FullName -Directory -Force -ErrorAction Stop)) {
                $rows.Add("$($prefix.Name)/$($uuid.Name)|$($uuid.LastWriteTimeUtc.ToString('o'))")
            }
        }
        return @($rows | Sort-Object)
    }

    function Get-MetadataIndex([string]$Root) {
        if ([string]::IsNullOrWhiteSpace($Root)) { return @() }
        $metadata = Join-Path $Root 'metadata'
        if (-not (Test-Path -LiteralPath $metadata -PathType Container)) { return @() }
        $rows = New-Object System.Collections.Generic.List[string]
        foreach ($file in @(Get-ChildItem -LiteralPath $metadata -File -Recurse -Force -ErrorAction Stop)) {
            $relative = $file.FullName.Substring($metadata.Length).TrimStart('\')
            $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            $rows.Add("$relative|$($file.Length)|$hash")
        }
        return @($rows | Sort-Object)
    }

    function Get-ListDigest([string[]]$Rows) {
        $text = ($Rows -join "`n")
        $bytes = [Text.Encoding]::UTF8.GetBytes($text)
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
    }

    function Get-HotRootSiblingState([string]$HotRoot) {
        $rows = @()
        foreach ($dir in @(Get-ChildItem -LiteralPath $HotRoot -Directory -Force -ErrorAction Stop | Sort-Object Name)) {
            $case = Get-CaseSensitivity $dir.FullName
            $topLevelEntryCount = @(Get-ChildItem -LiteralPath $dir.FullName -Force -ErrorAction Stop).Count
            $rows += [pscustomobject][ordered]@{
                name = $dir.Name
                full_path = $dir.FullName
                creation_time_utc = $dir.CreationTimeUtc.ToString('o')
                last_write_time_utc = $dir.LastWriteTimeUtc.ToString('o')
                top_level_entry_count = $topLevelEntryCount
                case_sensitive = $case.enabled
                case_sensitive_query_exit_code = $case.exit_code
            }
        }
        return $rows
    }

    function Get-CaseChain([string]$Root, [string]$SchemaUuidRelative) {
        $paths = @(
            [ordered]@{ label = 'root'; path = $Root },
            [ordered]@{ label = 'metadata'; path = (Join-Path $Root 'metadata') },
            [ordered]@{ label = 'store'; path = (Join-Path $Root 'store') },
            [ordered]@{ label = 'store_prefix'; path = (Join-Path (Join-Path $Root 'store') '771') },
            [ordered]@{ label = 'schema_version_uuid'; path = (Join-Path (Join-Path $Root 'store') $SchemaUuidRelative) }
        )
        $rows = @()
        foreach ($item in $paths) {
            $case = Get-CaseSensitivity $item.path
            $rows += [pscustomobject][ordered]@{
                label = $item.label
                path = $item.path
                exists = [bool]$case.exists
                case_sensitive = $case.enabled
                query_exit_code = $case.exit_code
                query_output = @($case.output)
            }
        }
        return $rows
    }

    function Get-LocalEnvHotPath([string]$Root) {
        $envPath = Join-Path $Root '.env'
        if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
            return [ordered]@{ env_file_exists = $false; match_count = 0; value = $null }
        }
        $matches = @()
        foreach ($line in @(Get-Content -LiteralPath $envPath -Encoding UTF8)) {
            if ($line -match '^\s*CLICKHOUSE_HOT_DATA_PATH\s*=\s*(.*?)\s*$') {
                $value = $Matches[1].Trim().Trim('"').Trim("'")
                $matches += $value
            }
        }
        return [ordered]@{
            env_file_exists = $true
            match_count = $matches.Count
            value = if ($matches.Count -eq 1) { $matches[0] } else { $null }
        }
    }

    if (git status --porcelain) { throw 'Working tree must be clean.' }
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Audit must run from main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed.' }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) { throw 'Exact-main mismatch.' }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-idle-worker.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Global idle gate failed.' }
    $worker = @(& docker compose ps -a -q worker | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($worker.Count -ne 0) { throw 'Worker containers must be absent.' }

    Write-Host 'audit_stage=resolve_expected_paths'
    $acceptedState = Get-OptionalDirectoryState $AcceptedHotPath 'AcceptedHotPath'
    $legacyState = Get-OptionalDirectoryState $RejectedLegacyHotPath 'RejectedLegacyHotPath'
    $hotRoot = Split-Path -Parent $acceptedState.expected_path
    $legacyHotRoot = Split-Path -Parent $legacyState.expected_path
    if (-not [string]::Equals([string]$hotRoot, [string]$legacyHotRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Accepted and rejected Hot paths must share one Hot parent for this audit.'
    }
    $hotRoot = Resolve-ExistingDir $hotRoot 'HotRoot'

    Write-Host 'audit_stage=inspect_clickhouse_mount'
    $clickhouse = @(& docker compose ps --status running -q clickhouse | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($clickhouse.Count -ne 1) { throw 'Exactly one running ClickHouse container required.' }
    $containerId = $clickhouse[0].Trim()
    $mounts = ((& docker inspect --format '{{json .Mounts}}' $containerId) -join '') | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect ClickHouse mounts.' }
    $dataMounts = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    if ($dataMounts.Count -ne 1) { throw 'Ambiguous ClickHouse data mount.' }
    $actual = Resolve-ExistingDir ([string]$dataMounts[0].Source) 'ActualMountSource'

    $labelsJson = ((& docker inspect --format '{{json .Config.Labels}}' $containerId) -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($labelsJson)) {
        throw 'Unable to inspect ClickHouse Compose labels.'
    }
    $labels = $labelsJson | ConvertFrom-Json
    $composeConfigFiles = [string]$labels.'com.docker.compose.project.config_files'
    $composeWorkingDir = [string]$labels.'com.docker.compose.project.working_dir'

    Write-Host 'audit_stage=verify_schema_snapshot'
    $snapshot = (& docker compose exec -T clickhouse clickhouse-client --query "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL").Trim()
    if ($LASTEXITCODE -ne 0 -or $snapshot -ne $ExpectedSchemaSnapshot) { throw 'schema_version snapshot drifted.' }

    Write-Host 'audit_stage=collect_case_sensitivity'
    $schemaUuidRelative = '771\7716c662-1886-4e4b-a7e2-631c80ac8dd2'
    $actualCaseChain = @(Get-CaseChain $actual $schemaUuidRelative)
    $legacyCase = if ($legacyState.exists) { Get-CaseSensitivity $legacyState.resolved_path } else { Get-CaseSensitivity $legacyState.expected_path }
    $acceptedCase = if ($acceptedState.exists) { Get-CaseSensitivity $acceptedState.resolved_path } else { Get-CaseSensitivity $acceptedState.expected_path }
    $hotSiblings = @(Get-HotRootSiblingState $hotRoot)
    $localEnv = Get-LocalEnvHotPath $repoRoot

    Write-Host '===== ACTIVE MOUNT / COMPOSE PROVENANCE ====='
    Write-Host "actual_hot_mount_source=$actual"
    Write-Host "actual_hot_mount_type=$([string]$dataMounts[0].Type)"
    Write-Host "actual_hot_mount_rw=$([bool]$dataMounts[0].RW)"
    Write-Host "compose_project_config_files=$composeConfigFiles"
    Write-Host "compose_project_working_dir=$composeWorkingDir"
    Write-Host "local_env_file_exists=$($localEnv.env_file_exists)"
    Write-Host "local_env_hot_path_match_count=$($localEnv.match_count)"
    Write-Host "local_env_hot_path=$($localEnv.value)"

    Write-Host '===== EXPECTED PATH STATE ====='
    Write-Host "accepted_hot_path=$($acceptedState.expected_path)"
    Write-Host "accepted_hot_path_exists=$($acceptedState.exists)"
    Write-Host "rejected_legacy_path=$($legacyState.expected_path)"
    Write-Host "rejected_legacy_path_exists=$($legacyState.exists)"
    Write-Host "legacy_case_sensitive=$($legacyCase.enabled)"
    Write-Host "accepted_case_sensitive=$($acceptedCase.enabled)"

    Write-Host '===== ACTUAL PATH CASE-SENSITIVITY CHAIN ====='
    foreach ($item in $actualCaseChain) {
        Write-Host ("actual_case_chain|label={0}|exists={1}|case_sensitive={2}|exit={3}|path={4}" -f `
            $item.label, $item.exists, $item.case_sensitive, $item.query_exit_code, $item.path)
    }

    Write-Host '===== HOT ROOT SIBLINGS ====='
    Write-Host "hot_root=$hotRoot"
    Write-Host "hot_root_sibling_count=$($hotSiblings.Count)"
    foreach ($item in $hotSiblings) {
        Write-Host ("hot_sibling|name={0}|case_sensitive={1}|entries={2}|created={3}|last_write={4}|path={5}" -f `
            $item.name, $item.case_sensitive, $item.top_level_entry_count, $item.creation_time_utc, $item.last_write_time_utc, $item.full_path)
    }

    Write-Host 'audit_stage=index_metadata_store'
    Write-Host '===== METADATA / STORE UUID INDEX ====='
    $actualMetadata = @(Get-MetadataIndex $actual)
    $actualStore = @(Get-StoreUuidIndex $actual)
    $actualUuidNames = @($actualStore | ForEach-Object { ($_ -split '\|', 2)[0] })

    $legacyRoot = if ($legacyState.exists) { $legacyState.resolved_path } else { $null }
    $acceptedRoot = if ($acceptedState.exists) { $acceptedState.resolved_path } else { $null }
    $legacyMetadata = @(Get-MetadataIndex $legacyRoot)
    $acceptedMetadata = @(Get-MetadataIndex $acceptedRoot)
    $legacyStore = @(Get-StoreUuidIndex $legacyRoot)
    $acceptedStore = @(Get-StoreUuidIndex $acceptedRoot)
    $legacyUuidNames = @($legacyStore | ForEach-Object { ($_ -split '\|', 2)[0] })
    $acceptedUuidNames = @($acceptedStore | ForEach-Object { ($_ -split '\|', 2)[0] })

    $actualVsAcceptedMetadataDiff = if ($acceptedState.exists) {
        @(Compare-Object -ReferenceObject $acceptedMetadata -DifferenceObject $actualMetadata).Count
    } else { $null }
    $actualVsAcceptedUuidDiff = if ($acceptedState.exists) {
        @(Compare-Object -ReferenceObject $acceptedUuidNames -DifferenceObject $actualUuidNames).Count
    } else { $null }

    $actualSchemaExists = Test-Path -LiteralPath (Join-Path (Join-Path $actual 'store') $schemaUuidRelative) -PathType Container
    $legacySchemaExists = if ($legacyState.exists) {
        Test-Path -LiteralPath (Join-Path (Join-Path $legacyRoot 'store') $schemaUuidRelative) -PathType Container
    } else { $false }
    $acceptedSchemaExists = if ($acceptedState.exists) {
        Test-Path -LiteralPath (Join-Path (Join-Path $acceptedRoot 'store') $schemaUuidRelative) -PathType Container
    } else { $false }

    $actualIsLegacyName = [string]::Equals([string]$actual, [string]$legacyState.expected_path, [System.StringComparison]::OrdinalIgnoreCase)
    $actualIsAcceptedName = [string]::Equals([string]$actual, [string]$acceptedState.expected_path, [System.StringComparison]::OrdinalIgnoreCase)
    $classification = if (-not $acceptedState.exists -and $actualIsLegacyName) {
        'ACCEPTED_PATH_MISSING_LEGACY_NAME_ACTIVE_POST_CUTOVER_REGRESSION'
    } elseif (-not $acceptedState.exists) {
        'ACCEPTED_PATH_MISSING_UNKNOWN_ACTIVE_POST_CUTOVER_REGRESSION'
    } elseif ($actualIsLegacyName) {
        'REJECTED_LEGACY_HOT_PATH_ACTIVE_OFFLINE_PARITY_REQUIRED'
    } elseif ($actualIsAcceptedName) {
        'ACCEPTED_CLICKHOUSE_CS_ALREADY_ACTIVE'
    } else {
        'UNKNOWN_CLICKHOUSE_HOT_PATH_ACTIVE'
    }

    Write-Host 'audit_stage=write_evidence_report'
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "clickhouse_hot_path_regression_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $reportPath = Join-Path $evidenceDir 'hot_path_regression.json'
    $report = [ordered]@{
        report_version = 'CLICKHOUSE_HOT_PATH_REGRESSION_AUDIT_V2_PS51_SAFE'
        engine_sha = $head
        read_only = $true
        schema_version_snapshot = $snapshot
        active_mount = [ordered]@{
            source = $actual
            type = [string]$dataMounts[0].Type
            rw = [bool]$dataMounts[0].RW
        }
        compose_provenance = [ordered]@{
            project_config_files = $composeConfigFiles
            project_working_dir = $composeWorkingDir
            local_env = $localEnv
        }
        accepted_path = $acceptedState
        rejected_legacy_path = $legacyState
        accepted_case_sensitivity = $acceptedCase
        legacy_case_sensitivity = $legacyCase
        actual_case_sensitivity_chain = $actualCaseChain
        hot_root = $hotRoot
        hot_root_siblings = $hotSiblings
        actual_metadata_count = $actualMetadata.Count
        actual_metadata_digest = Get-ListDigest $actualMetadata
        legacy_metadata_count = $legacyMetadata.Count
        legacy_metadata_digest = Get-ListDigest $legacyMetadata
        accepted_metadata_count = $acceptedMetadata.Count
        accepted_metadata_digest = Get-ListDigest $acceptedMetadata
        actual_vs_accepted_metadata_diff_count = $actualVsAcceptedMetadataDiff
        actual_store_uuid_count = $actualUuidNames.Count
        actual_store_uuid_digest = Get-ListDigest $actualUuidNames
        legacy_store_uuid_count = $legacyUuidNames.Count
        legacy_store_uuid_digest = Get-ListDigest $legacyUuidNames
        accepted_store_uuid_count = $acceptedUuidNames.Count
        accepted_store_uuid_digest = Get-ListDigest $acceptedUuidNames
        actual_vs_accepted_store_uuid_diff_count = $actualVsAcceptedUuidDiff
        actual_schema_version_uuid_exists = [bool]$actualSchemaExists
        legacy_schema_version_uuid_exists = [bool]$legacySchemaExists
        accepted_schema_version_uuid_exists = [bool]$acceptedSchemaExists
        classification = $classification
        safe_to_switch = $false
        clickhouse_restart_performed = $false
        filesystem_mutation_performed = $false
        directory_created = $false
        directory_renamed = $false
        case_sensitivity_changed = $false
        schema_apply_performed = $false
        corpus_replay_performed = $false
    }
    $report | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $reportPath

    Write-Host "actual_metadata_count=$($actualMetadata.Count)"
    Write-Host "legacy_metadata_count=$($legacyMetadata.Count)"
    Write-Host "accepted_metadata_count=$($acceptedMetadata.Count)"
    Write-Host "actual_vs_accepted_metadata_diff_count=$actualVsAcceptedMetadataDiff"
    Write-Host "actual_store_uuid_count=$($actualUuidNames.Count)"
    Write-Host "legacy_store_uuid_count=$($legacyUuidNames.Count)"
    Write-Host "accepted_store_uuid_count=$($acceptedUuidNames.Count)"
    Write-Host "actual_vs_accepted_store_uuid_diff_count=$actualVsAcceptedUuidDiff"
    Write-Host "actual_schema_version_uuid_exists=$actualSchemaExists"
    Write-Host "legacy_schema_version_uuid_exists=$legacySchemaExists"
    Write-Host "accepted_schema_version_uuid_exists=$acceptedSchemaExists"
    Write-Host "classification=$classification"
    Write-Host 'safe_to_switch=False'
    Write-Host 'clickhouse_restart_performed=False'
    Write-Host 'filesystem_mutation_performed=False'
    Write-Host 'directory_created=False'
    Write-Host 'directory_renamed=False'
    Write-Host 'case_sensitivity_changed=False'
    Write-Host 'schema_apply_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Report: $reportPath"
    Write-Host 'CLICKHOUSE_HOT_PATH_REGRESSION_AUDIT_V2_COMPLETE'
}
catch {
    Write-Host 'AUDIT_RUNTIME_FAILURE'
    Write-Host "exception_type=$($_.Exception.GetType().FullName)"
    Write-Host "exception_message=$($_.Exception.Message)"
    $stack = [string]$_.ScriptStackTrace
    if (-not [string]::IsNullOrWhiteSpace($stack)) {
        Write-Host ("script_stack_trace=" + ($stack -replace "`r?`n", ' <- '))
    }
    throw
}
finally { Pop-Location }
