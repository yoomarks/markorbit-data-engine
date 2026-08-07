$ErrorActionPreference = "Stop"

docker compose up -d worker
if ($LASTEXITCODE -ne 0) { throw "worker start failed" }

docker compose ps worker
