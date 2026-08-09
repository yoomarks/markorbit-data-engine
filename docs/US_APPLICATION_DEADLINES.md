# US Application-Stage Deadline Engine

Status: evidence-backed deadline metadata only; **not an application legal-status engine**.

Rule version: `US_APPLICATION_DEADLINES_2026_08_09_V1`.
Rule sources were re-verified on 2026-08-09 against current USPTO/TMEP/TTAB materials.

## 1. Office Actions

For standard pre-registration Office Actions:

- Section 1 and Section 44 applications: Office Actions issued on or after 2022-12-03 use a three-month response period. One paid three-month extension may be requested before the initial deadline.
- Section 66(a) Madrid applications: six-month response period; the standard three-month extension mechanism does not apply.
- Pre-2022-12-03 non-Madrid Office Actions are modeled as the legacy six-month regime.
- The Office Action notice itself remains authoritative. An explicitly supplied notice deadline overrides the standard nominal calculation.

The engine does not automatically calculate weekend/federal-holiday adjustments or filing-time-zone cutoffs. Those must be verified against the notice and current USPTO rules.

## 2. Section 1(b) Notice of Allowance

The engine models the statutory potential ladder after a Notice of Allowance:

- every six months, file a Statement of Use or the next extension request;
- no more than five extension requests;
- final potential Statement of Use deadline is 36 months after the Notice of Allowance;
- later extension requests require the applicable good-cause showing.

Crucially, elapsed time does **not** prove that an extension request was filed or granted. Unless `extensions_granted` is supplied from USPTO records or another verified source, the current six-month period remains `CURRENT_PERIOD_UNKNOWN`.

A reported Statement of Use filing also does not imply that the filing was timely or accepted.

## 3. Publication and opposition

The original opposition period is 30 days from publication. The engine exposes the currently modeled TTAB extension paths without assuming a grant:

- no extension: publication + 30 days;
- initial 30-day extension: publication + 60 days;
- total 90 days of extension: publication + 120 days;
- final additional 60 days after total 90: publication + 180 days, requiring applicant consent or extraordinary circumstances under the current TTAB structure.

An extended opposition deadline becomes operational only when the caller supplies the explicit total extension grant fact (`0`, `30`, `90`, or `150`).

## 4. Evidence boundary

The current fact model directly stores `publication_date`, so publication deadlines can be calculated from the ingested USPTO case fact.

The current fact model does **not** have dedicated `office_action_issue_date` or `notice_of_allowance_date` fields. Therefore:

- the API/CLI accept those dates as explicit evidence inputs;
- the engine does not guess those dates from an unknown event code;
- after a source-backed official event-code reference is populated, a separate reviewed mapping layer may safely select event dates automatically.

## 5. Read-only API

- `GET /api/us/application-deadlines/rules`
- `GET /api/us/application-deadlines/{serial_number}`

The single-case route automatically reads the case `publication_date` and filing-basis flags. Office Action and NOA dates remain explicit inputs until reviewed event evidence exists.

Example parameters:

```text
/api/us/application-deadlines/97123456
  ?as_of=2026-08-09
  &office_action_issue_date=2026-02-10
  &notice_of_allowance_date=2026-07-01
  &itu_extensions_granted=0
  &opposition_extension_days_granted=30
```

## 6. Standalone calculator

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\calc-us-application-deadlines.ps1 `
  -AsOf 2026-08-09 `
  -PublicationDate 2026-06-01 `
  -OfficeActionIssueDate 2026-02-10 `
  -NoticeOfAllowanceDate 2026-07-01 `
  -ItuExtensionsGranted 0
```

Madrid Office Action example:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\calc-us-application-deadlines.ps1 `
  -AsOf 2026-08-09 `
  -Madrid66a `
  -OfficeActionIssueDate 2026-02-28
```

## 7. Official evidence identifiers

Every calculated report carries the rule version, verification date, and current official evidence URLs, including:

- USPTO Response time period
- USPTO Response forms
- current TMEP §711
- USPTO Intent-to-use forms
- USPTO Statement of Use minimum filing requirements
- USPTO trademark process/publication materials
- TTAB ESTTA extension-of-time materials

Future rule changes should create a new rule version rather than silently changing the meaning of previously generated schedules.
