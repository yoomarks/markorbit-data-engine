param(
    [string]$SnapshotPath,
    [string]$ManifestPath,
    [string]$ExpectedContentHash = '',
    [string]$ExpectedSchemaHash = '',
    [string]$EvidenceRoot,
    [int]$SampleRows = 10000,
    [string]$ExpectedMainSha = '',
    [string]$ClickHouseImage = 'clickhouse/clickhouse-server:24.8',
    [switch]$KeepPilotRuntime,
    [switch]$ContractOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pilotDatabase = 'sg_capacity_pilot'
$sampleTable = 'native_sample'
$fullTable = 'native_full'

function Assert-ExactMain([string]$ExpectedSha) {
    $expected = $ExpectedSha.Trim().ToLowerInvariant()
    if ($expected -notmatch '^[0-9a-f]{40}$') {
        throw 'ExpectedMainSha must be an exact 40-character Git SHA.'
    }
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    if ($head -ne $expected -or $origin -ne $expected) {
        throw "Exact main drift detected. expected=$expected head=$head origin=$origin"
    }
    if (git status --porcelain=v1) {
        throw 'Working tree must be clean for the SG capacity pilot.'
    }
    return $expected
}

function Invoke-DockerText([string[]]$Arguments, [string]$Label) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(docker @Arguments 2>&1)
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    if ($code -ne 0) {
        throw "${Label} failed with exit code ${code}: $($lines -join [Environment]::NewLine)"
    }
    return (@($lines) -join [Environment]::NewLine).Trim()
}

function Write-JsonReceipt([object]$Value, [string]$Path) {
    $dir = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $temp = "$Path.part"
    $json = $Value | ConvertTo-Json -Depth 14
    [IO.File]::WriteAllText($temp, $json, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

$ExpectedHeaders = @(
    'Application Number',
    'Filing Date',
    'International Registration Date',
    'Singapore Protection Date',
    'Series Mark Number',
    'Application Type',
    'Trade Mark Type',
    'Description Particular Feature Of Mark',
    'Application Date',
    'Mark Status',
    'Mark Status Date',
    'Status Update Date',
    'Registration Procedure Completion Date',
    'Expiry Date',
    'Publication Date',
    'Last Modified Date',
    'Journal Data',
    'IR Details',
    'IA Details',
    'Transformation Data',
    'Transformation Into Data',
    'Replacement Data',
    'Priority Data',
    'Replacement Replaces Data',
    'Mark Clauses Data',
    'Mark Data',
    'HMG Cases',
    'Other Entries Data',
    'Logogram Data',
    'License Data',
    'Grantor Data',
    'Grantee Data',
    'Security Interest Data',
    'Transfer Data',
    'Documents',
    'Goods And Services Specifications',
    'Priority Claims Details',
    'Current Applicant Proprietor Details',
    'Agent Correspondence Details'
)

function Quote-ChIdentifier([string]$Name) {
    $tick = [char]96
    return ([string]$tick + $Name + [string]$tick)
}

function Get-SchemaText {
    return (($ExpectedHeaders | ForEach-Object { "$(Quote-ChIdentifier $_) String" }) -join ', ')
}

function Assert-SnapshotContract {
    param([string]$CsvPath, [string]$Manifest, [string]$ContentHash, [string]$SchemaHash)
    if (-not [IO.Path]::IsPathRooted($CsvPath)) { throw 'SnapshotPath must be absolute.' }
    if (-not (Test-Path -LiteralPath $CsvPath -PathType Leaf)) { throw "Snapshot missing: $CsvPath" }
    if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { throw "Manifest missing: $Manifest" }
    if ($ContentHash -notmatch '^[0-9a-f]{64}$') { throw 'ExpectedContentHash must be SHA-256-shaped.' }
    if ($SchemaHash -notmatch '^[0-9a-f]{64}$') { throw 'ExpectedSchemaHash must be SHA-256-shaped.' }
    $manifestValue = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$manifestValue.jurisdiction -ne 'SG') { throw 'Manifest jurisdiction is not SG.' }
    if ([string]$manifestValue.source_id -ne 'IPOS_SG_TRADEMARK_APPLICATIONS') { throw 'Manifest source_id drifted.' }
    if ([string]$manifestValue.content_hash -ne $ContentHash) { throw 'Manifest content hash drifted.' }
    if ([string]$manifestValue.schema_hash -ne $SchemaHash) { throw 'Manifest schema hash drifted.' }
    if ([int64]$manifestValue.row_count -lt 1) { throw 'Manifest row_count must be positive.' }
    $baseName = [IO.Path]::GetFileNameWithoutExtension($CsvPath)
    if ($baseName -ne $ContentHash) { throw 'Snapshot filename does not match accepted content hash.' }
    $headerLine = [IO.File]::ReadLines($CsvPath) | Select-Object -First 1
    $observed = @($headerLine.Split(','))
    if ($observed.Count -ne $ExpectedHeaders.Count) { throw "Snapshot header count drifted: $($observed.Count)" }
    $observed[0] = $observed[0].TrimStart([char]0xFEFF)
    for ($i = 0; $i -lt $ExpectedHeaders.Count; $i++) {
        if ($observed[$i] -ne $ExpectedHeaders[$i]) {
            throw "Snapshot header drift at index $i`: expected='$($ExpectedHeaders[$i])' observed='$($observed[$i])'"
        }
    }
    return $manifestValue
}

function Wait-PilotReady([string]$Container) {
    for ($i = 0; $i -lt 60; $i++) {
        $previous = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $out = @(docker exec $Container clickhouse-client --query 'SELECT 1' 2>&1)
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previous }
        if ($code -eq 0 -and (@($out) -join '').Trim() -eq '1') { return }
        Start-Sleep -Seconds 1
    }
    throw 'Disposable ClickHouse pilot did not become ready.'
}

