param(
    [string]$OutputDirectory = "reports",
    [switch]$Compact
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (-not $OutputDirectory.Trim()) {
        throw "OutputDirectory must not be empty."
    }

    $resolvedOutputDirectory = $OutputDirectory
    if (-not [System.IO.Path]::IsPathRooted($resolvedOutputDirectory)) {
        $resolvedOutputDirectory = Join-Path $repoRoot $resolvedOutputDirectory
    }
    New-Item -ItemType Directory -Force -Path $resolvedOutputDirectory | Out-Null
    $resolvedOutputDirectory = (Resolve-Path -LiteralPath $resolvedOutputDirectory).Path

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $servingStatePath = Join-Path `
        $resolvedOutputDirectory `
        "cn_m16_lightweight_serving_checkpoint_$timestamp.json"
    $promotionPath = Join-Path `
        $resolvedOutputDirectory `
        "platformization_m17_promotion_$timestamp.json"

    $servingScript = Join-Path $PSScriptRoot "check-cn-serving-state.ps1"
    $promotionScript = Join-Path $PSScriptRoot "check-platformization-m17-promotion.ps1"

    $servingArgs = @{
        ExpectedFileName = "2023_5.zip"
        OutputPath = $servingStatePath
    }
    if ($Compact) {
        $servingArgs["Compact"] = $true
    }

    Write-Host "Step 1/2: capture current lightweight CN serving-state evidence."
    & $servingScript @servingArgs

    if (-not (Test-Path -LiteralPath $servingStatePath -PathType Leaf)) {
        throw "CN serving-state child gate completed without persisting evidence: $servingStatePath"
    }
    $servingState = Get-Content -LiteralPath $servingStatePath -Raw | ConvertFrom-Json
    if ($servingState.status -notin @("PASS", "WARN")) {
        throw "CN serving-state evidence is not promotable: status=$($servingState.status)"
    }
    if ($servingState.read_only -ne $true) {
        throw "CN serving-state evidence is not marked read-only."
    }
    if ($servingState.evidence_mode -ne "LIGHTWEIGHT_SERVING_CHECKPOINT") {
        throw "CN serving-state evidence mode is not LIGHTWEIGHT_SERVING_CHECKPOINT."
    }

    $promotionArgs = @{
        CnServingCheckpointPath = $servingStatePath
        OutputPath = $promotionPath
    }
    if ($Compact) {
        $promotionArgs["Compact"] = $true
    }

    Write-Host "Step 2/2: evaluate M1.7 promotion from persisted evidence."
    & $promotionScript @promotionArgs

    if (-not (Test-Path -LiteralPath $promotionPath -PathType Leaf)) {
        throw "M1.7 promotion child gate completed without persisting evidence: $promotionPath"
    }
    $promotion = Get-Content -LiteralPath $promotionPath -Raw | ConvertFrom-Json
    if ($promotion.release_promotion_allowed -ne $true) {
        $reasonCodes = @($promotion.reasons | ForEach-Object { $_.code })
        throw (
            "M1.7 target promotion evidence is not ready: " +
            "status=$($promotion.status); reasons=$($reasonCodes -join ', ')"
        )
    }

    Write-Host "M1.7 target promotion evidence: READY"
    Write-Host "CN serving-state evidence: $servingStatePath"
    Write-Host "M1.7 promotion evidence: $promotionPath"
    Write-Host "No package replay/rescan or full-corpus validation was executed by this operator."
    Write-Host "No release version is changed by this operator."
}
finally {
    Pop-Location
}
