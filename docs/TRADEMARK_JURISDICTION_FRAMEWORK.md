# Trademark Jurisdiction Framework V1

## Purpose

`TRADEMARK_JURISDICTION_FRAMEWORK_V1` turns repeated country-by-country trademark ingestion work
into a reusable platform capability.

The target onboarding flow for a new jurisdiction is:

`official source/API -> source profile -> country pack -> acquisition adapter -> immutable raw objects -> runtime adapter -> parser/mapping -> country-native store -> current projection -> acceptance -> bounded pilot`

The framework deliberately **does not** make every country look the same. It standardizes the
mechanics that should be reusable while preserving source-native legal/registry facts.

## What is reusable

The framework standardizes:

- source role, transport, format, adapter kind and update semantics;
- source-declared record identity;
- metadata-to-pipeline routing for multi-table/multi-stream sources;
- paginated/cursor source acquisition and resumable raw-object materialization;
- runtime request normalization, source selector validation, preflight and loader dispatch;
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
- acquisition authentication/pagination interpretation;
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

`GLOBAL_TM_OPERATOR_V4` uses this registry resolver. Adding another multi-table or multi-stream
jurisdiction therefore does not require another country-specific operator branch merely to decide
durable pipeline identity.

## Runtime adapter layer

`TRADEMARK_RUNTIME_ADAPTER_V1` is the reuse layer between materialized source objects and the
country-native loader.

The shared runtime contract separates generic ingestion orchestration from country-specific source
execution. A runtime adapter normalizes one source invocation into a `RuntimeRequest` containing:

- jurisdiction and source ID;
- exact materialized source path;
- parser version;
- source selector metadata;
- bounded `max_records` when requested;
- optional compatibility command identity.

It then provides only two source-specific hooks:

```text
preflight(RuntimeRequest) -> SourcePreflight
execute(RuntimeRequest) -> durable source-native loader
```

Everything around those hooks remains shared: source-object SHA registration, manifest attachment,
execution lock, intended-pipeline resolution, ingest-run checkpoints, bounded resume, release
acceptance and Data Trust.

The initial runtime registry wraps four existing execution families without rewriting their native
parsers:

| Runtime adapter | Sources covered |
|---|---|
| `UKIPO_2018_RUNTIME_V1` | GB `UKIPO_OPEN_DATA_2018` |
| `TM_LINK_RUNTIME_V1` | EU `TM_LINK_EU`, NZ `TM_LINK_NZ` |
| `IPGOD_2022_RUNTIME_V1` | AU `IPGOD_2022` |
| `CIPO_ST96_RUNTIME_V1` | CA GLOBAL baseline and WEEKLY source |

The old country-specific CLI commands are retained as compatibility entrypoints, but their
preflight/execute dispatch now resolves through the runtime registry. Loader maps live behind the
source adapters rather than in the top-level CLI.

A generic entrypoint is also available:

```bash
python -m app.global_trademarks.cli ingest-source \
  --jurisdiction GB \
  --source-id UKIPO_OPEN_DATA_2018 \
  --path <file> \
  --selector source_stream=DOMESTIC
```

Multi-table examples use the same mechanism:

```bash
python -m app.global_trademarks.cli ingest-source \
  --jurisdiction AU \
  --source-id IPGOD_2022 \
  --path <file.csv> \
  --selector source_table=application-events
```

The generic command is still **no-write by default**. `--apply` does not create a new safety path;
it enters the same existing source-object/manifest/checkpoint execution boundary as the
compatibility commands.

This is the intended pattern for jurisdiction N+1: the top-level CLI should not need a new country
`if/elif` execution branch merely because a new parser exists.

## Source acquisition layer

`TRADEMARK_SOURCE_ACQUISITION_V1` adds the reusable boundary in front of runtime parsing for remote
or paginated sources.

The source-specific acquisition adapter only needs to expose an initial pagination position and
fetch one authority page. The shared executor then owns:

