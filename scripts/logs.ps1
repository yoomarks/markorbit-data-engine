$ErrorActionPreference = "Stop"

docker compose logs -f --tail=200
if ($LASTEXITCODE -ne 0) {
    throw "docker compose logs failed with exit code $LASTEXITCODE."
}
