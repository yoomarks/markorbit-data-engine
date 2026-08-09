# US Maintenance Engine and Official Reference Pack Workflow

Status: evidence-backed deadline calculator; **not a registration legal-status engine**.

Rule version: `US_MAINTENANCE_2026_08_09_V1`.

## 1. Maintenance calculation boundary

The calculator derives nominal filing windows from dates and filing-basis facts. It does not conclude that a registration is active, cancelled, expired, incontestable, valid, or enforceable.

Current modeled rules are grounded in current USPTO/TMEP materials:

- modern non-Madrid registrations: first Section 8 filing between the fifth and sixth anniversaries; combined Sections 8 and 9 between the ninth and tenth anniversaries and each successive ten-year period;
- registered extensions under Madrid: Section 71 on the corresponding 5–6 and 9–10 / successive ten-year schedule; Section 9 is not used for the U.S. extension;
- Madrid records receive a separate informational WIPO/International Bureau ten-year renewal reminder when an international registration date is available;
- Section 15 remains optional and never becomes `eligible` from registration age alone;
- registrations before 1989-11-16 default to `LEGACY_TERM_REQUIRES_RENEWAL_HISTORY`, because older 20-year terms and transition renewals cannot safely be reconstructed from registration date alone.

The calculator exposes **nominal** statutory anniversary dates. It does not shift deadlines for Saturdays, Sundays, or U.S. federal holidays. The USPTO business-day rule must be checked before filing.

## 2. Section 15 safety rule

For Section 15 the engine only returns an earliest possible date and `REQUIRES_EXTERNAL_FACTS`. At minimum, the legal determination requires facts outside a registration anniversary calculation, including Principal Register status, continuous use, and the absence of disqualifying decisions or pending proceedings.

## 3. Standalone calculation

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\calc-us-maintenance.ps1 `
  -RegistrationDate 2020-07-28 `
  -AsOf 2026-08-09
```

Madrid example:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\calc-us-maintenance.ps1 `
  -RegistrationDate 2017-06-15 `
  -AsOf 2026-08-09 `
  -Madrid66a `
  -InternationalRegistrationDate 2016-03-01
```

For a legacy registration, an operator may supply a verified current term expiration date. The engine will calculate only that explicitly supplied renewal window rather than reconstructing unknown renewal history.

## 4. Read-only API

The semantic router adds read-only endpoints without changing the existing `/api/us/cases/{serial_number}` contract:

- `GET /api/us/references/status`
- `GET /api/us/references/events`
- `GET /api/us/references/acceptance`
- `GET /api/us/semantic-readiness?expected_history_parts=N`
- `GET /api/us/interpretation/ruleset`
- `GET /api/us/status-interpretation/{serial_number}`
- `GET /api/us/maintenance/{serial_number}`

The interpretation endpoint keeps three layers separate: raw USPTO facts, official-reference text, and MarkOrbit-derived interpretation. If the evidence-bound interpretation framework cannot support a result, it returns `UNKNOWN`.

## 5. Building official reference evidence packs

Current USPTO source documents may be binary `.doc` files. The engine deliberately does **not** auto-extract legal meanings from those bytes. An operator reviews/transcribes the official table into CSV, and the pack builder binds that reviewed transcription to the original source bytes.

Required CSV columns:

```text
code,official_description
```

Optional columns:

```text
official_definition,official_category,source_locator
```

Place the original source document and reviewed CSV under the matching family directory:

```text
RAW_DATA_PATH/reference/us/status/
RAW_DATA_PATH/reference/us/event/
```

Then run, for example:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\build-us-reference-pack.ps1 `
  -Family status `
  -SourceDocumentName Table1TrademarkStatusCodes_20250813.doc `
  -ReviewedCsvName status_codes_reviewed.csv `
  -ReferenceVersion USPTO_STATUS_CODES_20250813 `
  -DocumentDate 2025-08-13 `
  -SourceUrl https://www.uspto.gov/trademarks/trademark-updates-and-announcements/xml-resources
```

The tool writes the normalized JSON **beside the source document** plus a deterministic manifest containing:

- source-document SHA-256;
- reviewed-transcription CSV SHA-256;
- normalized-payload SHA-256;
- reference version and record count.

The existing production import CLIs then independently re-hash the original source document again before importing the reference version.

## 6. Evidence sources encoded by the calculator

- USPTO: Keeping your registration alive
- USPTO: Post-registration timeline for all registrations except Madrid Protocol
- USPTO: Post-registration timeline for Section 66(a) / Madrid registrations
- current TMEP Chapter 1600 term rules

These source identifiers and URLs are returned with every calculated schedule so later rule revisions remain auditable.
