param(
    [Parameter(Mandatory = $true)]
    [string]$PostgresDataPath,
    [Parameter(Mandatory = $true)]
    [string]$ClickHouseDataPath,
    [Parameter(Mandatory = $true)]
    [string]$ClickHouseLogPath,
    [switch]$RequireExistingDirectories
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

$previous = @{
    POSTGRES_DATA_PATH = $env:POSTGRES_DATA_PATH
    CLICKHOUSE_DATA_PATH = $env:CLICKHOUSE_DATA_PATH
    CLICKHOUSE_LOG_PATH = $env:CLICKHOUSE_LOG_PATH
}

try {
    $paths = [ordered]@{
        POSTGRES_DATA_PATH = $PostgresDataPath
        CLICKHOUSE_DATA_PATH = $ClickHouseDataPath
        CLICKHOUSE_LOG_PATH = $ClickHouseLogPath
    }

    foreach ($entry in $paths.GetEnumerator()) {
        if (-not [System.IO.Path]::IsPathRooted($entry.Value)) {
            throw "$($entry.Key) must be an absolute host path: $($entry.Value)"
        }
        if ($RequireExistingDirectories -and -not (Test-Path -LiteralPath $entry.Value -PathType Container)) {
            throw "$($entry.Key) directory does not exist: $($entry.Value)"
        }
    }

    $env:POSTGRES_DATA_PATH = $PostgresDataPath
    $env:CLICKHOUSE_DATA_PATH = $ClickHouseDataPath
    $env:CLICKHOUSE_LOG_PATH = $ClickHouseLogPath

    $configLines = & docker compose `
        -f docker-compose.yml `
        -f docker-compose.external-storage.yml `
        config --format json
    if ($LASTEXITCODE -ne 0) {
        throw "External-storage Compose configuration failed to render."
    }
    $configJson = $configLines -join "`n"
    if (-not $configJson.Trim()) {
        throw "External-storage Compose configuration produced no JSON."
    }
    try {
        $config = $configJson | ConvertFrom-Json
    }
    catch {
        throw "External-storage Compose configuration produced invalid JSON: $($_.Exception.Message)"
    }

    $expected = @(
        @{ service = "postgres"; target = "/var/lib/postgresql/data"; source = $PostgresDataPath },
        @{ service = "clickhouse"; target = "/var/lib/clickhouse"; source = $ClickHouseDataPath },
        @{ service = "clickhouse"; target = "/var/log/clickhouse-server"; source = $ClickHouseLogPath }
    )

    foreach ($mount in $expected) {
        $service = $config.services.($mount.service)
        if (-not $service) {
            throw "Rendered Compose configuration is missing service $($mount.service)."
        }
        $match = @($service.volumes | Where-Object { $_.target -eq $mount.target })
        if ($match.Count -ne 1) {
            throw "Expected exactly one mount for $($mount.service):$($mount.target); found $($match.Count)."
        }
        if ($match[0].type -ne "bind") {
            throw "External-storage mount is not a bind mount: $($mount.service):$($mount.target)."
        }
        $renderedSource = [System.IO.Path]::GetFullPath([string]$match[0].source).TrimEnd('\', '/')
        $expectedSource = [System.IO.Path]::GetFullPath([string]$mount.source).TrimEnd('\', '/')
        if ($renderedSource -ne $expectedSource) {
            throw "External-storage source mismatch for $($mount.service):$($mount.target): expected $expectedSource, got $renderedSource."
        }
    }

    $report = [ordered]@{
        version = "DATA_ENGINE_EXTERNAL_STORAGE_CONFIG_V1"
        read_only = $true
        status = "PASS"
        compose_files = @("docker-compose.yml", "docker-compose.external-storage.yml")
        mounts = $expected
        note = "Configuration only. No container was started, stopped, recreated, or migrated."
    }
    $report | ConvertTo-Json -Depth 10
}
finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], "Process")
    }
    Pop-Location
}
