param(
    [Parameter(Mandatory = $true)]
    [string]$ReviewPath,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$review = Resolve-Path $ReviewPath
$reviewFile = Get-Item $review
if (-not $OutputPath) {
    $OutputPath = Join-Path $reviewFile.DirectoryName ($reviewFile.BaseName + "_score.json")
}

$outputFull = [System.IO.Path]::GetFullPath($OutputPath)
$outputDir = Split-Path -Parent $outputFull
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$inputMount = "$($reviewFile.DirectoryName):/review/in:ro"
$outputMount = "${outputDir}:/review/out"
$containerInput = "/review/in/$($reviewFile.Name)"
$containerOutput = "/review/out/$([System.IO.Path]::GetFileName($outputFull))"

Write-Host "Scoring CN case-status ground-truth review packet..."
& docker compose run --rm --no-deps `
    -v $inputMount `
    -v $outputMount `
    worker python -m app.cn.case_status_ground_truth `
    score $containerInput $containerOutput
if ($LASTEXITCODE -ne 0) {
    throw "Ground-truth review packet scoring failed."
}

Write-Host "Ground-truth score: $outputFull"
Write-Host "No score automatically promotes an EMPIRICAL rule."
