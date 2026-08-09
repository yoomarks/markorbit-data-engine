$ErrorActionPreference = "Stop"

docker compose run --rm --no-deps worker python -m app.us_ttab.run_once
if ($LASTEXITCODE -ne 0) { throw "US TTAB one-shot ingestion failed." }
