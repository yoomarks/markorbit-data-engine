param(
    [string]$StateDir
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-IposSgStateDir {
    param(
        [string]$RequestedStateDir,
        [string]$RepoRoot
    )

    if ($RequestedStateDir) {
        if ([System.IO.Path]::IsPathRooted($RequestedStateDir)) {
            return [System.IO.Path]::GetFullPath($RequestedStateDir)
        }
        return [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $RequestedStateDir))
    }

    $rawDataPath = $env:RAW_DATA_PATH
    if (-not $rawDataPath) {
        $envPath = Join-Path $RepoRoot ".env"
        if (Test-Path -LiteralPath $envPath) {
            foreach ($line in Get-Content -LiteralPath $envPath) {
                if ($line -match '^\s*RAW_DATA_PATH\s*=\s*(.+?)\s*$') {
                    $rawDataPath = $Matches[1].Trim()
                    if ($rawDataPath.Length -ge 2) {
                        $first = [int][char]$rawDataPath[0]
                        $last = [int][char]$rawDataPath[$rawDataPath.Length - 1]
                        if (($first -eq 34 -and $last -eq 34) -or ($first -eq 39 -and $last -eq 39)) {
                            $rawDataPath = $rawDataPath.Substring(1, $rawDataPath.Length - 2)
                        }
                    }
                    break
                }
            }
        }
    }

    if (-not $rawDataPath) {
        throw "StateDir was not supplied and RAW_DATA_PATH is not configured; refusing repo-local fallback."
    }
    if ([System.IO.Path]::IsPathRooted($rawDataPath)) {
        $rawRoot = [System.IO.Path]::GetFullPath($rawDataPath)
    }
    else {
        $rawRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $rawDataPath))
    }
    return [System.IO.Path]::GetFullPath((Join-Path $rawRoot "ipos_sg"))
}
Push-Location $repoRoot
try {
    $hostState = Resolve-IposSgStateDir -RequestedStateDir $StateDir -RepoRoot $repoRoot
    if (-not (Test-Path -LiteralPath $hostState -PathType Container)) {
        throw "Singapore IPOS retained state directory does not exist: $hostState. No directory was created."
    }
    $hostState = (Resolve-Path -LiteralPath $hostState).Path

    $appMount = "${repoRoot}\app:/app/app:ro"
    $stateMount = "${hostState}:/app/ipos_sg_state:ro"

    docker compose run --rm --no-deps -T `
        --volume $appMount `
        --volume $stateMount `
        worker python -m app.snapshot_delta.ipos_sg_state `
            --state-dir /app/ipos_sg_state
    if ($LASTEXITCODE -ne 0) {
        throw "Singapore IPOS lifecycle state audit is BLOCKED. No data was modified."
    }

    Write-Host "Singapore IPOS lifecycle state audit completed read-only."
    Write-Host "State: $hostState"
}
finally {
    Pop-Location
}
