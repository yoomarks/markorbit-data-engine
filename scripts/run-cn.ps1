$ErrorActionPreference = "Stop"

# Real import is manually deterministic. The worker must not race this command.
$worker = docker compose ps --status running --services worker
if ($worker -match "worker") {
    throw "worker is running. Stop it first: docker compose stop worker"
}

# The API now protects CN ingestion with a PostgreSQL session advisory lock.
# If Docker/the API/the host stops mid-package, the next invocation reclaims
# orphaned PROCESSING work as INTERRUPTED and replays that older package before
# advancing to newer source ranks.
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/jobs/cn/run" |
    ConvertTo-Json -Depth 20
