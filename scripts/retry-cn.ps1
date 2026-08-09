$ErrorActionPreference = "Stop"

$worker = docker compose ps --status running --services worker
if ($worker -match "worker") {
    throw "worker is running. Stop it first: docker compose stop worker"
}

# Fast gates: schema/SQL compile first, then a non-empty two-package runtime fixture.
docker compose exec -T api python -m app.cn.validate_contract
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 CN contract preflight failed; real CN package retry was not started."
}

docker compose exec -T api python -m app.cn.validate_fixture
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 CN runtime fixture failed; real CN package retry was not started."
}

Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/jobs/cn/retry" |
    ConvertTo-Json -Depth 20
