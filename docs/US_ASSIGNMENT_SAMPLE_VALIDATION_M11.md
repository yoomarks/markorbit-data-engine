# US Assignment Sample Validation M1.1

## Scope

This milestone returns the US Assignment work to the Data Engine ingestion sequence:

`official XML/ZIP -> inspect -> parse -> normalize -> sample ingest -> replay -> acceptance`

It does **not** authorize full-corpus ingestion and does not add applicant piercing,
lifecycle management, risk monitoring, demand mining, Case 360, or alert features.

## Official source boundary

USPTO publishes Trademark Assignment XML as a data product separate from Trademark
Application XML and TTAB XML. The Assignment dataset has historical/backfile and daily
updates. Source acquisition must be treated separately from parser correctness because
the USPTO Open Data Portal access path can change over time.

Do not infer source kind, effective date, completeness, or precedence from a filename.
Those values remain explicit control-plane facts.

## What M1.1 adds

`app.us_assignment.sample_audit` is a pre-ingest, read-only sample profiler for an XML
file or ZIP containing XML members. It records:

- source SHA-256 and byte size;
- XML member names;
- assignment / assignor / assignee / property counts;
- parsed-field coverage by fact family;
- invalid date and malformed property-serial observations;
- sample reel/frame examples;
- explicit source kind and optional explicit effective date;
- fail-closed status when the sample cannot be parsed or contains no assignment records.

Every report freezes `scale_up_authorized=false`. A passing sample only means the sample
can proceed to the next sample-ingest step.

## Historical real-shape regression fixture

`tests/fixtures/us_assignment_real_historical_shape.xml` preserves the legacy tag shape
observed in the USPTO historical Assignment XML package
`asb19550103-20211231-01.zip`, including:

- `person-or-organization-name`;
- `serial-no`;
- `registration-no`;
- early recorded dates;
- reel/frame identity.

The repository fixture is deliberately a minimized excerpt/reconstruction. It is not the
source ZIP and no official package SHA is claimed for it. Therefore it validates parser
shape compatibility but is **not** a substitute for running a genuine downloaded package.

## Run against genuine samples

Historical sample:

```powershell
.\scripts\audit-us-assignment-sample.ps1 `
  -SourcePath "D:\data\assignment\<historical-package>.zip" `
  -SourceKind HISTORICAL `
  -EffectiveDate "YYYY-MM-DD"
```

Daily sample:

```powershell
.\scripts\audit-us-assignment-sample.ps1 `
  -SourcePath "D:\data\assignment\<daily-package>.zip" `
  -SourceKind DAILY `
  -EffectiveDate "YYYY-MM-DD"
```

The effective date is optional for profiling, but when supplied it is always explicit and
never parsed from the filename.

## Sample acceptance gate

Before any Assignment scale-up plan is approved, retain at least one genuine historical
package and one genuine daily package and verify both through the following sequence:

1. package SHA and archive readability;
2. sample audit PASS or investigated PASS_WITH_WARNINGS;
3. parser field coverage review against raw XML;
4. sample database ingest;
5. historical -> daily overlap/update behavior;
6. retry/replay behavior;
7. Assignment real-data acceptance;
8. spot-check selected reel/frame and trademark properties against USPTO records.

Only after those gates pass should storage sizing, download cadence, concurrency, full
historical replay, and daily automation be designed.

## Legal boundary

Assignment XML is stored as USPTO recorded-assignment evidence. Recordation alone is not
converted into a legal-title or current-ownership conclusion.