function Get-DataDirBytes([string]$Container) {
    $text = Invoke-DockerText @('exec',$Container,'sh','-lc','du -sb /var/lib/clickhouse | cut -f1') 'pilot data-dir measurement'
    return [int64]$text.Trim()
}

function Get-PilotMetrics([string]$Container, [string]$TableName) {
    $query = "SELECT count() AS active_parts, sum(rows) AS rows, sum(bytes_on_disk) AS bytes_on_disk, sum(data_compressed_bytes) AS data_compressed_bytes, sum(data_uncompressed_bytes) AS data_uncompressed_bytes, sum(marks_bytes) AS marks_bytes, sum(primary_key_bytes_in_memory) AS primary_key_bytes_in_memory, sum(primary_key_bytes_in_memory_allocated) AS primary_key_bytes_in_memory_allocated, sum(secondary_indices_compressed_bytes) AS secondary_indices_compressed_bytes, sum(secondary_indices_uncompressed_bytes) AS secondary_indices_uncompressed_bytes FROM system.parts WHERE database='$pilotDatabase' AND table='$TableName' AND active FORMAT JSONEachRow"
    $json = Invoke-DockerText @('exec',$Container,'clickhouse-client','--query',$query) 'pilot metrics query'
    if (-not $json) { throw "No system.parts metrics returned for $TableName." }
    return ($json | ConvertFrom-Json)
}

function Start-AsyncSql([string]$Container, [string]$Sql) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Sql)
    $encoded = [Convert]::ToBase64String($bytes)
    $shell = "rm -f /tmp/pilot.exit /tmp/pilot.err /tmp/pilot.out /tmp/pilot.sql; echo '$encoded' | base64 -d > /tmp/pilot.sql; if clickhouse-client --multiquery < /tmp/pilot.sql > /tmp/pilot.out 2>/tmp/pilot.err; then echo 0 > /tmp/pilot.exit; else echo 1 > /tmp/pilot.exit; fi"
    [void](Invoke-DockerText @('exec','-d',$Container,'sh','-lc',$shell) 'start async pilot load')
}

function Wait-AsyncSql([string]$Container, [int]$TimeoutSeconds = 7200) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $peak = Get-DataDirBytes $Container
    while ($watch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        Start-Sleep -Seconds 2
        $current = Get-DataDirBytes $Container
        if ($current -gt $peak) { $peak = $current }
        $state = Invoke-DockerText @('exec',$Container,'sh','-lc','if [ -f /tmp/pilot.exit ]; then cat /tmp/pilot.exit; fi') 'poll async pilot load'
        if ($state) {
            $watch.Stop()
            if ($state.Trim() -ne '0') {
                $err = Invoke-DockerText @('exec',$Container,'sh','-lc','tail -n 80 /tmp/pilot.err || true') 'read pilot load error'
                throw "Full SG pilot load failed: $err"
            }
            return [ordered]@{
                elapsed_seconds = [math]::Round($watch.Elapsed.TotalSeconds, 3)
                peak_data_dir_bytes = [int64]$peak
            }
        }
    }
    throw "Full SG pilot load exceeded timeout_seconds=$TimeoutSeconds."
}

