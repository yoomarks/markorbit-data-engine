$ErrorActionPreference = "Stop"

$api = docker compose ps --status running -q api
if ($LASTEXITCODE -ne 0 -or -not $api) {
    throw "api must be running before applying the contact source catalog patch."
}

Write-Host "Applying reviewed contact source metadata and missing-country fallback..."
docker compose exec -T api python -m app.contact_ingest.source_catalog_patch
if ($LASTEXITCODE -ne 0) {
    throw "Contact source catalog patch failed with exit code $LASTEXITCODE."
}
