# US TTAB M1.1 — Verified TTABVUE Raw XML Contract

## Status

`US_TTAB_M1.1` upgrades the isolated TTAB procedural-fact subsystem from a field-model/synthetic-layout contract to a **live-verified USPTO TTABVUE `rawxml=1` contract**.

On 2026-08-09, MarkOrbit's parser was exercised in GitHub Actions against public TTABVUE raw XML for all four supported proceeding shapes:

- `OPP` — Opposition
- `CAN` — Cancellation
- `EXA` — Ex Parte Appeal
- `EXT` — Extension of Time

The live source probe passed for all four shapes. The temporary network probe was then removed from normal CI; permanent regression is network-independent through sanitized real-layout fixtures.

Semantic marker remains:

`USPTO_TTAB_PROCEDURAL_FACTS_NOT_OUTCOME_OR_SUBSTANTIVE_RIGHTS_CONCLUSION`

## Verified response contract

Observed public response content type:

`text/xml; charset=ISO-8859-1`

Root:

`uspto-ttabvue-document`

Proceeding:

`uspto-ttabvue-document/proceeding`

### Proceeding attributes

Metadata is under:

`proceeding/proceeding-attributes`

Verified fields include:

- `proceeding-number`
- `proceeding-type`
  - element text = raw code such as `OPP`, `CAN`, `EXA`, `EXT`
  - XML `name` attribute = display name
- `filing-date` when present
- `proceeding-status` when present
  - element text = raw status code
  - XML `name` attribute = display text
- `proceeding-status-date` when present
- `general-contact-number`
- `interlocutory-attorney`
- `paralegal-name`

Board staff names may be composed from nested `first-name` and `last-name`.

M1.1 deliberately stores **raw code and display text separately**.

### Parties

Parties are under:

`proceeding/proceeding-parties`

Verified side containers:

- `plaintiffs`
- `defendants`

The public Ex Parte sample is represented on the plaintiff side. Extension records may contain only a requesting party.

Verified `party` attributes include:

- `party-id`
- `role`
- `name`
- `company` when present
- `organization` when present
- `granted-to-date` when present

Nested `party-info-from-db`, `correspondence-info-from-db`, `contact-address`, and `country` data are parsed as source facts when present.

### Trademark properties

Properties are under:

`party/ttab-properties/ttab-property`

Verified fields include:

- `serial-number`
- `registration-number` when present
- `property-filing`
- `property-filing-cd`
- `mark-explanation`
- `common-law-ind`
- nested `tm-com-status/trademark-gid`
- nested `tm-com-status/application-status`
  - raw code in element text
  - display value in XML `name`

`mark-explanation` is stored separately from `mark_text`; MarkOrbit does not relabel it as literal mark text.

### Prosecution history / docket

Docket data is under:

`proceeding/prosecution-history/prosecution-history-event`

The material fields are XML attributes rather than child elements. Verified attributes include:

- `identifier`
- `object-id`
- `entry-code`
- `entry-date`
- `event-text`
- `due-date` when present
- `confidential` when present

A TTABVUE `due-date` remains a **source due-date observation** only. M1.1 does not infer that it is still operative, was extended, suspended, satisfied, or legally controlling.

## Supported shape differences

The real-source verification intentionally covers structural differences:

- Opposition: parties + multiple properties + prosecution history
- Cancellation: parties + multiple properties + prosecution history
- Ex Parte Appeal: one principal party + one property + prosecution history
- Extension of Time: may omit filing/status, properties, and prosecution history

Missing optional structures are preserved as missing; they are not treated as parser corruption.

## Schema changes

M1.1 is an additive upgrade over `010_us_ttab_m10.sql` using:

`database/clickhouse/init/011_us_ttab_m11_real_rawxml.sql`

New facts include:

Proceeding:
- `proceeding_type_code`
- `status_code`

Party:
- `party_id`
- `role`
- `company`
- `organization`
- `granted_to_date_raw`
- `correspondent_organization`

Property:
- `mark_explanation`
- `property_filing`
- `property_filing_code`
- `common_law_indicator`
- `application_status_code`
- `trademark_gid`

Docket:
- `identifier`
- `object_id`
- `entry_code`
- `confidential`

## Manual public-source capture

Normal CI does not call TTABVUE. To materialize a new authoritative public snapshot deliberately:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\capture-us-ttabvue.ps1 `
  -ProceedingNumber 92090576 `
  -ProceedingType CAN `
  -SnapshotAt "2026-08-09T20:00:00+08:00"
```

The capture utility:

1. requests public TTABVUE `rawxml=1`
2. preserves the response bytes without decode/re-encode
3. parses exactly one proceeding
4. verifies requested proceeding number/type
5. writes the raw XML into `raw_data/incoming/us_ttab`
6. calculates SHA-256
7. registers the package with the explicit source snapshot timestamp

It does not automatically ingest the package. Use the normal TTAB ingestion runner afterward.

## Offline regression fixtures

Sanitized fixtures preserve verified production tag/attribute layout without reproducing public party/correspondence details:

- `tests/fixtures/us_ttab_real_opposition.xml`
- `tests/fixtures/us_ttab_real_cancellation.xml`
- `tests/fixtures/us_ttab_real_exparte.xml`
- `tests/fixtures/us_ttab_real_extension.xml`

These fixtures make standard CI deterministic and independent of TTABVUE uptime or network access.

## Safety boundary

M1.1 still does not determine:

- whether a TTAB due date remains legally operative
- whether a party won or lost
- whether a registration/application is legally valid because of a TTAB event
- trademark infringement
- ownership of substantive trademark rights

The subsystem records and compares official procedural-source observations only.
