$ErrorActionPreference = "Stop"
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/jobs/cn/run" |
    ConvertTo-Json -Depth 20
