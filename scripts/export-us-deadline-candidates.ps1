param(
    [Parameter(Mandatory = $true)]
    [string]$OutputFileName,
    [string]$AsOf = (Get-Date -Format "yyyy-MM-dd"),
    [ValidateRange(0, 3650)]
    [int]$HorizonDays = 90,
    [ValidateRange(0, 365)]
    [int]$RecentPastDays = 30,
    [ValidateRange(1, 500)]
    [int]$BatchSize = 500,
    [int]$MaxCases = 0
)

$ErrorActionPreference = "Stop"
if ([System.IO.Path]::GetFileName($OutputFileName) -ne $OutputFileName) {
    throw "OutputFileName must be a file name under RAW_DATA_PATH/exports/us."
}
if ($MaxCases -lt 0) {
    throw "MaxCases must be 0 (all cases) or a positive integer."
}

$containerPath = "/data/raw/exports/us/$OutputFileName"
$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.deadline_portfolio_cli",
    "--output", $containerPath,
    "--as-of", $AsOf,
    "--horizon-days", "$HorizonDays",
    "--recent-past-days", "$RecentPastDays",
    "--batch-size", "$BatchSize"
)
if ($MaxCases -gt 0) {
    $args += @("--max-cases", "$MaxCases")
}

& docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US deadline candidate export failed."
}
