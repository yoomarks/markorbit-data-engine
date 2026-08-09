param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][ValidateSet("HISTORICAL", "DAILY")][string]$SourceKind,
    [string]$EffectiveDate = ""
)

$ErrorActionPreference = "Stop"

$argsList = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.sample_audit",
    $SourcePath,
    "--source-kind", $SourceKind
)
if ($EffectiveDate) {
    $argsList += @("--effective-date", $EffectiveDate)
}

& docker compose @argsList
if ($LASTEXITCODE -ne 0) {
    throw "US application sample audit failed."
}