function Remove-PilotRuntime([string]$Container, [string]$Volume) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        if ($Container) { docker rm -f $Container 2>&1 | Out-Null }
        if ($Volume) { docker volume rm $Volume 2>&1 | Out-Null }
    }
    finally { $ErrorActionPreference = $previous }
}

if ($ContractOnly) {
    if ($ExpectedHeaders.Count -ne 39) { throw "Expected 39 SG fields; observed=$($ExpectedHeaders.Count)" }
    $schema = Get-SchemaText
    if ($schema -notmatch 'Application Number' -or $schema -notmatch 'Agent Correspondence Details') {
        throw 'SG pilot schema contract markers are missing.'
    }
    if ($ClickHouseImage -ne 'clickhouse/clickhouse-server:24.8') {
        throw 'SG pilot ClickHouse image contract drifted.'
    }
    Write-Host 'IPOS_SG_CAPACITY_PILOT_CONTRACT_PASS'
    exit 0
}

if ($SampleRows -lt 1 -or $SampleRows -gt 100000) {
    throw 'SampleRows must be between 1 and 100000.'
}
if (-not $SnapshotPath) { throw 'SnapshotPath is required.' }
if (-not $ExpectedContentHash) { throw 'ExpectedContentHash is required.' }
if (-not $ExpectedSchemaHash) { throw 'ExpectedSchemaHash is required.' }
if (-not $EvidenceRoot) { throw 'EvidenceRoot is required.' }

