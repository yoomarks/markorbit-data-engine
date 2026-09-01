[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$EvidenceRoot = 'reports'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Invoke-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
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
        throw "$Command exited $exitCode: $($lines -join ' | ')"
    }
    return [ordered]@{ exit_code=$exitCode; lines=$lines }
}

function Assert-ExactMain([string]$Phase) {
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $origin = (git rev-parse origin/main).Trim().ToLowerInvariant()
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$origin"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $origin -ne $expected) {
        throw "Exact-main mismatch at $Phase."
    }
    $dirty = @(git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect git working tree.' }
    if (@($dirty | Where-Object { $_.Trim() }).Count -ne 0) {
        throw "Working tree must be clean at $Phase."
    }
}

function Get-RawDataPathEvidence {
    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw '.env is missing; RAW_DATA_PATH provenance cannot be established.'
    }
    $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match '^\s*RAW_DATA_PATH\s*=' } |
        Select-Object -First 1
    if (-not $line) { throw 'RAW_DATA_PATH is not configured in .env.' }
    $value = (($line -split '=',2)[1]).Trim().Trim('"').Trim("'")
    if (-not [System.IO.Path]::IsPathRooted($value)) { $value = Join-Path $repoRoot $value }
    $full = [System.IO.Path]::GetFullPath($value).TrimEnd('\')
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
        throw "Configured RAW_DATA_PATH does not exist as a directory: $full"
    }
    $resolved = (Resolve-Path -LiteralPath $full).Path.TrimEnd('\')
    return [ordered]@{
        configured_path=$value
        resolved_path=$resolved
        drive_root=[System.IO.Path]::GetPathRoot($resolved)
    }
}

