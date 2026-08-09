param(
    [Parameter(Mandatory = $true)]
    [string]$RulesetFileName,
    [switch]$NoActivate
)
$ErrorActionPreference = "Stop"
if ([System.IO.Path]::GetFileName($RulesetFileName) -ne $RulesetFileName) {
    throw "RulesetFileName must be a file name under RAW_DATA_PATH/reference/us/interpretation."
}
$containerPath = "/data/raw/reference/us/interpretation/$RulesetFileName"
$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.import_status_ruleset",
    "--ruleset-file", $containerPath
)
if ($NoActivate) { $args += "--no-activate" }
& docker compose @args
if ($LASTEXITCODE -ne 0) { throw "US status interpretation ruleset import failed." }
