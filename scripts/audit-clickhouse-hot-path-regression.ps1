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
    function Resolve-Dir([string]$Path, [string]$Name) {
        $candidate = $Path.Replace('/', '\')
        if ($candidate -notmatch '^[A-Za-z]:\\' -or -not (Test-Path -LiteralPath $candidate -PathType Container)) {
            throw "$Name missing or invalid: $Path"
        }
        return (Resolve-Path -LiteralPath $candidate).Path.TrimEnd('\')
    }
    function Get-CaseSensitivity([string]$Path) {
        $lines = @(& fsutil.exe file queryCaseSensitiveInfo $Path 2>&1 | ForEach-Object { $_.ToString() })
        $exit = $LASTEXITCODE
        $text = ($lines -join ' ').Trim()
        $disabled = $text -match '(?i)\bdisabled\b|已禁用|未启用|禁用'
        $enabled = (-not $disabled) -and ($text -match '(?i)\benabled\b|已启用|启用')
        return [ordered]@{ exit_code = $exit; enabled = $enabled; output = $lines }
    }
    function Get-StoreUuidIndex([string]$Root) {
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

    $accepted = Resolve-Dir $AcceptedHotPath 'AcceptedHotPath'
    $legacy = Resolve-Dir $RejectedLegacyHotPath 'RejectedLegacyHotPath'
    $clickhouse = @(& docker compose ps --status running -q clickhouse | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($clickhouse.Count -ne 1) { throw 'Exactly one running ClickHouse container required.' }
    $mounts = ((& docker inspect --format '{{json .Mounts}}' $clickhouse[0]) -join '') | ConvertFrom-Json
    $dataMounts = @($mounts | Where-Object { [string]$_.Destination -eq '/var/lib/clickhouse' })
    if ($dataMounts.Count -ne 1) { throw 'Ambiguous ClickHouse data mount.' }
    $actual = Resolve-Dir ([string]$dataMounts[0].Source) 'ActualMountSource'

    $snapshot = (& docker compose exec -T clickhouse clickhouse-client --query "SELECT concat(toString(count()), '|', toString(countDistinct(component)), '|', toString(max(applied_at))) FROM markorbit_facts.schema_version FINAL").Trim()
    if ($LASTEXITCODE -ne 0 -or $snapshot -ne $ExpectedSchemaSnapshot) { throw 'schema_version snapshot drifted.' }

    Write-Host '===== CASE SENSITIVITY ====='
    $legacyCase = Get-CaseSensitivity $legacy
    $acceptedCase = Get-CaseSensitivity $accepted
    Write-Host "actual_hot_mount_source=$actual"
    Write-Host "legacy_case_sensitive=$($legacyCase.enabled)"
    Write-Host "accepted_case_sensitive=$($acceptedCase.enabled)"

    Write-Host '===== METADATA / STORE UUID INDEX ====='
    $legacyMetadata = @(Get-MetadataIndex $legacy)
    $acceptedMetadata = @(Get-MetadataIndex $accepted)
    $legacyStore = @(Get-StoreUuidIndex $legacy)
    $acceptedStore = @(Get-StoreUuidIndex $accepted)
    $legacyUuidNames = @($legacyStore | ForEach-Object { ($_ -split '\|', 2)[0] })
    $acceptedUuidNames = @($acceptedStore | ForEach-Object { ($_ -split '\|', 2)[0] })
    $uuidDiff = @(Compare-Object -ReferenceObject $acceptedUuidNames -DifferenceObject $legacyUuidNames)
    $metadataDiff = @(Compare-Object -ReferenceObject $acceptedMetadata -DifferenceObject $legacyMetadata)

    $schemaUuidRelative = '771/7716c662-1886-4e4b-a7e2-631c80ac8dd2'
    $legacySchemaExists = Test-Path -LiteralPath (Join-Path (Join-Path $legacy 'store') $schemaUuidRelative) -PathType Container
    $acceptedSchemaExists = Test-Path -LiteralPath (Join-Path (Join-Path $accepted 'store') $schemaUuidRelative) -PathType Container

    $classification = if ($actual.Equals($legacy, [System.StringComparison]::OrdinalIgnoreCase)) {
        'REJECTED_LEGACY_HOT_PATH_ACTIVE_OFFLINE_PARITY_REQUIRED'
    } elseif ($actual.Equals($accepted, [System.StringComparison]::OrdinalIgnoreCase)) {
        'ACCEPTED_CLICKHOUSE_CS_ALREADY_ACTIVE'
    } else {
        'UNKNOWN_CLICKHOUSE_HOT_PATH_ACTIVE'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $EvidenceRoot "clickhouse_hot_path_regression_$timestamp"
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    $reportPath = Join-Path $evidenceDir 'hot_path_regression.json'
    $report = [ordered]@{
        report_version = 'CLICKHOUSE_HOT_PATH_REGRESSION_AUDIT_V1'
        engine_sha = $head
        read_only = $true
        actual_mount_source = $actual
        rejected_legacy_path = $legacy
        accepted_hot_path = $accepted
        schema_version_snapshot = $snapshot
        legacy_case_sensitivity = $legacyCase
        accepted_case_sensitivity = $acceptedCase
        legacy_metadata_count = $legacyMetadata.Count
        accepted_metadata_count = $acceptedMetadata.Count
        legacy_metadata_digest = Get-ListDigest $legacyMetadata
        accepted_metadata_digest = Get-ListDigest $acceptedMetadata
        metadata_diff_count = $metadataDiff.Count
        legacy_store_uuid_count = $legacyUuidNames.Count
        accepted_store_uuid_count = $acceptedUuidNames.Count
        legacy_store_uuid_digest = Get-ListDigest $legacyUuidNames
        accepted_store_uuid_digest = Get-ListDigest $acceptedUuidNames
        store_uuid_diff_count = $uuidDiff.Count
        legacy_schema_version_uuid_exists = [bool]$legacySchemaExists
        accepted_schema_version_uuid_exists = [bool]$acceptedSchemaExists
        classification = $classification
        safe_to_switch = $false
        clickhouse_restart_performed = $false
        filesystem_mutation_performed = $false
        schema_apply_performed = $false
        corpus_replay_performed = $false
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $reportPath

    Write-Host "legacy_metadata_count=$($legacyMetadata.Count)"
    Write-Host "accepted_metadata_count=$($acceptedMetadata.Count)"
    Write-Host "metadata_diff_count=$($metadataDiff.Count)"
    Write-Host "legacy_store_uuid_count=$($legacyUuidNames.Count)"
    Write-Host "accepted_store_uuid_count=$($acceptedUuidNames.Count)"
    Write-Host "store_uuid_diff_count=$($uuidDiff.Count)"
    Write-Host "legacy_schema_version_uuid_exists=$legacySchemaExists"
    Write-Host "accepted_schema_version_uuid_exists=$acceptedSchemaExists"
    Write-Host "classification=$classification"
    Write-Host 'safe_to_switch=False'
    Write-Host 'filesystem_mutation_performed=False'
    Write-Host 'schema_apply_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Report: $reportPath"
    Write-Host 'CLICKHOUSE_HOT_PATH_REGRESSION_AUDIT_COMPLETE'
}
finally { Pop-Location }
