# MarkOrbit US M1.3 Core Model

Status: REAL USPTO TDXF OFFICIAL FACT LAYER + HISTORICAL/DAILY RECONCILIATION

US M1.3 is grounded in the supplied real USPTO Trademark Applications TDXF historical and daily packages. It preserves official source facts, reconciles complete case snapshots, and keeps legal-status/maintenance interpretation outside the fact layer.

## Source boundary

Two official-source package families are modeled:

- historical coverage parts such as `apc18840407-20251231-05.zip`;
- daily updates such as `apc260108.zip` / controlled extracted XML.

Historical records can legitimately be sparse. Source acquisition remains outside the parser/publisher contract; locally materialized packages are registered and verified by SHA-256 before ingestion.

## Identity and precedence

- `serial_number` is the canonical US case identity.
- `registration_number` is an attribute and secondary lookup key.
- every historical source rank is below every daily source rank.
- historical part identity is coverage-range + part number; daily identity is update date.

A late-loaded historical snapshot therefore cannot overwrite a later daily observation.

## Real TDXF field families

M1.1 established the real case layout for registration/transaction dates, publication, events, owner nationality, filed/current basis flags, maintenance indicators, and inbound Madrid `international-registration` facts.

M1.3 adds six fact families that were first identified in the old refinery Skill and then verified against the supplied full daily TDXF package:

- `correspondent` plus explicit header `attorney-name`, `attorney-docket-number`, and `domestic-representative-name`;
- `design-searches / design-search / code`;
- `prior-registration-applications / prior-registration-application`;
- `foreign-applications / foreign-application`;
- `madrid-international-filing-requests / madrid-international-filing-record`;
- nested `madrid-history-events / madrid-history-event`.

The inspected daily package contained approximately 36,422 correspondent blocks, 29,867 attorney-name fields, 26,762 design-search rows, 5,575 prior-registration records, 2,801 foreign applications, 953 Madrid filing records, and 5,961 Madrid history events.

These counts are source-profile evidence, not schema assumptions.

## Madrid separation

Two different Madrid fact chains must not be merged:

1. inbound US Section 66(a) case facts use the case-level sibling `international-registration` block and remain on `us_case_current`;
2. `madrid-international-filing-record` represents the separate Madrid international filing-request process and is persisted in `us_madrid_filing_current` with its own history in `us_madrid_event_history`.

A filing-request international-registration number therefore does not populate the inbound 66(a) case-level international-registration fields.

## Durable tables

Existing core:

- `us_case_current`
- `us_owner_current`
- `us_classification_current`
- `us_statement_current`
- `us_event_history`

M1.3 official fact families:

- `us_correspondent_current`
- `us_design_search_current`
- `us_prior_registration_current`
- `us_foreign_application_current`
- `us_madrid_filing_current`
- `us_madrid_event_history`

The five new `*_current` families are replaceable snapshot facts. `us_madrid_event_history`, like `us_event_history`, is cumulative event evidence.

## Snapshot reconciliation

For every newer source-ranked case snapshot, the engine compares current child identities with the identities present in the new observation. Older active identities omitted from a complete snapshot receive deterministic tombstones.

Replaceable families now include:

- owner
- classification
- statement
- correspondent
- design search
- prior registration
- foreign application
- Madrid filing request

General events and Madrid history events are excluded from tombstoning and remain cumulative evidence.

The lookup only considers rows with `source_rank < new_source_rank`, so a lower-ranked historical source cannot retire a newer daily fact. Tombstones carry the current package UUID, allowing full-package failure cleanup to reveal the previous valid snapshot before replay.

## Official-fact boundary

M1.3 does not create any of the following from the newly captured fields:

- `has_attorney`
- `is_pro_se`
- inferred correspondent/attorney roles
- a deduplicated cross-case attorney entity
- `ACTIVE/DEAD`, `REGISTERED/ABANDONED`, Section 8 compliance, renewal eligibility, or other legal conclusions

The old refinery Skill remains a discovery and field-coverage reference only. A field enters the durable engine only after its real TDXF path and semantics are verified.

US API/status consumers must continue to treat the data as:

`OFFICIAL_RAW_NOT_LEGAL_INTERPRETATION`

## Runtime acceptance gates

Three live PostgreSQL/ClickHouse fixtures run in CI:

- `app.us.validate_fixture` — M1.1 real-TDXF field regression;
- `app.us.validate_snapshot_fixture` — M1.2 historical→daily current-child replacement;
- `app.us.validate_official_fact_fixture` — M1.3 correspondent/design/prior/foreign/Madrid filing/history publication and cleanup.

All fixtures use isolated package UUIDs and synchronously remove their rows after validation.

## Next implementation layers

1. expose the new official fact families through the US read API without adding interpretation;
2. run larger real historical→daily acceptance profiles and measure row/tombstone rates;
3. establish full historical replay performance and coverage baselines;
4. add remaining real-source fact families only after direct structural validation;
5. only then build versioned US legal-status and maintenance-deadline interpretation.
