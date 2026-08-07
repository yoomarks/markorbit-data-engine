param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationNumber
)

$ErrorActionPreference = "Stop"
$encoded = [Uri]::EscapeDataString($ApplicationNumber.Trim().ToUpperInvariant())
Invoke-RestMethod -Method Get -Uri "http://localhost:8080/api/cn/cases/$encoded" |
    ConvertTo-Json -Depth 50
