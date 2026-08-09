$ErrorActionPreference = "Stop"

docker compose run --rm --no-deps worker python -m app.us_ttab.retry_once
if ($LASTEXITCODE -ne 0) { throw "US TTAB retry failed." }
