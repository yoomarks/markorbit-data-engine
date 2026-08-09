param([switch]$VerifySourceFiles)
$ErrorActionPreference = "Stop"
$argsList = @("-m", "app.us_ttab.audit_cli", "readiness")
if ($VerifySourceFiles) { $argsList += "--verify-source-files" }
docker compose run --rm --no-deps worker python @argsList
if ($LASTEXITCODE -ne 0) { throw "US TTAB readiness check failed." }
