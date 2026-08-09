param(
    [string]$AsOf = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$Madrid66a,
    [string]$PublicationDate = "",
    [string]$OfficeActionIssueDate = "",
    [switch]$OfficeActionFinal,
    [string]$OfficeActionNoticeDeadline = "",
    [string]$NoticeOfAllowanceDate = "",
    [int]$ItuExtensionsGranted = -1,
    [switch]$StatementOfUseFiled,
    [int]$OppositionExtensionDaysGranted = -1
)

$ErrorActionPreference = "Stop"
if ($ItuExtensionsGranted -lt -1 -or $ItuExtensionsGranted -gt 5) {
    throw "ItuExtensionsGranted must be -1 (unknown) or 0 through 5."
}
if ($OppositionExtensionDaysGranted -notin @(-1, 0, 30, 90, 150)) {
    throw "OppositionExtensionDaysGranted must be -1 (unknown), 0, 30, 90, or 150."
}

$args = @(
    "run", "--rm", "--no-deps", "worker",
    "python", "-m", "app.us.application_deadlines_cli",
    "--as-of", $AsOf
)
if ($Madrid66a) { $args += "--madrid-66a" }
if ($PublicationDate) { $args += @("--publication-date", $PublicationDate) }
if ($OfficeActionIssueDate) {
    $args += @("--office-action-issue-date", $OfficeActionIssueDate)
}
if ($OfficeActionFinal) { $args += "--office-action-final" }
if ($OfficeActionNoticeDeadline) {
    $args += @("--office-action-notice-deadline", $OfficeActionNoticeDeadline)
}
if ($NoticeOfAllowanceDate) {
    $args += @("--notice-of-allowance-date", $NoticeOfAllowanceDate)
}
if ($ItuExtensionsGranted -ge 0) {
    $args += @("--itu-extensions-granted", "$ItuExtensionsGranted")
}
if ($StatementOfUseFiled) { $args += "--statement-of-use-filed" }
if ($OppositionExtensionDaysGranted -ge 0) {
    $args += @(
        "--opposition-extension-days-granted",
        "$OppositionExtensionDaysGranted"
    )
}

& docker compose @args
if ($LASTEXITCODE -ne 0) {
    throw "US application deadline calculation failed."
}
