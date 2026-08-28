param(
    [string]$RecoveryRoot = "F:\MarkOrbitData\recovery\docker-data-reset",
    [string]$PostgresBackupRoot = "F:\MarkOrbitData\recovery\postgres-before-docker-reset",
    [string]$HotPath = "E:\MarkOrbitData\hot\clickhouse",
    [string]$ColdPath = "F:\MarkOrbitData\cold\clickhouse",
    [string]$LogPath = "E:\MarkOrbitData\hot\clickhouse-logs"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Get-HealthyContainer([string]$Name) {
    $raw = docker inspect $Name 2>$null
    Assert-LastExitCode "Container not found: $Name"
    $obj = (($raw | Out-String) | ConvertFrom-Json)[0]
    $health = if ($obj.State.Health) { $obj.State.Health.Status } else { "none" }
    if ($obj.State.Status -ne "running" -or $health -ne "healthy") {
        throw "$Name must be running and healthy. Current=$($obj.State.Status)/$health"
    }
    return $obj
}

function Assert-Hash([string]$Path, [string]$Expected) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Backup file missing: $Path" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) { throw "SHA256 mismatch: $Path" }
    return $actual
}

try {
    Write-Host "`n===== DOCKER DATA RESET PREPARATION PREFLIGHT ====="

    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run from an Administrator PowerShell session."
    }
    Write-Host "ADMINISTRATOR_OK"

    docker info | Out-Null
    Assert-LastExitCode "Docker Engine is unavailable."

    $null = Get-HealthyContainer "markorbit-data-engine-postgres-1"
    $null = Get-HealthyContainer "markorbit-data-engine-clickhouse-1"

    $running = @(docker ps --format "{{.Names}}")
    if ($running -contains "markorbit-data-engine-api-1") { throw "API must remain stopped before reset." }
    if ($running -contains "markorbit-data-engine-worker-1") { throw "Persistent worker must remain stopped before reset." }

    foreach ($p in @($HotPath, $ColdPath, $LogPath)) {
        if (-not (Test-Path -LiteralPath $p -PathType Container)) { throw "Persistent bind path missing: $p" }
    }

    $env:CLICKHOUSE_HOT_DATA_PATH = $HotPath.Replace([char]92, [char]47)
    $env:CLICKHOUSE_COLD_DATA_PATH = $ColdPath.Replace([char]92, [char]47)
    $env:CLICKHOUSE_LOG_PATH = $LogPath.Replace([char]92, [char]47)
    Write-Host "PRE_RESET_RUNTIME_OK"

    Write-Host "`n===== REVERIFY POSTGRES DUAL BACKUP ====="

    $pgBackupDir = Get-ChildItem -LiteralPath $PostgresBackupRoot -Directory |
        Sort-Object LastWriteTime -Descending |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "manifest.json") } |
        Select-Object -First 1
    if ($null -eq $pgBackupDir) { throw "No PostgreSQL dual-backup manifest found under $PostgresBackupRoot" }

    $pgManifestPath = Join-Path $pgBackupDir.FullName "manifest.json"
    $pgManifest = Get-Content -Raw -LiteralPath $pgManifestPath | ConvertFrom-Json
    if ($pgManifest.schema_version -ne "POSTGRES_BEFORE_DOCKER_DATA_RESET_V1") { throw "Unexpected PostgreSQL backup manifest schema." }
    if (-not $pgManifest.postgres_restarted_healthy -or -not $pgManifest.api_worker_remained_stopped) { throw "PostgreSQL backup manifest is not accepted." }
    $logicalHash = Assert-Hash $pgManifest.logical_backup.path $pgManifest.logical_backup.sha256
    $physicalHash = Assert-Hash $pgManifest.cold_pgdata_backup.path $pgManifest.cold_pgdata_backup.sha256
    Write-Host "PostgreSQL backup manifest: $pgManifestPath"
    Write-Host "POSTGRES_DUAL_BACKUP_REVERIFIED"

    Write-Host "`n===== FREEZE CN PRE-RESET EVIDENCE ====="

    New-Item -ItemType Directory -Path $RecoveryRoot -Force | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = Join-Path $RecoveryRoot $stamp
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

    powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-serving-state.ps1 -Compact
    Assert-LastExitCode "CN serving-state must PASS before Docker data reset."

    $capacityPath = Join-Path $backupDir "cn_capacity_pre_docker_reset.json"
    powershell.exe -ExecutionPolicy Bypass -File .\scripts\profile-cn-hot-warm-capacity.ps1 -OutputPath $capacityPath
    Assert-LastExitCode "CN capacity profile failed before Docker data reset."
    $capacity = Get-Content -Raw -LiteralPath $capacityPath | ConvertFrom-Json
    if ($capacity.profile_version -ne "CN_HOT_WARM_CAPACITY_PROFILE_V1" -or [bool]$capacity.full_corpus_scan) {
        throw "Unexpected CN pre-reset capacity profile contract."
    }
    Write-Host "CN_PRE_RESET_EVIDENCE_OK"

    Write-Host "`n===== BACK UP DOCKER DESKTOP SETTINGS ====="

    $settingsSource = Join-Path $env:APPDATA "Docker\settings-store.json"
    $settingsEntry = $null
    if (Test-Path -LiteralPath $settingsSource -PathType Leaf) {
        $settingsTarget = Join-Path $backupDir "docker-settings-store.json"
        Copy-Item -LiteralPath $settingsSource -Destination $settingsTarget -Force
        $settingsHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $settingsTarget).Hash.ToLowerInvariant()
        $settingsEntry = [ordered]@{ source=$settingsSource; backup=$settingsTarget; sha256=$settingsHash }
        Write-Host "Settings backup: $settingsTarget"
        Write-Host "DOCKER_SETTINGS_BACKUP_OK"
    }
    else {
        Write-Warning "Docker settings-store.json was not found at $settingsSource"
    }

    Write-Host "`n===== BACK UP ANCILLARY MARKORBIT VOLUMES ====="

    $ancillary = @()
    $volumeNames = @(docker volume ls --format "{{.Name}}" | Where-Object { $_ -like "markorbit-local_*" })
    foreach ($volumeName in $volumeNames) {
        $safeName = $volumeName -replace '[^A-Za-z0-9_.-]', '_'
        $archiveName = "$safeName.tar.gz"
        $archivePath = Join-Path $backupDir $archiveName
        $volumeMount = "type=volume,source=$volumeName,target=/source,readonly"
        $backupMount = "type=bind,source=$backupDir,target=/backup"
        docker run --rm --mount $volumeMount --mount $backupMount -e "BACKUP_NAME=$archiveName" postgres:16-alpine sh -lc 'set -eu; cd /source; tar -czf "/backup/$BACKUP_NAME" .; gzip -t "/backup/$BACKUP_NAME"; tar -tzf "/backup/$BACKUP_NAME" >/dev/null'
        Assert-LastExitCode "Failed backing up ancillary volume $volumeName"
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
        $ancillary += [ordered]@{ volume=$volumeName; archive=$archivePath; bytes=(Get-Item -LiteralPath $archivePath).Length; sha256=$hash }
        Write-Host "Ancillary volume backed up: $volumeName"
    }
    Write-Host "ANCILLARY_VOLUMES_BACKUP_OK"

    Write-Host "`n===== SAVE DOCKER INVENTORY ====="

    docker info | Set-Content -Encoding utf8 (Join-Path $backupDir "docker-info.txt")
    docker ps -a --no-trunc | Set-Content -Encoding utf8 (Join-Path $backupDir "docker-containers.txt")
    docker volume ls | Set-Content -Encoding utf8 (Join-Path $backupDir "docker-volumes.txt")
    docker image ls --no-trunc | Set-Content -Encoding utf8 (Join-Path $backupDir "docker-images.txt")
    docker system df -v | Set-Content -Encoding utf8 (Join-Path $backupDir "docker-system-df.txt")
    docker compose -f docker-compose.yml -f docker-compose.hot-cold-storage.yml config | Set-Content -Encoding utf8 (Join-Path $backupDir "compose-hot-cold-resolved.yml")
    Assert-LastExitCode "Unable to freeze resolved Hot/Cold Compose configuration."

    $vhdxInventory = @(Get-ChildItem "D:\DockerData\DockerDesktopWSL" -Recurse -Filter *.vhdx -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{ path=$_.FullName; bytes=$_.Length }
    })
    $vhdxInventory | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 (Join-Path $backupDir "docker-vhdx-pre-reset.json")

    $manifestPath = Join-Path $backupDir "manifest.json"
    $manifest = [ordered]@{
        schema_version = "DOCKER_DATA_RESET_PREPARATION_V1"
        created_at = (Get-Date).ToString("o")
        repo_head = (git rev-parse HEAD).Trim()
        postgres_backup_manifest = $pgManifestPath
        postgres_logical_sha256 = $logicalHash
        postgres_pgdata_sha256 = $physicalHash
        docker_settings = $settingsEntry
        ancillary_volumes = @($ancillary)
        hot_path = $HotPath
        cold_path = $ColdPath
        log_path = $LogPath
        cn_profile = $capacityPath
        cn_active_rows = [int64]$capacity.active_totals.rows_from_parts
        cn_active_bytes = [int64]$capacity.active_totals.bytes_on_disk
        vhdx_inventory = @($vhdxInventory)
        destructive_action_performed = $false
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -LiteralPath $manifestPath

    Write-Host "Preparation manifest: $manifestPath"
    Write-Host "CN active rows baseline: $($manifest.cn_active_rows)"
    Write-Host "DOCKER_DATA_RESET_PREPARATION_OK"
}
finally {
    Pop-Location
}
