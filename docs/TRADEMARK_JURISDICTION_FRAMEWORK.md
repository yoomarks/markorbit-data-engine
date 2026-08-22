# Trademark Jurisdiction Framework V1

## Purpose

`TRADEMARK_JURISDICTION_FRAMEWORK_V1` turns repeated country-by-country trademark ingestion work
into a reusable platform capability.

The target onboarding flow for a new jurisdiction is:

`official source/API -> source profile -> country pack -> parser/mapping -> country-native store -> current projection -> acceptance -> bounded pilot`

The framework deliberately **does not** make every country look the same. It standardizes the
mechanics that should be reusable while preserving source-native legal/registry facts.

## What is reusable

The framework standardizes:

- source role, transport, format, adapter kind and update semantics;
- source-declared record identity;
- metadata-to-pipeline routing for multi-table/multi-stream sources;
- observation-domain capabilities (record/party/goods/event/relationship/etc.);
- current-projection mode and ordering contract;
- jurisdiction onboarding maturity;
- asset support state;
- pipeline IDs, parser/mapping versions and preflight profiles;
- country/source registry validation;
- a safe new-country scaffold that starts disabled;
- compatibility projection into the existing `app.global_trademarks.catalog` API.

Existing shared Global Trademark infrastructure remains responsible for SHA-backed source objects,
dataset manifests, no-write preflight, explicit apply, bounded pilots, checkpoints/resume,
execution locks and release acceptance.

## What remains country-native

The following must not be guessed or flattened simply to satisfy the framework:

- native record identity;
- source status vocabulary;
- application/registration/extension relationships;
- parties and party roles;
- goods/services structure and classification details;
- procedural events and dates;
- source-declared related marks/applications;
- source Update/Delete semantics;
- images/assets and document semantics;
- country-specific acceptance checks.

`No source observation != legal nonexistence` remains a platform invariant.

## Country pack

A `CountryPack` is the reusable onboarding unit. It describes:

- `jurisdiction` / aliases;
- `store_schema`;
- `JurisdictionStage` engineering maturity;
- `IdentityContract`;
- supported `ObservationDomain` values;
- `CurrentProjectionContract`;
- `AssetMode`;
- one or more `SourceDescriptor` objects;
- optional native-table and extension metadata.

The initial registry describes six materially different patterns already present in the project:

| Jurisdiction | Framework maturity | Reusable pattern being proven |
|---|---|---|
| US | `PRODUCTION_CURRENT` | compatibility with an existing mature subsystem |
| GB | `COUNTRY_STORE_READY` | delimited historical baseline plus pending weekly/comparable-right sources |
| EU | `COUNTRY_STORE_READY` | non-authoritative multi-file historical seed plus future official API |
| CA | `CURRENT_PROJECTION_READY` | ST.96 snapshot + Update/Delete + manifest-ordered current projection |
| AU | `COUNTRY_STORE_READY` | six-table source-native snowflake snapshot |
| NZ | `COUNTRY_STORE_READY` | historical multi-file seed plus future official API |

These maturity values describe **implemented engineering evidence only**. They do not promote a
source release, historical seed or jurisdiction to accepted/current data merely by declaration.
For example, Canada's framework maturity can be `CURRENT_PROJECTION_READY` while CIPO WEEKLY still
remains `pipeline_ready=false` pending assets and real-package pilot validation.

This reverse validation is intentional: the framework is accepted only if it can describe existing
heterogeneous implementations without erasing their differences.

## Source descriptor and pipeline routing

A source descriptor answers the questions that otherwise get redesigned for every country:

- What is this source for? (`PRIMARY`, `HISTORICAL_SEED`, `INCREMENTAL`, `ENRICHMENT`, `REFERENCE`)
- How is it acquired? (`FILE`, `HTTP_API`, `SOAP_API`, `SFTP`, existing subsystem, or unresolved)
- What parser family is appropriate? (delimited, ZIP/XML, multi-table, API, etc.)
- What is its data format?
- Is it a snapshot, historical seed, append stream, API-current source or Update/Delete feed?
- Is the source available now?
- Is the Data Engine pipeline actually ready now?
- Which pipeline IDs/parser/mapping/preflight contract implement it?

`active_now` and `pipeline_ready` remain separate. A known or available source is not considered
production ingestible merely because it exists. Unverified access/format must be represented as
unresolved rather than guessed into an API or file contract.

