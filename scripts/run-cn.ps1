$ErrorActionPreference = "Stop"

# Real import is manually deterministic. The worker must not race this command.
$worker = docker compose ps --status running --services worker
if ($worker -match "worker") {
    throw "worker is running. Stop it first: docker compose stop worker"
}

Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/jobs/cn/run" |
    ConvertTo-Json -Depth 20
