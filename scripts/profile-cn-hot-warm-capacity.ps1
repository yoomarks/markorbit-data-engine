param(
    [double]$ProjectedUsHotGiB = -1,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $pythonCommand = $null
    $pythonPrefix = @()
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $pythonCommand = $venvPython
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $pythonCommand = (Get-Command python).Source
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $pythonCommand = (Get-Command py).Source
        $pythonPrefix = @("-3")
    }
    else {
        throw "Python 3.12 or 3.13 is required for the CN capacity profile."
    }

    $argsList = @($pythonPrefix) + @("-m", "app.cn.capacity_profile")
    if ($ProjectedUsHotGiB -ge 0) {
        $argsList += @("--projected-us-hot-gib", $ProjectedUsHotGiB.ToString([Globalization.CultureInfo]::InvariantCulture))
    }

    $jsonLines = & $pythonCommand @argsList
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if ($exitCode -ne 0) {
        throw "CN capacity profile failed with exit code $exitCode."
    }
    if (-not $json.Trim()) {
        throw "CN capacity profile produced no JSON."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "CN capacity profile produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = Join-Path "reports" "cn_hot_warm_capacity_$timestamp.json"
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    $gib = 1GB
    $active = $report.active_totals
    $gate = $report.us_scale_out_gate
    $hotDisk = $report.disks | Where-Object { $_.name -eq "default" } | Select-Object -First 1

    Write-Host "CN Hot/Warm capacity profile: $($report.profile_version)"
    Write-Host "Query scope: $($report.query_scope)"
    Write-Host "Full corpus scan: $($report.full_corpus_scan)"
    Write-Host ("Active rows from parts: {0:N0}" -f [double]$active.rows_from_parts)
    Write-Host ("Active bytes on disk: {0:N2} GiB" -f ([double]$active.bytes_on_disk / $gib))
    Write-Host ("Hot-contract bytes: {0:N2} GiB" -f ([double]$active.hot_contract_bytes / $gib))
    Write-Host ("Warm-candidate bytes: {0:N2} GiB" -f ([double]$active.warm_candidate_bytes / $gib))

    if ($null -ne $hotDisk) {
        Write-Host ("Hot disk free: {0:N2} GiB ({1:P1})" -f ([double]$hotDisk.free_space / $gib), [double]$hotDisk.free_ratio)
    }

    if ($null -ne $gate.hard_floor) {
        Write-Host ("Max incremental Hot at 20% floor: {0:N2} GiB" -f ([double]$gate.hard_floor.max_additional_hot_bytes / $gib))
        Write-Host ("Max incremental Hot at 30% recommended floor: {0:N2} GiB" -f ([double]$gate.recommended_floor.max_additional_hot_bytes / $gib))
    }
    Write-Host "US scale-out decision: $($gate.decision)"
    Write-Host "US scale-out reason: $($gate.reason)"
    Write-Host "Report: $OutputPath"
    Write-Host "Capacity profile completed read-only. No table scan, package replay, service lifecycle action, mutation, OPTIMIZE FINAL, or table swap was performed."
}
finally {
    Pop-Location
}
