param([switch]$VerifySourceFiles)
$ErrorActionPreference = "Stop"
$args = @("run", "--rm", "--no-deps", "worker", "python", "-m", "app.us_assignment.audit_cli", "audit")
if ($VerifySourceFiles) { $args += "--verify-source-files" }
& docker compose @args
if ($LASTEXITCODE -ne 0) { throw "US assignment real-data acceptance audit failed." }
