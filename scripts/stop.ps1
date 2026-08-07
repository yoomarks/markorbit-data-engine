$ErrorActionPreference = "Stop"

docker compose down
if ($LASTEXITCODE -ne 0) {
    throw "docker compose down failed with exit code $LASTEXITCODE."
}
