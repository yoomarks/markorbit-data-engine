param(
    [Parameter(Mandatory = $true)]
    [string]$RegistrationDate,
    [string]$AsOf = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$Madrid66a,
    [string]$InternationalRegistrationDate = "",
    [string]$CurrentTermExpirationDate = ""
)

$ErrorActionPreference = "Stop"
$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.maintenance_cli",
    "--registration-date", $RegistrationDate,
    "--as-of", $AsOf
)
if ($Madrid66a) { $args += "--madrid-66a" }
if ($InternationalRegistrationDate) {
    $args += @("--international-registration-date", $InternationalRegistrationDate)
}
if ($CurrentTermExpirationDate) {
    $args += @("--current-term-expiration-date", $CurrentTermExpirationDate)
}

& docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US maintenance deadline calculation failed."
}
