param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [ValidateSet("HISTORICAL", "DAILY")]
    [string]$SourceKind,

    [string]$EffectiveDate
)

$arguments = @(
    "-m", "app.us_assignment.sample_audit",
    $SourcePath,
    "--source-kind", $SourceKind
)

if ($EffectiveDate) {
    $arguments += @("--effective-date", $EffectiveDate)
}

python @arguments
exit $LASTEXITCODE