Push-Location $repoRoot
try {
    $executionMain = Assert-ExactMain $ExpectedMainSha
    $snapshotResolved = (Resolve-Path -LiteralPath $SnapshotPath).Path
    if (-not $ManifestPath) {
        $ManifestPath = [IO.Path]::Combine([IO.Path]::GetDirectoryName($snapshotResolved), [IO.Path]::GetFileNameWithoutExtension($snapshotResolved) + '.manifest.json')
    }
    $manifestResolved = (Resolve-Path -LiteralPath $ManifestPath).Path
    $contentHash = $ExpectedContentHash.Trim().ToLowerInvariant()
    $schemaHash = $ExpectedSchemaHash.Trim().ToLowerInvariant()
    $manifest = Assert-SnapshotContract $snapshotResolved $manifestResolved $contentHash $schemaHash
    $evidenceFull = [IO.Path]::GetFullPath($EvidenceRoot)
    $repoFull = [IO.Path]::GetFullPath($repoRoot)
    if ($evidenceFull.StartsWith($repoFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'EvidenceRoot must be outside the Git worktree so exact-main remains clean.'
    }
    New-Item -ItemType Directory -Force -Path $evidenceFull | Out-Null
    if ($snapshotResolved -like 'D:\MarkOrbitData\production\clickhouse*' -or $snapshotResolved -like 'E:\MarkOrbitData\production\clickhouse*') {
        throw 'Production ClickHouse storage cannot be used as SG pilot input.'
    }

    $prefix = $contentHash.Substring(0, 12)
    $containerName = "markorbit-sg-capacity-pilot-$prefix"
    $volumeName = "markorbit_sg_capacity_pilot_$prefix"
    $snapshotDir = [IO.Path]::GetDirectoryName($snapshotResolved)
    $fileName = [IO.Path]::GetFileName($snapshotResolved)
    $snapshotBytes = [int64](Get-Item -LiteralPath $snapshotResolved).Length
    $existingContainer = Invoke-DockerText @('ps','-a','--filter',"name=$containerName",'--format','{{.Names}}') 'inspect pilot container'
    if (@($existingContainer -split "`r?`n") -contains $containerName) { throw "Pilot container already exists: $containerName" }
    $existingVolumes = Invoke-DockerText @('volume','ls','--format','{{.Name}}') 'inspect pilot volumes'
    if (@($existingVolumes -split "`r?`n") -contains $volumeName) { throw "Pilot volume already exists: $volumeName" }

    $schema = Get-SchemaText
    $schemaForFile = $schema.Replace("'", "''")
    $orderBy = Quote-ChIdentifier 'Application Number'
    $inputRef = "input/$fileName"
    $containerCreated = $false
    $volumeCreated = $false
    $cleanupPerformed = $false
    $pilotResult = $null
    try {
        [void](Invoke-DockerText @('volume','create',$volumeName) 'create disposable pilot volume')
        $volumeCreated = $true
        $volumeMount = "type=volume,src=$volumeName,dst=/var/lib/clickhouse"
        $inputMount = "type=bind,src=$snapshotDir,dst=/var/lib/clickhouse/user_files/input,readonly"
        [void](Invoke-DockerText @('run','-d','--name',$containerName,'--mount',$volumeMount,'--mount',$inputMount,$ClickHouseImage) 'start disposable pilot ClickHouse')
        $containerCreated = $true
        Wait-PilotReady $containerName
        $imageId = Invoke-DockerText @('inspect','--format','{{.Image}}',$containerName) 'inspect pilot image'

        $createSample = "CREATE DATABASE IF NOT EXISTS $pilotDatabase; CREATE TABLE $pilotDatabase.$sampleTable ($schema) ENGINE=MergeTree ORDER BY $orderBy"
        [void](Invoke-DockerText @('exec',$containerName,'clickhouse-client','--multiquery','--query',$createSample) 'create sample pilot table')
        $sampleSql = "INSERT INTO $pilotDatabase.$sampleTable SELECT * FROM file('$inputRef', CSVWithNames, '$schemaForFile') LIMIT $SampleRows"
        $sampleWatch = [Diagnostics.Stopwatch]::StartNew()
        [void](Invoke-DockerText @('exec',$containerName,'clickhouse-client','--query',$sampleSql) 'load sample pilot rows')
        $sampleWatch.Stop()
        $sampleMetrics = Get-PilotMetrics $containerName $sampleTable
        if ([int64]$sampleMetrics.rows -ne $SampleRows) { throw "Sample row count mismatch: $($sampleMetrics.rows)" }
        [void](Invoke-DockerText @('exec',$containerName,'clickhouse-client','--query',"DROP TABLE $pilotDatabase.$sampleTable SYNC") 'drop sample pilot table')

        $createFull = "CREATE TABLE $pilotDatabase.$fullTable ($schema) ENGINE=MergeTree ORDER BY $orderBy"
        [void](Invoke-DockerText @('exec',$containerName,'clickhouse-client','--query',$createFull) 'create full pilot table')
        $baselineDataDirBytes = Get-DataDirBytes $containerName
        $fullSql = "INSERT INTO $pilotDatabase.$fullTable SELECT * FROM file('$inputRef', CSVWithNames, '$schemaForFile')"
        Start-AsyncSql $containerName $fullSql
        $async = Wait-AsyncSql $containerName
        $fullMetrics = Get-PilotMetrics $containerName $fullTable
        $finalDataDirBytes = Get-DataDirBytes $containerName
        if ([int64]$fullMetrics.rows -ne [int64]$manifest.row_count) {
            throw "Full pilot row count mismatch. expected=$($manifest.row_count) observed=$($fullMetrics.rows)"
        }
        $rawRatio = [math]::Round(([double]$fullMetrics.bytes_on_disk / [double]$snapshotBytes), 6)
        $compressionRatio = if ([double]$fullMetrics.data_uncompressed_bytes -gt 0) {
            [math]::Round(([double]$fullMetrics.data_compressed_bytes / [double]$fullMetrics.data_uncompressed_bytes), 6)
        } else { 0.0 }
        $peakGrowth = [math]::Max(0, [int64]$async.peak_data_dir_bytes - $baselineDataDirBytes)
        $payload = [double]$fullMetrics.bytes_on_disk
        $scenario1x = [int64][math]::Ceiling($payload / 0.70)
        $scenario2x = [int64][math]::Ceiling(($payload * 2.0) / 0.70)
        $scenario4x = [int64][math]::Ceiling(($payload * 4.0) / 0.70)

        $pilotResult = [ordered]@{
            image_id = $imageId
            sample = [ordered]@{ rows=[int64]$sampleMetrics.rows; elapsed_seconds=[math]::Round($sampleWatch.Elapsed.TotalSeconds,3); metrics=$sampleMetrics }
            full = [ordered]@{ rows=[int64]$fullMetrics.rows; elapsed_seconds=[double]$async.elapsed_seconds; metrics=$fullMetrics }
            baseline_data_dir_bytes = [int64]$baselineDataDirBytes
            peak_data_dir_bytes = [int64]$async.peak_data_dir_bytes
            peak_data_dir_growth_bytes = [int64]$peakGrowth
            final_data_dir_bytes = [int64]$finalDataDirBytes
            raw_to_target_bytes_on_disk_ratio = $rawRatio
            compressed_to_uncompressed_ratio = $compressionRatio
            capacity_scenarios = [ordered]@{
                internal_free_target = 0.30
                current_1x_min_virtual_bytes = $scenario1x
                growth_2x_min_virtual_bytes = $scenario2x
                growth_4x_min_virtual_bytes = $scenario4x
                recommendation = 'REVIEW_2X_AND_4X_SCENARIOS_BEFORE_PRODUCTION_PROVISIONING'
            }
        }
    }
    finally {
        if (-not $KeepPilotRuntime -and ($containerCreated -or $volumeCreated)) {
            Remove-PilotRuntime $containerName $volumeName
            $cleanupPerformed = $true
        }
    }

    if ($null -eq $pilotResult) { throw 'Pilot completed without a result payload.' }
    $containerStillExists = $false
    $volumeStillExists = $false
    if (-not $KeepPilotRuntime) {
        $containersAfter = Invoke-DockerText @('ps','-a','--format','{{.Names}}') 'verify pilot container cleanup'
        $volumesAfter = Invoke-DockerText @('volume','ls','--format','{{.Name}}') 'verify pilot volume cleanup'
        $containerStillExists = @($containersAfter -split "`r?`n") -contains $containerName
        $volumeStillExists = @($volumesAfter -split "`r?`n") -contains $volumeName
        if ($containerStillExists -or $volumeStillExists) { throw 'Disposable pilot runtime cleanup verification failed.' }
    }

    $receipt = [ordered]@{
        version = 'IPOS_SG_CAPACITY_PILOT_RECEIPT_V1'
        status = 'PASS'
        issue = 814
        completed_at = [DateTimeOffset]::UtcNow.ToString('o')
        execution_main_sha = $executionMain
        production_mutation_authorized = $false
        production_mutation_performed = $false
        source = [ordered]@{
            jurisdiction = 'SG'
            source_id = [string]$manifest.source_id
            snapshot_path = $snapshotResolved
            manifest_path = $manifestResolved
            content_hash = $contentHash
            schema_hash = $schemaHash
            row_count = [int64]$manifest.row_count
            snapshot_bytes = $snapshotBytes
            field_count = $ExpectedHeaders.Count
            format = 'CSVWithNames_UNCOMPRESSED'
        }
        pilot = $pilotResult
        runtime = [ordered]@{
            container_name = $containerName
            volume_name = $volumeName
            clickhouse_image = $ClickHouseImage
            keep_runtime_requested = [bool]$KeepPilotRuntime
            cleanup_performed = [bool]$cleanupPerformed
            cleanup_verified = [bool](-not $containerStillExists -and -not $volumeStillExists)
        }
        safety = [ordered]@{
            production_vhdx_mutated = $false
            production_wsl_mutated = $false
            production_clickhouse_mutated = $false
            production_data_copied = $false
            source_snapshot_mutated = $false
            us_amplification_reused = $false
        }
        next_gate = 'REVIEW_MEASURED_HOT_GLOBAL_CAPACITY_SCENARIOS'
    }
    $receiptPath = Join-Path $evidenceFull "ipos_sg_capacity_pilot_$prefix.json"
    Write-JsonReceipt $receipt $receiptPath
    $receiptSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $receiptPath).Hash.ToLowerInvariant()
    Write-Host 'Singapore IPOS non-production capacity pilot: PASS'
    Write-Host "Receipt: $receiptPath"
    Write-Host "Receipt SHA256: $receiptSha"
    Write-Host "Rows: $($pilotResult.full.rows)"
    Write-Host "Target bytes_on_disk: $($pilotResult.full.metrics.bytes_on_disk)"
    Write-Host "Raw-to-target ratio: $($pilotResult.raw_to_target_bytes_on_disk_ratio)"
    Write-Host "Peak data-dir growth bytes: $($pilotResult.peak_data_dir_growth_bytes)"
    Write-Host "1x/2x/4x min virtual bytes: $scenario1x / $scenario2x / $scenario4x"
}
finally {
    Pop-Location
}