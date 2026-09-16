param(
    [string]$StateDir,
    [switch]$RecoverStaleLock
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
    if (-not $env:DATA_GOV_SG_API_KEY) {
        throw "DATA_GOV_SG_API_KEY must be set in the current PowerShell session."
    }

    $runningWorker = docker compose ps --status running -q worker
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker Compose worker state."
    }
    if ($runningWorker) {
        throw "A worker container is already running. Finish or stop it before the Singapore IPOS full-corpus run."
    }

    $explicitStateDir = [bool]$StateDir
    $hostState = Resolve-IposSgStateDir -RequestedStateDir $StateDir -RepoRoot $repoRoot
    if (-not (Test-Path -LiteralPath $hostState -PathType Container)) {
        if (-not $explicitStateDir) {
            throw "Resolved production Singapore IPOS state directory does not exist: $hostState. Refusing implicit bootstrap. Use -StateDir explicitly only for an intentional bootstrap."
        }
        New-Item -ItemType Directory -Force -Path $hostState | Out-Null
    }
    $hostState = (Resolve-Path -LiteralPath $hostState).Path

    $appMount = "${repoRoot}\app:/app/app:ro"
    $stateMount = "${hostState}:/app/ipos_sg_state"
    $operatorArgs = @(
        "-m",
        "app.snapshot_delta.ipos_sg_operator",
        "--state-dir",
        "/app/ipos_sg_state"
    )
    if ($RecoverStaleLock) {
        $operatorArgs += "--recover-stale-lock"
    }

    Write-Host "Running leased authenticated Singapore IPOS operator cycle..."
    Write-Host "The same one-shot worker remains alive for preflight, source authentication, full corpus, and postflight."
    Write-Host "State: $hostState"
    docker compose run --rm --no-deps -T `
        --env DATA_GOV_SG_API_KEY `
        --volume $appMount `
        --volume $stateMount `
        worker python @operatorArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Singapore IPOS operator cycle failed. Accepted state, failure evidence, and any recoverable lifecycle artifacts remain in $hostState."
    }

    Write-Host "Singapore IPOS authenticated operator acceptance: PASS"
    Write-Host "State: $hostState"
    Write-Host "Corpus report: $(Join-Path $hostState 'acceptance\latest.json')"
    Write-Host "Operator report: $(Join-Path $hostState 'acceptance\operator_latest.json')"
}
finally {
    Pop-Location
}