- bounded page execution and resume;
- opaque cursor lineage;
- atomic raw-byte materialization;
- SHA256 evidence per page;
- durable local acquisition ledger;
- complete-session replay without re-fetching;
- tamper detection;
- non-advancing/repeated cursor detection;
- repeated page-key detection;
- fail-closed unledgered source drift after an interrupted object write.

This deliberately produces raw evidence before parsing:

`API/SFTP/remote source -> acquisition ledger + raw objects -> RuntimeRequest -> parser/store`

Authentication remains source-specific runtime configuration and is not accepted by the generic
ledger API. API keys, authorization headers and passwords must not become provenance metadata.

The acquisition executor does not itself guess HTTP endpoints, OAuth behavior, rate limits,
pagination parameter names or response semantics. Those details become very small country/source
adapter code after the official data contract is known. See `docs/TRADEMARK_SOURCE_ACQUISITION.md`.

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

Existing country parsers are **not** rewritten merely to satisfy the framework. Runtime adapters
wrap proven parsers/loaders and move repeated orchestration outward. Source-native parsing and
country-specific current-state rules stay where their semantics are explicit.

## Framework audit

Run:

```bash
python -m app.trademark_framework.cli audit
```

The audit is read-only and checks country/source contract consistency, aliases, pipeline routes and
pipeline-ID ownership. The CI framework fixture additionally audits the runtime registry and proves
that compatibility commands and generic source requests resolve to the same source/pipeline
identity. The acquisition fixture independently proves bounded/resumable raw materialization with
no network or database writes.

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

`TRADEMARK_COUNTRY_SCAFFOLD_V3` generates a 12-file skeleton covering country declaration,
source acquisition, source-native parser adapter, mapping, schema, no-write preflight, runtime
adapter, current projection, assets, acceptance and fixture guidance. The generated pack starts at
`SOURCE_FOUND` and intentionally contains:

- `pipeline_ready=False`;
- `TODO_SOURCE_IDENTITY`;
- acquisition/runtime/preflight/loader `NotImplementedError` gates;
- no current-state claim;
- no legal inference;
- parser/schema/preflight/mapping stubs that require official schema/sample evidence.

Use `--write` only when intentionally creating those files. Existing files are never overwritten.

The generator is a development accelerator, not an authority-discovery substitute. Finding an API
or download endpoint is not enough: authentication, pagination, identity, update semantics,
ordering, historical coverage and sample payload shape must still be verified.

## Expected future onboarding workflow

For a new jurisdiction, the desired engineering work becomes mostly:

1. locate and verify the official source/API and license/access terms;
2. profile real schema/data dictionary, authentication/pagination and representative samples;
3. create/complete the generated Country Pack;
4. implement the source acquisition adapter when remote acquisition is needed;
5. implement source-native identity/parser/mapping and runtime adapter;
6. reuse shared raw materialization/source-object/manifest/preflight/resume/operator/acceptance infrastructure;
7. prove acquisition resume, selector/pipeline routing, replay idempotency and interruption equivalence;
8. run adversarial fixtures and bounded real-data pilot;
9. promote source/pipeline maturity only after evidence passes.

The framework is successful when adding jurisdiction N+1 requires less infrastructure code than
jurisdiction N, without reducing source fidelity.

## Current boundary

The framework/runtime/acquisition layer intentionally does **not**:

- create a Global Trademark Index;
- force common legal statuses across jurisdictions;
- replace proven country-native parsers with a lowest-common-denominator parser;
- infer entity/brand families from registry relationships;
- decide PostgreSQL vs ClickHouse for all future native stores;
- declare CA/GB/AU/EU/NZ production-current merely because they have a Country Pack/runtime;
- turn an unresolved future source into a guessed API contract;
- perform live acquisition for a jurisdiction without a verified source adapter;
- rebuild or restart the live CN worker.

Next framework steps should be driven by demonstrated duplication after real source adapters exist:
HTTP retry/rate-limit helpers, page-number/offset/cursor transport helpers, SFTP/download helpers,
reusable native observation writers, generic manifest-ordered current-projection primitives, asset
pipeline primitives and country acceptance extensions.
