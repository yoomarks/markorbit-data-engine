param(
    [Parameter(Mandatory = $true)]
    [string]$AuditPath,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$audit = Resolve-Path $AuditPath
$auditFile = Get-Item $audit
if (-not $OutputPath) {
    $OutputPath = Join-Path $auditFile.DirectoryName ($auditFile.BaseName + "_review.csv")
}

$outputFull = [System.IO.Path]::GetFullPath($OutputPath)
$outputDir = Split-Path -Parent $outputFull
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$inputMount = "$($auditFile.DirectoryName):/review/in:ro"
$outputMount = "$outputDir:/review/out"
$containerInput = "/review/in/$($auditFile.Name)"
$containerOutput = "/review/out/$([System.IO.Path]::GetFileName($outputFull))"

Write-Host "Building CN case-status ground-truth review packet..."
& docker compose run --rm --no-deps `
    -v $inputMount `
    -v $outputMount `
    worker python -m app.cn.case_status_ground_truth `
    build $containerInput $containerOutput
if ($LASTEXITCODE -ne 0) {
    throw "Ground-truth review packet build failed."
}

Write-Host "Review packet: $outputFull"
Write-Host "Fill review_label and official_source_ref before scoring decisive labels."