For a source that fans out into multiple durable pipelines, `PipelineRoute` maps source metadata to
the intended pipeline. Examples already encoded in the registry include:

- GB `source_stream=DOMESTIC|MADRID_IR`;
- EU/NZ TM-Link `source_table=applications|applicants|details|classes`;
- AU IPGOD six `source_table` values.

The shared resolver is:

```python
resolve_pipeline_id(jurisdiction, source_id, metadata)
```

`GLOBAL_TM_OPERATOR_V4` now uses this registry resolver. As a result, adding another multi-table or
multi-stream jurisdiction does not require adding another country-specific `if` branch to the
operator merely to determine durable pipeline identity.

## Current projection

History and current state are separate concepts.

The framework supports multiple current modes rather than imposing one algorithm on all offices:

- `EXISTING_SUBSYSTEM`: already implemented outside this framework layer;
- `SOURCE_NATIVE_CURRENT`: source itself declares a reliable current representation;
- `MANIFEST_ORDERED`: source releases compete using explicit manifest ordering;
- `HISTORICAL_ONLY`: useful seed, not verified current state;
- `NOT_IMPLEMENTED`: native ingestion can exist before current reconstruction is ready.

Canada proves the manifest-ordered pattern using:

`(source_period_end, source_precedence, source_sequence)`

The framework does not assume this ordering is correct for another jurisdiction until that source
contract is proven.

## Compatibility boundary

`app.global_trademarks.catalog` is retained for existing callers, but its country/source plans are
now generated from the framework registry. This removes a second manually maintained list of
jurisdictions and source readiness flags.

Existing loaders are **not** rewritten in V1. This is deliberate. Framework adoption is
incremental: first make contracts/registry/routing authoritative, then move repeated
operator/preflight/acceptance behavior behind reusable adapters without destabilizing working
country parsers.

## Framework audit

Run:

```bash
python -m app.trademark_framework.cli audit
```

The audit is read-only and checks country/source contract consistency, aliases, pipeline routes and
pipeline-ID ownership.

Inspect packs with:

```bash
python -m app.trademark_framework.cli show
python -m app.trademark_framework.cli show --jurisdiction CA
```

## New-country scaffold

The scaffold command is no-write by default:

```bash
python -m app.trademark_framework.cli scaffold \
  --jurisdiction JP \
  --source-id JPO_OFFICIAL_BULK \
  --adapter-kind ZIP_XML \
  --transport FILE \
  --data-format XML \
  --update-semantics SNAPSHOT
```

It plans a skeleton under:

`app/trademark_jurisdictions/<jurisdiction>/`

The generated pack starts at `SOURCE_FOUND` and the 10-file skeleton covers country declaration,
source adapter, native mapping, schema, no-write preflight, current projection, assets, acceptance
and fixture guidance. It intentionally contains:

- `pipeline_ready=False`;
- `TODO_SOURCE_IDENTITY`;
- no current-state claim;
- no legal inference;
- parser/schema/preflight/mapping stubs that require official schema/sample evidence.

Use `--write` only when intentionally creating those files. Existing files are never overwritten.

The generator is a development accelerator, not an authority-discovery substitute. Finding an API
or download endpoint is not enough: identity, update semantics, ordering, historical coverage and
sample payload shape must still be verified.

## Expected future onboarding workflow

For a new jurisdiction, the desired engineering work eventually becomes mostly:

1. locate and verify the official source/API and license/access terms;
2. profile real schema/data dictionary and representative samples;
3. create/complete the generated Country Pack;
4. implement only the source-native identity/parser/mapping and country-specific rules;
5. reuse shared source-object/manifest/preflight/resume/operator/acceptance infrastructure;
6. run adversarial fixtures and bounded real-data pilot;
7. promote source/pipeline maturity only after evidence passes.

The framework is successful when adding jurisdiction N+1 requires less infrastructure code than
jurisdiction N, without reducing source fidelity.

## V1 boundary

V1 intentionally does **not**:

- create a Global Trademark Index;
- force common legal statuses across jurisdictions;
- move all existing loaders into a new runtime abstraction at once;
- infer entity/brand families from registry relationships;
- decide PostgreSQL vs ClickHouse for all future native stores;
- declare CA/GB/AU/EU/NZ production-current merely because they have a Country Pack;
- rebuild or restart the live CN worker.

Next framework steps should be driven by demonstrated duplication in existing loaders: reusable
source-adapter execution, native observation writers, generic current-projection primitives,
asset pipeline primitives and country acceptance extensions.
