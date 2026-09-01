[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [string]$RuntimeDistro = 'MarkOrbit-ClickHouse-Spike',
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [int]$RuntimeTimeoutSeconds = 15,
    [int]$UnmountTimeoutSeconds = 30,
    [string]$EvidenceRoot = 'reports',
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Assert-ExactMain([string]$Phase) {
    $expected = $ExpectedMainSha.Trim().ToLowerInvariant()
    $head = (git rev-parse HEAD).Trim().ToLowerInvariant()
    $originMain = (git rev-parse origin/main).Trim().ToLowerInvariant()
    Write-Host "exact_main_phase=$Phase"
    Write-Host "HEAD=$head"
    Write-Host "origin/main=$originMain"
    Write-Host "expected=$expected"
    if ($head -ne $expected -or $originMain -ne $expected) {
        throw "Exact main drift detected during $Phase."
    }
    if (git status --porcelain) {
        throw "Working tree must be clean during $Phase."
    }
}

try {
    Write-Host '===== WSL NO-ARG UNMOUNT RECOVERY ====='
    Write-Host 'safety_status=PERMANENTLY_DISABLED_AFTER_DOCKER_DESKTOP_SIGBUS'
    Write-Host 'incident_reference=2026-09-01 Docker Desktop containerd SIGBUS after no-argument WSL unmount'
    Write-Host 'docker_custom_wsl_data_root=D:\DockerData\DockerDesktopWSL'

    if ((git branch --show-current).Trim() -ne 'main') {
        throw 'WSL no-arg recovery safety receipt must run from local main.'
    }

    & git fetch origin main | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to fetch origin/main.'
    }
    Assert-ExactMain 'entry'

    Write-Host 'decision=WSL_NOARG_UNMOUNT_RECOVERY_PERMANENTLY_DISABLED'
    Write-Host 'no_arg_unmount_authorized=False'
    Write-Host 'no_arg_unmount_performed=False'
    Write-Host 'wsl_mount_performed=False'
    Write-Host 'wsl_shutdown_performed=False'
    Write-Host 'runtime_distro_unregister_performed=False'
    Write-Host 'spike_vhdx_mutation_performed=False'
    Write-Host 'production_clickhouse_restart_performed=False'
    Write-Host 'production_clickhouse_mutation_performed=False'
    Write-Host 'accepted_volume_mutation_performed=False'
    Write-Host 'corpus_replay_performed=False'

    if ($Apply) {
        throw 'No-argument WSL unmount recovery is permanently disabled after the Docker Desktop containerd SIGBUS incident. Use only path-specific or product-owned recovery primitives whose ownership boundary can be proven before mutation.'
    }

    Assert-ExactMain 'exit'
    Write-Host 'WSL_NOARG_UNMOUNT_RECOVERY_DISABLED_RECEIPT_DONE'
}
finally {
    Pop-Location
}
