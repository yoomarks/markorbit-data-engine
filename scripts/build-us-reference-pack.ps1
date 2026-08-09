param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("status", "event")]
    [string]$Family,

    [Parameter(Mandatory = $true)]
    [string]$SourceDocumentName,

    [Parameter(Mandatory = $true)]
    [string]$ReviewedCsvName,

    [Parameter(Mandatory = $true)]
    [string]$ReferenceVersion,

    [Parameter(Mandatory = $true)]
    [string]$DocumentDate,

    [Parameter(Mandatory = $true)]
    [string]$SourceUrl,

    [string]$EvidenceNote = ""
)

$ErrorActionPreference = "Stop"
foreach ($name in @($SourceDocumentName, $ReviewedCsvName)) {
    if ([System.IO.Path]::GetFileName($name) -ne $name) {
        throw "SourceDocumentName and ReviewedCsvName must be file names, not paths."
    }
}

$base = "/data/raw/reference/us/$Family"
$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.build_reference_pack",
    "--family", $Family,
    "--source-document", "$base/$SourceDocumentName",
    "--reviewed-csv", "$base/$ReviewedCsvName",
    "--reference-version", $ReferenceVersion,
    "--document-date", $DocumentDate,
    "--source-url", $SourceUrl
)
if ($EvidenceNote) {
    $args += @("--evidence-note", $EvidenceNote)
}

& docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US official reference pack build failed."
}
