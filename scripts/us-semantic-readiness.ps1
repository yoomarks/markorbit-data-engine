param(
    [Parameter(Mandatory = $true)]
    [int]$ExpectedHistoryParts,
    [switch]$DeepSourceTest
)
$ErrorActionPreference = "Stop"
$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.semantic_readiness",
    "--expected-history-parts", "$ExpectedHistoryParts"
)
if ($DeepSourceTest) { $args += "--deep-source-test" }
& docker compose @args
if ($LASTEXITCODE -ne 0) { throw "US semantic readiness check failed." }
