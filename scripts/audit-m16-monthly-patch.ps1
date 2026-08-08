param(
    [Parameter(Mandatory = $true)]
    [string]$FileName
)

$ErrorActionPreference = "Stop"

Write-Host "Running M1.6 monthly patch acceptance audit for $FileName..." -ForegroundColor Cyan

docker compose run --rm --no-deps worker python -m app.cn.audit_monthly_patch $FileName
if ($LASTEXITCODE -ne 0) {
    throw "M1.6 monthly patch acceptance audit failed to execute"
}

Write-Host "Monthly patch audit complete. Persistent worker remains stopped." -ForegroundColor Green
