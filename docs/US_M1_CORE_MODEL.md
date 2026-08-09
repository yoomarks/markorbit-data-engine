# MarkOrbit US M1.1 Core Model

Status: REAL USPTO TDXF OFFICIAL FACT LAYER + PACKAGE INGESTION + READ API

US M1.1 is grounded in real USPTO Trademark Applications TDXF rather than a synthetic XML layout. It supports both the historical coverage snapshot parts and continuing daily application updates while keeping official facts separate from later legal-status and maintenance interpretation.

## Source boundary

Two official-source package families are modeled:

- historical coverage parts such as `apc18840407-20251231-05.zip`;
- daily updates such as `apc260108.zip` / controlled extracted `apc260108.xml`.

Both package families carry the same TDXF case structure. Historical records can legitimately be sparse, especially very old registrations; absence of modern fields in an early historical case is not itself a quality failure.

Source acquisition credentials remain outside the parser/publisher contract. Official packages are first materialized locally, then registered by SHA-256 and processed by the engine.

## Durable identity and precedence

- `serial_number` is the canonical US case identity.
- `registration_number` is a case attribute and secondary lookup key.
- historical snapshot parts always have lower source precedence than daily updates, regardless of ingestion wall-clock order;
- historical parts use explicit coverage-range + part identity;
- daily precedence is derived from the package update date.

This prevents a late-loaded historical snapshot from overwriting a newer daily observation.

## Real TDXF field layout

US M1.1 freezes the actual USPTO structure observed in historical and daily packages:

- `registration-number` and `transaction-date` are direct children of `case-file`;
- `published-for-opposition-date` is the publication field;
- events are `case-file-event-statement` records and retain `description-text`;
- most boolean indicators use USPTO `T/F` encoding;
- owner nationality is a nested `nationality` block and is not confused with mailing-address country/state;
- Madrid data is carried in the sibling `international-registration` block.

Known historical/transform aliases remain accepted only for compatibility; real TDXF names are the canonical contract.

## Filing basis

Filed basis and current basis are different official facts and are persisted separately:

- Section 1(a): filed/current
- Section 1(b): filed/current
- Section 44(d): filed/current
- Section 44(e): filed/current
- Section 66(a): filed/current
- no-basis current indicator

Real daily data contains cases where filed and current values differ. The engine therefore never derives current basis from filed basis when an explicit current field is present. Fallback to filed basis occurs only when the current field is absent in legacy/synthetic input.

The original `use_1a`, `intent_to_use_1b`, `foreign_application_44d`, `foreign_registration_44e`, `madrid_66a`, and `no_basis` fields remain compatibility aliases for the current observation.

## Core tables

### `us_case_current`

Current official case observation, including filing/publication/registration dates, transaction date, raw USPTO status, mark metadata, filed/current basis flags, selected post-registration indicators, and Madrid international-registration facts.

### `us_owner_current`

Durable owner observations including party/entry type, legal-entity code/statement, nested nationality, mailing address, DBA/AKA text, and composed-of statement.

### `us_classification_current`

Primary/International/US classes, class status, and first-use evidence. Partial dates such as `YYYYMM00` remain in raw columns while typed `Date32` stays NULL; missing day/month values are never invented.

### `us_event_history`

USPTO event code/date/sequence/type plus official event description text. Events are evidence, not MarkOrbit legal conclusions.

### `us_statement_current`

USPTO typed statements such as goods/services, disclaimers, mark descriptions, translations, and other statement families.

## Status boundary

US M1.1 preserves official `status_code`, `status_date`, event evidence, and official filing/maintenance indicators. It does **not** turn them into MarkOrbit `ACTIVE/DEAD`, `REGISTERED/ABANDONED`, Section 8 compliance, renewal eligibility, or other legal conclusions in the fact layer.

US API responses continue to expose:

`status_semantics = OFFICIAL_RAW_NOT_LEGAL_INTERPRETATION`

Any later legal interpretation must be versioned and evidence-linked.

## Legacy refinery skill

The prior `us_base_refinery` / `us_daily_refinery` skill is treated as a field-coverage and business-rule reference, not as the ingestion implementation. Useful families identified there include correspondent/attorney, design search codes, prior registrations, foreign applications, Madrid request/events, owner mentions, and basis flags.

US M1.1 deliberately keeps the engine's streaming ZIP/XML reader because the legacy skill loaded whole XML members into memory. Additional skill-derived fact families will be added only after their real TDXF identities and daily reconciliation semantics are frozen.

The skill's status-code enrichment is not copied into official fact tables without an independently validated status dictionary and interpretation layer.

## Runtime acceptance gate

`app.us.validate_fixture` now validates the US M1.1 schema against live ClickHouse/PostgreSQL, including explicit filed/current basis fields, Section 8 official indicators, Madrid fields, event descriptions, and partial-date preservation. Fixture rows are package-isolated and synchronously cleaned after validation.

GitHub Actions runs the live PostgreSQL 16 + ClickHouse 24.8 fixture on every PR.

## Read API

- `GET /api/us/schema`
- `GET /api/us/summary`
- `GET /api/us/cases/{serial_number}`

Serial lookup remains exactly eight digits and returns the official-fact families currently modeled.

## Next implementation layers

1. run real historical-part and real daily-package acceptance profiles;
2. define child-row reconciliation/tombstones so a newer full case observation can retire stale owner/class/statement identities safely;
3. port correspondent/attorney, design, prior-registration, foreign-application, and Madrid-request fact families from the old refinery skill using real TDXF identities;
4. establish real-source coverage and performance baselines;
5. only then build versioned US legal-status and maintenance-deadline interpretation.
