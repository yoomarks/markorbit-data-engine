# MarkOrbit US M1.2 Core Model

Status: REAL USPTO TDXF OFFICIAL FACT LAYER + HISTORICAL/DAILY RECONCILIATION + READ API

US M1.2 is grounded in real USPTO Trademark Applications TDXF. It supports historical coverage snapshot parts and continuing daily application updates while keeping official facts separate from later legal-status and maintenance interpretation.

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

The real-source contract preserves:

- direct `registration-number` and `transaction-date` case fields;
- `published-for-opposition-date`;
- `case-file-event-statement` records including `description-text`;
- USPTO `T/F` indicators;
- nested owner nationality distinct from mailing address;
- sibling `international-registration` Madrid facts;
- filed and current Section 1(a), 1(b), 44(d), 44(e), and 66(a) basis flags as separate official facts.

Partial first-use dates such as `YYYYMM00` stay in raw fields while typed dates remain NULL.

## Core tables

### `us_case_current`

Latest source-ranked official case snapshot: case dates, raw USPTO status, mark metadata, filed/current basis flags, selected post-registration indicators, and Madrid international-registration facts.

### `us_owner_current`

Current owner rows observed in the latest authoritative case snapshot.

### `us_classification_current`

Current class rows and first-use evidence observed in the latest authoritative case snapshot.

### `us_statement_current`

Current typed USPTO statements observed in the latest authoritative case snapshot.

### `us_event_history`

Cumulative official event evidence. Events are not treated as a replace-all child collection because an event can remain historically relevant even if a later snapshot no longer repeats it.

## M1.2 child snapshot reconciliation

TDXF application case files are treated as complete observations for the replaceable child families `owner`, `classification`, and `statement`.

When a newer source-ranked snapshot touches a serial number:

1. the engine computes the child identities present in the new snapshot;
2. it reads the currently active child identities for that serial from ClickHouse;
3. any older active child identity omitted from the new snapshot receives a deterministic tombstone at the newer source rank;
4. child identities still present are published normally and are not tombstoned;
5. events are excluded from this process and remain cumulative historical evidence.

This fixes the stale-child failure mode where an owner, class row, or statement from the historical baseline could otherwise remain falsely current forever after disappearing from a daily case snapshot.

The replacement logic is source-rank guarded: a lower-ranked historical package cannot tombstone a child already established by a newer daily source.

Tombstones carry the new source package UUID. Full-package retry/cleanup therefore removes both newly published rows and omission tombstones. If a package fails, removing its outputs reveals the prior valid state again before deterministic replay.

## Status boundary

US M1.2 preserves official `status_code`, `status_date`, event evidence, and official filing/maintenance indicators. It does **not** turn them into MarkOrbit `ACTIVE/DEAD`, `REGISTERED/ABANDONED`, Section 8 compliance, renewal eligibility, or other legal conclusions in the fact layer.

US API responses continue to expose:

`status_semantics = OFFICIAL_RAW_NOT_LEGAL_INTERPRETATION`

Any later legal interpretation must be versioned and evidence-linked.

## Legacy refinery skill

The prior `us_base_refinery` / `us_daily_refinery` skill remains a field-coverage and business-rule reference rather than the ingestion implementation. Useful families identified there include correspondent/attorney, design search codes, prior registrations, foreign applications, Madrid request/events, owner mentions, and basis flags.

The skill's status-code enrichment is not copied into official fact tables without an independently validated status dictionary and interpretation layer.

## Runtime acceptance gates

Two live PostgreSQL/ClickHouse fixtures run in CI:

- `app.us.validate_fixture`: US M1.1 real-TDXF field regression, including direct/Madrid, filed/current basis, event description, and partial-date preservation;
- `app.us.validate_snapshot_fixture`: US M1.2 historical→daily child replacement, verifying owner/classification/statement disappearance plus cumulative event history.

Both fixtures clean all package-isolated rows after validation.

## Read API

- `GET /api/us/schema`
- `GET /api/us/summary`
- `GET /api/us/cases/{serial_number}`

Serial lookup remains exactly eight digits and returns official facts only.

## Next implementation layers

1. run larger real historical→daily acceptance profiles and measure tombstone rates;
2. port correspondent/attorney, design, prior-registration, foreign-application, and Madrid-request fact families from the old refinery skill using real TDXF identities;
3. establish full historical replay performance and coverage baselines;
4. only then build versioned US legal-status and maintenance-deadline interpretation.
