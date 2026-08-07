$ErrorActionPreference = "Continue"

Write-Host "== Docker versions =="
docker version
docker compose version

Write-Host ""
Write-Host "== Docker Hub DNS =="
Resolve-DnsName registry-1.docker.io -ErrorAction Continue

Write-Host ""
Write-Host "== Docker Hub TCP 443 =="
Test-NetConnection registry-1.docker.io -Port 443

Write-Host ""
Write-Host "== Docker Hub HTTPS endpoint =="
curl.exe -I --connect-timeout 15 https://registry-1.docker.io/v2/

Write-Host ""
Write-Host "Expected HTTPS result when reachable: HTTP 401 Unauthorized."
