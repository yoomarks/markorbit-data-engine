$ErrorActionPreference = "Stop"
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/jobs/cn/scan" |
    ConvertTo-Json -Depth 10
