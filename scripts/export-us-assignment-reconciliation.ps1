param(
    [string]$AfterSerial = "",
    [ValidateRange(1,1000)][int]$Limit = 200
)
$ErrorActionPreference = "Stop"
$args = @("run", "--rm", "--no-deps", "worker", "python", "-m", "app.us_assignment.audit_cli", "reconciliation", "--limit", "$Limit")
if ($AfterSerial) { $args += @("--after-serial", $AfterSerial) }
& docker compose @args
if ($LASTEXITCODE -ne 0) { throw "US assignment reconciliation report failed." }
