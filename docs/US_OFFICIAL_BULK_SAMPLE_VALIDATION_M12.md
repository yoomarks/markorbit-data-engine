# US Official Bulk Sample Validation M1.2

## Scope

This milestone validates the Data Engine ingestion layer against user-supplied official USPTO Trademark Assignment and TTAB bulk XML samples. It does not add Case 360, alerting, lifecycle inference, ownership conclusions, TTAB outcome conclusions, or other upper-layer product behavior.

## Assignment samples observed

### Historical opening sample

Source file supplied for validation: `asb19520326-20260409-01.xml` (a bounded opening sample derived from the single >400 MB historical package).

Observed by a full streaming scan of the supplied sample:

- XML is well formed and closes the `trademark-assignments` root.
- `version-no=1.0`
- `version-date=20130910`
- `action-key-code=DA`
- `transaction-date=20260513`
- 19,833 `assignment-entry` records.
- Official tags present in real data include `country-name`, `date-acknowledged`, `dba-aka-ta-statement`, and structured/mixed `composed-of-statement` content.

### Daily sample

Source: `asb260411.zip`.

Observed by a full streaming scan:

- one XML member, `asb260411.xml`
- `version-no=1.0`
- `version-date=20130910`
- `action-key-code=DA`
- `transaction-date=20260413`
- 819 `assignment-entry` records
- 880 assignors
- 855 assignees
- 3,888 properties

The XML transaction date differs from the date-like token in the ZIP filename. The engine therefore must not infer an effective date from the filename.

## Assignment parser corrections

Real data proved that the prior parser omitted official aliases:

- `date-acknowledged`
- `country-name`
- `dba-aka-ta-statement`

`composed-of-statement` is now flattened losslessly from mixed XML text rather than treated as a simple scalar child text node. These fields remain source facts only.

## TTAB samples observed

### Daily `tt260101.zip`

- `version-no=v1.0`
- `version-date=20211227`
- `action-key-code=DA`
- `transaction-date=20260101`
- 71 proceedings

### Daily `tt260808.zip`

- `version-no=v1.0`
- `version-date=20211227`
- `action-key-code=DA`
- `transaction-date=20260808`
- 56 proceedings

### Historical `tt19511002-20251231-1.zip`

- XML member expands to about 904 MB.
- Official bulk shape is `ttab-proceedings / proceeding-information / proceeding-entry`, not the TTABVUE `rawxml` layout supported by M1.1.
- A streaming scan of the historical source found a real nested `tma-proceeding` record in proceeding `97658985`, property identifier `1613341`, with raw TMA proceeding number `2024-101552` and raw type code `R`.

Because the historical XML is roughly 904 MB uncompressed, `ET.parse()` whole-tree loading is not acceptable. M1.2 uses `ET.iterparse()` record streaming.

## TTAB M1.2 official fields

M1.2 adds storage for bulk-only source facts that were observed in official samples:

- proceeding: employee number, location code, day-in-location, charge-to location code/name
- party correspondence: source address identifier and address type code
- property: source property identifier and nested TMA proceeding number/type code
- docket: raw entry type code in addition to raw entry code

Bulk proceeding type, status, party role, docket code/type, and TMA type remain raw codes. This milestone deliberately does not assign legal meanings to those codes without an evidence-bound official reference.

## Compatibility

TTABVUE M1.1 rawxml parsing remains supported and regressed. M1.2 adds the official bulk layout; it does not replace the prior source contract.

## Safety / scale-up gate

- Assignment recordation is recorded-interest evidence, not a legal ownership conclusion.
- TTAB records are procedural facts, not outcome or substantive-rights conclusions.
- Filename tokens are not authoritative source dates.
- Unknown codes are preserved, not guessed.
- Large historical XML is streamed.
- These sample validations do not by themselves authorize unattended full-corpus scale-up; full historical package ingestion and acceptance/reconciliation remain separate gates.
