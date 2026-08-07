$ErrorActionPreference = "Stop"
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/jobs/cn/retry" |
    ConvertTo-Json -Depth 20
