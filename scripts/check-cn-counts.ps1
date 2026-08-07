$ErrorActionPreference = "Stop"

$summary = Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/cn/summary"
$summary | ConvertTo-Json -Depth 20
