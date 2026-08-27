param(
    [string]$ExpectedFileName = "2023_5.zip",
    [string]$OutputPath = "",
    [switch]$Compact
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
        throw "Python 3.12 or 3.13 is required for the CN serving-state checkpoint."
    }

    # The checkpoint imports the repository database/config runtime. Do not
    # silently fall through to an arbitrary host Python that cannot import the
    # declared project dependencies; fail before the checkpoint with a stable,
    # actionable operator error instead of a ModuleNotFoundError traceback.
    # Keep this support window aligned with pyproject.toml. The pinned
    # psycopg[binary]==3.2.9 runtime does not provide a CPython 3.14 Windows
    # binary wheel, so Python 3.14 must fail before dependency installation or
    # checkpoint execution.
    $probeCode = @'
import importlib.util, json, sys
required = ("clickhouse_connect", "psycopg", "pydantic_settings")
missing = [name for name in required if importlib.util.find_spec(name) is None]
print(json.dumps({
    "python_version": list(sys.version_info[:3]),
    "version_ok": (3, 12) <= sys.version_info < (3, 14),
    "missing": missing,
}))
'@

    # Windows PowerShell 5.1 reconstructs native command lines using legacy
    # quoting rules. Passing a multiline script with embedded quotes through
    # `python -c $probeCode` is not reliable. Write the small probe as UTF-8
    # source and execute the file instead; this keeps the transport independent
    # of native argument quoting while preserving a read-only checkpoint.
    $probePath = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) ("markorbit-cn-serving-state-probe-{0}.py" -f [Guid]::NewGuid().ToString("N"))
    $probeErrorPath = "$probePath.stderr"
    $probeLines = @()
    $probeExitCode = $null
    $probeStdErr = ""
    $utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false

    try {
        [System.IO.File]::WriteAllText($probePath, $probeCode, $utf8NoBom)

        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $probeLines = @(& $pythonCommand @pythonPrefix $probePath 2> $probeErrorPath)
            $probeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }

        if (Test-Path -LiteralPath $probeErrorPath -PathType Leaf) {
            $probeStdErrRaw = Get-Content -LiteralPath $probeErrorPath -Raw
            if ($null -ne $probeStdErrRaw) {
                $probeStdErr = $probeStdErrRaw.Trim()
            }
        }
    }
    finally {
        Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $probeErrorPath -Force -ErrorAction SilentlyContinue
    }

    $probeJson = ($probeLines -join "`n").Trim()
    if ($probeExitCode -ne 0 -or -not $probeJson) {
        $probeDetails = "exit_code=$probeExitCode"
        if ($probeStdErr) {
            $normalizedProbeStdErr = ($probeStdErr -replace "`r?`n", " | ")
            if ($normalizedProbeStdErr.Length -gt 1000) {
                $normalizedProbeStdErr = $normalizedProbeStdErr.Substring(0, 1000) + "..."
            }
            $probeDetails += "; stderr=$normalizedProbeStdErr"
        }
        throw (
            "Unable to validate the CN serving-state Python runtime ($probeDetails). " +
            "Use Python 3.12 or 3.13 to create .venv, then run " +
            ".\.venv\Scripts\python.exe -m pip install -e ."
        )
    }
    try {
        $pythonRuntime = $probeJson | ConvertFrom-Json
    }
    catch {
        throw "CN serving-state Python runtime preflight produced invalid output."
    }

    $missingModules = @($pythonRuntime.missing)
    if ($pythonRuntime.version_ok -ne $true -or $missingModules.Count -gt 0) {
        $versionText = @($pythonRuntime.python_version) -join "."
        $missingText = if ($missingModules.Count -gt 0) {
            $missingModules -join ", "
        }
        else {
            "none"
        }
        throw (
            "CN serving-state Python runtime is incomplete or unsupported: " +
            "python=$versionText; missing_modules=$missingText. " +
            "Use Python 3.12 or 3.13 to create repository .venv, then run " +
            ".\.venv\Scripts\python.exe -m pip install -e ."
        )
    }

    $checkpointArgs = @(
        "-m",
        "app.cn.serving_state_checkpoint",
        "--expected-file-name",
        $ExpectedFileName
    )
    if ($Compact) {
        $checkpointArgs += "--compact"
    }

    # Local, read-only control/system-metadata checkpoint. It never manages
    # database or worker lifecycle; the configured target services must already
    # be reachable through the repository environment.
    $invokeArgs = @($pythonPrefix) + $checkpointArgs
    $jsonLines = & $pythonCommand @invokeArgs
    $exitCode = $LASTEXITCODE
    $json = $jsonLines -join "`n"

    if (-not $json.Trim()) {
        throw "CN serving-state checkpoint produced no JSON report."
    }

    try {
        $report = $json | ConvertFrom-Json
    }
    catch {
        throw "CN serving-state checkpoint produced invalid JSON: $($_.Exception.Message)"
    }

    if (-not $OutputPath) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $safeFileName = $ExpectedFileName -replace '[^A-Za-z0-9._-]', '_'
        $OutputPath = Join-Path "reports" "cn_serving_state_${safeFileName}_$timestamp.json"
    }

    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $json | Set-Content -Encoding UTF8 $OutputPath

    Write-Host "CN serving-state status: $($report.status)"
    Write-Host "Checkpoint version: $($report.checkpoint_version)"
    Write-Host "Expected package: $($report.expected_file_name)"
    Write-Host "Expected package SUCCESS: $($report.expected_package_success)"
    Write-Host "CN packages processing: $($report.processing_package_count)"
    Write-Host "Serving tables ready: $($report.core_tables_ready)"
    Write-Host "Goods schema exact: $($report.goods_schema_exact)"

    foreach ($disk in @($report.disks)) {
        $freePercent = "unknown"
        if ($null -ne $disk.free_ratio) {
            $freePercent = "{0:P1}" -f [double]$disk.free_ratio
        }
        Write-Host "Disk $($disk.name): free=$freePercent path=$($disk.path)"
    }

    $reasonCodes = @($report.reasons | ForEach-Object { $_.code })
    if ($reasonCodes.Count -gt 0) {
        Write-Host "Reason codes: $($reasonCodes -join ', ')"
    }
    Write-Host "Report: $OutputPath"

    if ($exitCode -ne 0) {
        throw "CN serving-state checkpoint blocked: status=$($report.status); reasons=$($reasonCodes -join ', ')"
    }

    Write-Host "CN serving-state checkpoint completed read-only. No service lifecycle action was performed."
}
finally {
    Pop-Location
}
