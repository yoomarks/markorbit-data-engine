$ErrorActionPreference = "Stop"
docker compose run --rm --no-deps worker python -m app.us.reference_acceptance
if ($LASTEXITCODE -ne 0) {
    throw "USPTO status/event reference acceptance audit failed."
}
