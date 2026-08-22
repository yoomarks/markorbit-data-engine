# Trademark Native Store Primitives

## Purpose

`TRADEMARK_NATIVE_STORE_PRIMITIVES_V1` removes another repeated piece of country onboarding:
source-native append-only observation tables and their provenance mechanics.

The primitives standardize only the parts that should be identical across jurisdictions:

- deterministic `source_row_hash` replay identity;
- `record_key` linkage to the jurisdiction's source-declared identity;
- exact `source_object_id` provenance;
- deterministic `source_index` within the source object;
- raw/source-native `source_payload` evidence;
- `observed_at` ingestion timestamp;
- standard record/source indexes;
- idempotent append semantics.

The jurisdiction still chooses its own tables and native columns. This is **not** a global common
trademark schema.

## Core contract

An `ObservationTableSpec` declares:

```python
ObservationTableSpec(
    schema_name="trademark_xx",
    table_name="event_observation",
    domain=ObservationDomain.EVENT,
    native_columns=(
        NativeColumn("application_number", NativeSqlType.TEXT, nullable=False),
        NativeColumn("event_code", NativeSqlType.TEXT, nullable=False),
        NativeColumn("event_text", NativeSqlType.TEXT),
    ),
)
```

The resulting table contains the declared native fields plus the standard provenance envelope:

```text
source_row_hash
record_key
source_object_id
source_index
<native jurisdiction columns>
source_payload
observed_at
```

`source_object_id` is a foreign key to `acquisition.global_trademark_source_object`. The primitive
does not invent source identity or register source bytes by itself; those remain owned by the
existing acquisition/operator control plane.

## Supported native SQL types

V1 intentionally uses a bounded type set:

- `text`
- `smallint`
- `integer`
- `bigint`
- `boolean`
- `date`
- `timestamptz`
- `numeric`
- `jsonb`
- `text[]`

Schema/table/column identifiers are validated before SQL rendering. Native columns cannot reuse
reserved provenance names.

The bounded type set is deliberate: adding a new SQL type should be reviewed as a framework change
instead of allowing source adapters to inject arbitrary DDL fragments.

## Deterministic observation identity

`observation_row_hash()` hashes a canonical representation of:

- observation domain;
- source object UUID;
- source-native record key;
- source index;
- declared native values;
- source payload.

Dictionary ordering therefore does not affect replay identity. Replaying the same immutable source
observation is a no-op through `ON CONFLICT (source_row_hash) DO NOTHING`.

A genuinely different source observation receives a different hash and remains history rather than
overwriting previous evidence.

## Explicit DDL boundary

`install_observation_table(cur, spec)` runs only when explicitly called by migration/setup code. It
is not invoked automatically by `append_observation()`.

This preserves the platform rule that sanctioned ingestion should pass an explicit schema migration
boundary rather than silently creating tables while parsing data.

## Append boundary

`append_observation(cur, spec, row)` validates:

- nonblank `record_key`;
- `source_index >= 1`;
- only declared native columns are supplied;
- non-null native columns are present;
- JSONB native fields are adapted explicitly;
- the source-object foreign key already exists.

The primitive deliberately does not decide:

- current-state winner;
- Update/Delete precedence;
- legal status;
- owner/title conclusions;
- brand-family relationships;
- renewal opportunity;
- trusted-for-silence semantics.

Those remain separate country/source contracts.

## Why this speeds up a new jurisdiction

Before this primitive, a new country loader often repeated the same work to design provenance
columns, deterministic hashes, indexes and idempotent inserts for party/goods/event/relationship
history.

A new Country Pack can now focus its storage work on source-native differences:

```text
verified source schema
    -> identity/parser/mapping
    -> native table specs
    -> shared provenance/idempotent writer
    -> country-specific current projection
```

For unusual source structures, a jurisdiction may still implement bespoke native tables. The
primitive is a reusable building block, not a requirement to flatten richer source models.

## Regression fixture

`app.trademark_framework.validate_native_store_fixture` executes against PostgreSQL after the full
Global Trademark migration/legacy-upgrade fixture suite. It proves:

- additive table creation;
- standard provenance columns and source-object foreign key;
- native text, array and JSONB columns;
- deterministic hash independence from mapping key order;
- idempotent replay;
- append-only second source observation;
- rejection of missing required native fields;
- rejection of undeclared/invented native fields;
- no current-state or legal-conclusion behavior.

The fixture uses a synthetic source object and a temporary schema only. It performs no network or
real-jurisdiction acquisition.
