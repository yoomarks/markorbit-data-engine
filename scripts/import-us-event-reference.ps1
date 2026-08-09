param(
    [Parameter(Mandatory = $true)]
    [string]$ReferenceFileName,
    [switch]$NoActivate
)
$ErrorActionPreference = "Stop"
if ([System.IO.Path]::GetFileName($ReferenceFileName) -ne $ReferenceFileName) {
    throw "ReferenceFileName must be a file name under RAW_DATA_PATH/reference/us/event."
}
$containerPath = "/data/raw/reference/us/event/$ReferenceFileName"
$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.import_event_reference",
    "--reference-file", $containerPath
)
if ($NoActivate) { $args += "--no-activate" }
& docker compose @args
if ($LASTEXITCODE -ne 0) { throw "USPTO event reference import failed." }