function Get-RawTreeProfile([string]$RootPath) {
    $rootFull = [System.IO.Path]::GetFullPath($RootPath).TrimEnd('\')
    $familyMap = @{}
    [int64]$totalFiles = 0
    [int64]$totalBytes = 0
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    foreach ($filePath in [System.IO.Directory]::EnumerateFiles($rootFull, '*', [System.IO.SearchOption]::AllDirectories)) {
        $info = New-Object System.IO.FileInfo($filePath)
        [int64]$length = $info.Length
        $totalFiles++
        $totalBytes += $length

        $relative = $filePath.Substring($rootFull.Length).TrimStart('\')
        $separatorIndex = $relative.IndexOf('\')
        $family = if ($separatorIndex -ge 0) { $relative.Substring(0, $separatorIndex) } else { '__root__' }
        if (-not $familyMap.ContainsKey($family)) {
            $familyMap[$family] = [ordered]@{ family=$family; file_count=[int64]0; total_bytes=[int64]0 }
        }
        $familyMap[$family].file_count = [int64]$familyMap[$family].file_count + 1
        $familyMap[$family].total_bytes = [int64]$familyMap[$family].total_bytes + $length
    }
    $stopwatch.Stop()

    $families = @($familyMap.Values | Sort-Object @{Expression='total_bytes';Descending=$true}, @{Expression='family';Descending=$false})
    return [ordered]@{
        root=$rootFull
        file_count=$totalFiles
        total_bytes=$totalBytes
        scan_elapsed_seconds=[math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
        families=$families
    }
}

function Get-DirectoryNames([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return @() }
    return @(Get-ChildItem -LiteralPath $Path -Directory -Force | Sort-Object Name | ForEach-Object { $_.FullName })
}

function Get-ApiRawMountEvidence {
    $idProbe = Invoke-NativeText 'docker' @(
        'ps','-aq',
        '--filter','label=com.docker.compose.project=markorbit-data-engine',
        '--filter','label=com.docker.compose.service=api'
    ) -AllowFailure
    $ids = @($idProbe.lines | Where-Object { $_.Trim() })
    if ($idProbe.exit_code -ne 0 -or $ids.Count -eq 0) {
        return [ordered]@{ container_present=$false; container_id=$null; source=$null; mount_type=$null; destination='/data/raw' }
    }
    if ($ids.Count -ne 1) { throw "Expected at most one Compose api container; observed $($ids.Count)." }
    $format = '{{range .Mounts}}{{if eq .Destination "/data/raw"}}{{.Source}}|{{.Type}}{{end}}{{end}}'
    $mountProbe = Invoke-NativeText 'docker' @('inspect','-f',$format,$ids[0]) -AllowFailure
    if ($mountProbe.exit_code -ne 0) {
        throw 'Unable to inspect api /data/raw mount.'
    }
    $line = @($mountProbe.lines | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($line.Count -ne 1 -or -not $line[0].Contains('|')) {
        return [ordered]@{ container_present=$true; container_id=$ids[0]; source=$null; mount_type=$null; destination='/data/raw' }
    }
    $fields = $line[0] -split '\|',2
    return [ordered]@{
        container_present=$true
        container_id=$ids[0]
        source=[string]$fields[0]
        mount_type=[string]$fields[1]
        destination='/data/raw'
    }
}

try {
    Write-Host '===== RAW/COLD SOURCE PROVENANCE PROFILE ====='
    if ((git branch --show-current).Trim() -ne 'main') { throw 'Raw/Cold provenance must run from local main.' }
    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fetch origin/main.' }
    Assert-ExactMain 'entry'

    $adminRole = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $adminRole.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Raw/Cold provenance requires elevated Administrator PowerShell.'
    }

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $evidenceDir = Join-Path $repoRoot (Join-Path $EvidenceRoot "raw_cold_source_provenance_$timestamp")
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

    Write-Host 'provenance_stage=configured_raw_path'
    $rawPath = Get-RawDataPathEvidence
    Write-Host "raw_configured_path=$($rawPath.configured_path)"
    Write-Host "raw_resolved_path=$($rawPath.resolved_path)"
    Write-Host "raw_drive_root=$($rawPath.drive_root)"

    Write-Host 'provenance_stage=runtime_bind'
    $apiMount = Get-ApiRawMountEvidence
    Write-Host "api_container_present=$($apiMount.container_present)"
    Write-Host "api_raw_mount_source=$($apiMount.source)"
    Write-Host "api_raw_mount_type=$($apiMount.mount_type)"

    Write-Host 'provenance_stage=raw_tree_single_pass'
    $rawTree = Get-RawTreeProfile $rawPath.resolved_path
    Write-Host "raw_file_count=$($rawTree.file_count)"
    Write-Host "raw_total_bytes=$($rawTree.total_bytes)"
    Write-Host "raw_scan_elapsed_seconds=$($rawTree.scan_elapsed_seconds)"
    foreach ($family in @($rawTree.families)) {
        Write-Host "raw_family=$($family.family)`tfiles=$($family.file_count)`tbytes=$($family.total_bytes)"
    }

    Write-Host 'provenance_stage=shallow_target_inventory'
    $shallow = [ordered]@{
        D_root=Get-DirectoryNames 'D:\'
        E_root=Get-DirectoryNames 'E:\'
        F_root=Get-DirectoryNames 'F:\'
        D_markorbit=Get-DirectoryNames 'D:\MarkOrbitData'
        E_markorbit=Get-DirectoryNames 'E:\MarkOrbitData'
        F_markorbit=Get-DirectoryNames 'F:\MarkOrbitData'
    }
    foreach ($key in @('D_root','E_root','F_root','D_markorbit','E_markorbit','F_markorbit')) {
        foreach ($path in @($shallow[$key])) { Write-Host "shallow_$key=$path" }
    }

    $candidatePaths = @(
        'F:\raw_data',
        'F:\MarkOrbitData\raw_data',
        'F:\MarkOrbitData\raw',
        'F:\MarkOrbitData\raw-cold',
        'F:\yoomarks\markorbit-data-engine\raw_data',
        'E:\raw_data',
        'E:\MarkOrbitData\raw_data',
        'E:\MarkOrbitData\raw',
        'E:\yoomarks\markorbit-data-engine\raw_data'
    )
    $candidates = @()
    foreach ($candidate in $candidatePaths) {
        $exists = Test-Path -LiteralPath $candidate -PathType Container
        $entry = [ordered]@{ path=$candidate; exists=[bool]$exists }
        $candidates += $entry
        Write-Host "candidate_path=$candidate`texists=$exists"
    }

    $decision = 'RAW_COLD_SOURCE_PROVENANCE_CAPTURED'
    $receipt = [ordered]@{
        receipt_version='RAW_COLD_SOURCE_PROVENANCE_V1'
        decision=$decision
        read_only=$true
        engine_sha=$ExpectedMainSha.Trim().ToLowerInvariant()
        configured_raw=$rawPath
        api_raw_mount=$apiMount
        raw_tree=$rawTree
        shallow_directories=$shallow
        target_candidates=$candidates
        safety=[ordered]@{
            migration_authorized=$false
            env_change_authorized=$false
            raw_copy_authorized=$false
            raw_move_authorized=$false
            raw_delete_authorized=$false
            vhdx_mutation_authorized=$false
            docker_restart_authorized=$false
            clickhouse_mutation_authorized=$false
            corpus_replay_authorized=$false
            us_package_2_authorized=$false
            us_bulk_authorized=$false
        }
        mutations_performed=[ordered]@{
            env_change=$false
            raw_copy=$false
            raw_move=$false
            raw_delete=$false
            vhdx_mutation=$false
            docker_restart=$false
            clickhouse_mutation=$false
            corpus_replay=$false
        }
    }

    $receiptPath = Join-Path $evidenceDir 'raw_cold_source_provenance.json'
    $receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Write-Host '===== RAW/COLD SOURCE PROVENANCE RESULT ====='
    Write-Host "decision=$decision"
    Write-Host 'migration_authorized=False'
    Write-Host 'env_change_authorized=False'
    Write-Host 'raw_copy_authorized=False'
    Write-Host 'raw_move_authorized=False'
    Write-Host 'raw_delete_authorized=False'
    Write-Host 'vhdx_mutation_performed=False'
    Write-Host 'docker_restart_performed=False'
    Write-Host 'clickhouse_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'
    Write-Host "Evidence directory: $evidenceDir"
    Write-Host 'RAW_COLD_SOURCE_PROVENANCE_DONE'

    Assert-ExactMain 'exit'
}
finally {
    Pop-Location
}
