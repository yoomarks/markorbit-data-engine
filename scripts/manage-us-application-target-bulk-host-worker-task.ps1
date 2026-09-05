param(
    [ValidateSet('Install', 'Status', 'Start', 'Stop', 'Restart', 'Uninstall')]
    [string]$Action = 'Status',
    [ValidateRange(1, 3600)]
    [int]$PollSeconds = 2,
    [string]$PythonExe = 'python',
    [string]$TaskName = 'MarkOrbit-US-Application-Target-Bulk-Host-Worker-V2'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Require-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Require-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    Require-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'Run this scheduled-task manager from an elevated Administrator PowerShell.'
}

function Assert-CurrentMainIsDeployable {
    param([string]$RepoRoot)

    Push-Location $RepoRoot
    try {
        $branch = (& git branch --show-current).Trim()
        Require-True ($LASTEXITCODE -eq 0) 'Unable to inspect current git branch.'
        Require-True ($branch -eq 'main') 'US target bulk host worker scheduled task must be installed or started from local main.'

        $dirty = @(& git status --porcelain)
        Require-True ($LASTEXITCODE -eq 0) 'Unable to inspect git working tree.'
        Require-True ($dirty.Count -eq 0) 'US target bulk host worker scheduled task requires a clean working tree.'

        & git fetch origin main --quiet
        Require-True ($LASTEXITCODE -eq 0) 'Unable to refresh origin/main before installing or starting the host worker task.'

        $head = (& git rev-parse HEAD).Trim()
        $originMain = (& git rev-parse origin/main).Trim()
        Require-True ($LASTEXITCODE -eq 0) 'Unable to resolve exact main commit.'
        Require-True ($head -eq $originMain) "Local main must exactly equal origin/main before installing or starting the host worker task. HEAD=$head origin/main=$originMain"
        return $head
    }
    finally {
        Pop-Location
    }
}

function Get-TaskOrNull {
    param([string]$Name)
    return Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SupervisorPath = Join-Path $PSScriptRoot 'run-us-application-target-bulk-host-worker-supervisor.ps1'
$WorkerLauncher = Join-Path $PSScriptRoot 'run-us-application-target-bulk-host-worker.ps1'

Require-True ($env:OS -eq 'Windows_NT') 'US target bulk host worker scheduled-task management is Windows-only.'
Require-True (Test-Path -LiteralPath $SupervisorPath -PathType Leaf) "Supervisor script is missing: $SupervisorPath"
Require-True (Test-Path -LiteralPath $WorkerLauncher -PathType Leaf) "V2 worker launcher is missing: $WorkerLauncher"

if ($Action -eq 'Status') {
    $task = Get-TaskOrNull -Name $TaskName
    if ($null -eq $task) {
        Write-Host "task=$TaskName"
        Write-Host 'installed=False'
        exit 0
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "task=$TaskName"
    Write-Host 'installed=True'
    Write-Host "state=$($task.State)"
    Write-Host "last_run_time=$($info.LastRunTime.ToString('o'))"
    Write-Host "last_task_result=$($info.LastTaskResult)"
    Write-Host "next_run_time=$($info.NextRunTime.ToString('o'))"
    exit 0
}

Require-Administrator

if ($Action -eq 'Install') {
    $executionMain = Assert-CurrentMainIsDeployable -RepoRoot $RepoRoot
    $pythonCommand = Get-Command $PythonExe -ErrorAction Stop
    $powerShellCommand = Get-Command powershell.exe -ErrorAction Stop
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name

    $taskArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -PollSeconds {1} -PythonExe "{2}"' -f $SupervisorPath, $PollSeconds, $pythonCommand.Source
    $taskAction = New-ScheduledTaskAction -Execute $powerShellCommand.Source -Argument $taskArguments -WorkingDirectory $RepoRoot
    $taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew

    $task = New-ScheduledTask `
        -Action $taskAction `
        -Trigger $taskTrigger `
        -Principal $taskPrincipal `
        -Settings $taskSettings `
        -Description 'MarkOrbit US Application hot_us target bulk V2 host worker. Polls dedicated host tasks only; never creates or approves a production plan.'

    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName

    Write-Host "task=$TaskName"
    Write-Host 'installed=True'
    Write-Host 'started=True'
    Write-Host "execution_main=$executionMain"
    Write-Host "run_as=$identity"
    Write-Host 'worker=US_APPLICATION_TARGET_BULK_HOST_WORKER_V2'
    Write-Host 'automatic_plan_approval=False'
    Write-Host 'production_mutation_requires_prepared_plan_and_explicit_approval=True'
    exit 0
}

$existing = Get-TaskOrNull -Name $TaskName
Require-True ($null -ne $existing) "Scheduled task is not installed: $TaskName"

if ($Action -eq 'Start') {
    $executionMain = Assert-CurrentMainIsDeployable -RepoRoot $RepoRoot
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "task=$TaskName"
    Write-Host 'started=True'
    Write-Host "execution_main=$executionMain"
    exit 0
}

if ($Action -eq 'Stop') {
    Stop-ScheduledTask -TaskName $TaskName
    Write-Host "task=$TaskName"
    Write-Host 'stopped=True'
    exit 0
}

if ($Action -eq 'Restart') {
    $executionMain = Assert-CurrentMainIsDeployable -RepoRoot $RepoRoot
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "task=$TaskName"
    Write-Host 'restarted=True'
    Write-Host "execution_main=$executionMain"
    exit 0
}

if ($Action -eq 'Uninstall') {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "task=$TaskName"
    Write-Host 'installed=False'
    exit 0
}

throw "Unsupported action: $Action"
