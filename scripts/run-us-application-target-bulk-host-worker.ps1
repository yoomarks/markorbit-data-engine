param(
    [switch]$Once,
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
Set-Location $RepoRoot

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
Require-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'Run the US target bulk host worker from an elevated Administrator PowerShell.'

$pythonCommand = Get-Command $PythonExe -ErrorAction Stop
Require-True ($null -ne $pythonCommand) "Python executable not found: $PythonExe"

$branch = (& git branch --show-current).Trim()
Require-True ($LASTEXITCODE -eq 0) 'Unable to inspect the current git branch.'
Require-True ($branch -eq 'main') 'US target bulk host worker must run from local main.'

$argsList = @('-m', 'app.us.target_bulk_host_worker_v2', '--poll-seconds', [string]$PollSeconds)
if ($Once) { $argsList += '--once' }

Write-Host 'worker=US_APPLICATION_TARGET_BULK_HOST_WORKER_V2'
Write-Host "mode=$(if ($Once) { 'ONCE' } else { 'CONTINUOUS' })"
Write-Host 'execution_lane=WINDOWS_HOST_TARGET'
Write-Host 'container_worker_claimable=False'
Write-Host 'production_mutation_requires_prepared_plan_and_explicit_approval=True'
Write-Host 'success_requires_master_batch_final_audit=True'
& $PythonExe @argsList
exit $LASTEXITCODE
