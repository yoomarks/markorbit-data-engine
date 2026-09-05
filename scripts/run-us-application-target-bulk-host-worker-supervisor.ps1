param(
    [ValidateRange(1, 3600)]
    [int]$PollSeconds = 2,
    [string]$PythonExe = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Require-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$WorkerLauncher = Join-Path $PSScriptRoot 'run-us-application-target-bulk-host-worker.ps1'
$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source

Require-True (Test-Path -LiteralPath $WorkerLauncher -PathType Leaf) "US target bulk host worker launcher is missing: $WorkerLauncher"
Set-Location $RepoRoot

Write-Host 'supervisor=US_APPLICATION_TARGET_BULK_HOST_WORKER_SUPERVISOR_V1'
Write-Host 'worker=US_APPLICATION_TARGET_BULK_HOST_WORKER_V2'
Write-Host 'worker_invocation=ONE_CLAIM_PER_CHILD_PROCESS'
Write-Host 'automatic_plan_approval=False'
Write-Host 'production_mutation_requires_prepared_plan_and_explicit_approval=True'

while ($true) {
    $arguments = @(
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-File', $WorkerLauncher,
        '-Once',
        '-PollSeconds', [string]$PollSeconds,
        '-PythonExe', $PythonExe
    )

    & $PowerShellExe @arguments
    $workerExit = $LASTEXITCODE
    if ($workerExit -ne 0) {
        Write-Error "US target bulk host worker exited with code $workerExit; supervisor is exiting so Task Scheduler can apply its bounded restart policy."
        exit $workerExit
    }

    Start-Sleep -Seconds $PollSeconds
}
