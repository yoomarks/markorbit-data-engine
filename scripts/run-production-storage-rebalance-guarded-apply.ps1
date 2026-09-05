[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedMainSha,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Phase1E','Phase2D')]
    [string]$Phase,
    [string]$AcceptedVolume = 'markorbit-data-engine_clickhouse_data',
    [string]$LegacyRawRoot = 'D:\yoomarks\markorbit-data-engine\raw_data',
    [string]$RawTargetRoot = 'F:\MarkOrbitData\raw',
    [string]$LegacyEHotRoot = 'E:\MarkOrbitData\hot\clickhouse',
    [string]$LegacyEHotLogsRoot = 'E:\MarkOrbitData\hot\clickhouse-logs',
    [string]$EvidenceRoot = 'reports',
    [switch]$AcknowledgeTemporary20Percent,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$phase1EReplacement = 'scripts/run-production-rebalance-phase1-e-reparse-safe-delete.ps1'
$phase2DReplacement = 'scripts/run-production-rebalance-phase2-d-resumable-delete.ps1'
$phase2DAuthorityPreparation = 'scripts/run-production-rebalance-phase2-d-resumable-apply.ps1'

Write-Host 'decision=LEGACY_STORAGE_REBALANCE_OPERATOR_RETIRED'
Write-Host "requested_phase=$Phase"
Write-Host 'mutation_performed=False'

if ($Phase -eq 'Phase1E') {
    Write-Host "replacement_operator=$phase1EReplacement"
    throw "Legacy Phase1E recursive-delete path is retired. Use $phase1EReplacement and its accepted preflight/journal contract."
}

Write-Host "replacement_operator=$phase2DReplacement"
Write-Host "authority_preparation_operator=$phase2DAuthorityPreparation"
throw "Legacy generic Phase2D entry point is retired. Phase2D remains available through the dedicated resumable operators: $phase2DAuthorityPreparation and $phase2DReplacement."
