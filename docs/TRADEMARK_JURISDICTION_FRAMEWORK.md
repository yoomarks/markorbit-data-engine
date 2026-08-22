# Trademark Jurisdiction Framework V1

## Purpose

`TRADEMARK_JURISDICTION_FRAMEWORK_V1` turns repeated country-by-country trademark ingestion work
into a reusable platform capability while preserving jurisdiction-native source facts.

The target onboarding flow is:

`official source/API -> source profile -> CountryPack -> acquisition -> immutable raw evidence -> runtime adapter -> source-native parser -> reviewed mapping/native store -> current projection -> acceptance -> bounded pilot`

The framework standardizes mechanics. It does **not** make every office use the same trademark
record, legal status vocabulary, identity, event model or current-state algorithm.

## Reusable platform layers

The reusable stack now includes:

1. `CountryPack` / `SourceDescriptor` contracts for source role, transport, format, update semantics,
   identity, observation domains, current projection, assets, maturity and durable pipeline routes.
2. `TRADEMARK_SOURCE_ACQUISITION_V1` for bounded/resumable raw-object acquisition with SHA256 and
   durable cursor/page lineage.
3. `TRADEMARK_HTTP_TRANSPORT_V1` + `TRADEMARK_API_PAGINATION_V1` +
   `TRADEMARK_HTTP_ACQUISITION_ADAPTER_V1` for resilient HTTP acquisition without duplicating
   retry/page loops in each country adapter.
4. `TRADEMARK_RUNTIME_ADAPTER_V1` for generic source dispatch into verified source-native parsers and
   loaders.
5. `TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` + `TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` for reviewed
   declarative FIELD/COLUMN/JSON-pointer mapping into country-native observation fields.
6. `TRADEMARK_NATIVE_STORE_PRIMITIVES_V1` for append-only source observations with standardized
   provenance, parser/mapping lineage, deterministic replay and fail-closed replay drift detection.
7. `TRADEMARK_NATIVE_STORE_BUNDLE_V1` for binding one source to multiple reviewed native observation
   tables and writing a record/party/goods/event family through one caller-owned transaction.
8. `GLOBAL_TM_SCHEMA_V2`, manifests, source objects, execution locks, durable ingest checkpoints,
   bounded pilots and `GLOBAL_TM_ACCEPTANCE_V2` for shared control-plane safety.

Source-specific code should increasingly be limited to what cannot safely be inferred generically:

- official endpoint/download/authentication details;
- source-declared record identity;
- parser behavior and native source vocabulary;
- reviewed field mappings and native columns;
- update/delete semantics and release ordering;
- current projection rules;
- asset/document semantics;
- jurisdiction-specific acceptance rules.

`No source observation != legal nonexistence` remains a platform invariant.

## Country Pack and source registry

A `CountryPack` is the reusable onboarding unit. It declares jurisdiction aliases, country store
schema, identity, observation domains, current projection, asset mode, source descriptors, maturity
and optional native-table metadata.

The current registry reverse-validates six materially different patterns already implemented:

| Jurisdiction | Engineering maturity | Pattern |
|---|---|---|
| US | `PRODUCTION_CURRENT` | existing mature USPTO subsystem |
| GB | `COUNTRY_STORE_READY` | delimited historical baseline with pending incremental sources |
| EU | `COUNTRY_STORE_READY` | historical multi-file seed with future official source |
| CA | `CURRENT_PROJECTION_READY` | ST.96 snapshot + Update/Delete + manifest-ordered current projection |
| AU | `COUNTRY_STORE_READY` | six-table source-native snowflake snapshot |
| NZ | `COUNTRY_STORE_READY` | historical multi-file seed with future official source |

These labels describe engineering evidence only. They do not make a source release current or
accepted. For example, Canada can have an implemented ordered-current projection while the CIPO
WEEKLY source remains `pipeline_ready=false` pending the remaining source-specific production gates.

`app.global_trademarks.catalog` is only a compatibility projection from this framework registry; it
is no longer a second hand-maintained source catalog.

## Pipeline routing and runtime

Multi-table/multi-stream sources declare `PipelineRoute` entries instead of growing command-specific
routing branches. Existing examples include GB source streams, EU/NZ TM-Link tables and AU IPGOD
source tables.

The shared resolver is:

```python
resolve_pipeline_id(jurisdiction, source_id, metadata)
```

`GLOBAL_TM_OPERATOR_V4` uses that source registry. `TRADEMARK_RUNTIME_ADAPTER_V1` then normalizes a
materialized source object into `RuntimeRequest` and exposes only source-specific preflight/execute
hooks. Source-object SHA registration, manifest attachment, execution lock, checkpoint/resume and
release acceptance remain outside the country parser.

The generic entry point remains no-write by default:

```bash
python -m app.global_trademarks.cli ingest-source \
  --jurisdiction GB \
  --source-id UKIPO_OPEN_DATA_2018 \
  --path <file> \
  --selector source_stream=DOMESTIC
```

`--apply` enters the same protected operator boundary as compatibility commands; it does not create
an alternate mutation path.

## Acquisition and raw evidence

Remote/API sources are acquired before parsing. The shared acquisition stack owns bounded page
execution, raw response materialization, SHA256, resume, replay, cursor-loop protection, tamper
checking and secret-safe HTTP transport. The country/source adapter still owns verified endpoint,
authentication, pagination parameter names and official response continuation semantics.

The intended order is:

`authority -> immutable raw evidence -> source object -> parser -> reviewed mapping -> native store`

This lets parser/mapping versions replay the same authority evidence without re-contacting a slow,
rate-limited or paid API.

## Native stores

Country-native stores are not a lowest-common-denominator global schema.

`TRADEMARK_NATIVE_STORE_PRIMITIVES_V1` standardizes provenance columns and replay behavior around
jurisdiction-chosen tables/columns. `TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` connects safe reviewed
mappings to those tables. `TRADEMARK_NATIVE_STORE_BUNDLE_V1` raises the reuse boundary to a whole
source-specific table family, so one parsed source record can append record/party/goods/event/etc.
observations through one explicit transaction without rewriting common provenance and replay logic.

XML/XPath namespace/cardinality interpretation remains parser-owned in Mapping V1. Declarative
mapping currently executes only selectors whose behavior is deterministic and reviewed.

## Current projection

History and current state remain separate. The framework supports:

- `EXISTING_SUBSYSTEM`;
- `SOURCE_NATIVE_CURRENT`;
- `MANIFEST_ORDERED`;
- `HISTORICAL_ONLY`;
- `NOT_IMPLEMENTED`.

Canada proves one `MANIFEST_ORDERED` pattern using:

`(source_period_end, source_precedence, source_sequence)`

That ordering is not automatically copied to another jurisdiction. Each office must prove its own
snapshot/delta/API ordering semantics. Source history can be complete enough to ingest while current
projection is still intentionally not implemented.

## New-country scaffold V5

The scaffold is a development accelerator, not an authority-discovery substitute. It is no-write by
default:

```bash
python -m app.trademark_framework.cli scaffold \
  --jurisdiction JP \
  --source-id JPO_OFFICIAL_BULK \
  --adapter-kind ZIP_XML \
  --transport FILE \
  --data-format XML \
  --update-semantics SNAPSHOT
```

`TRADEMARK_COUNTRY_SCAFFOLD_V5` generates a 13-file country package under
`app/trademark_jurisdictions/<jurisdiction>/` covering:

- country declaration;
- acquisition;
- source-native parser adapter;
- reviewed mapping registry;
- reviewed native-table registry;
- native-store bundle wiring;
- no-write preflight;
- runtime adapter;
- current projection;
- assets;
- acceptance;
- fixture guidance.

For HTTP sources the acquisition file is wired to the reusable HTTP/pagination stack. For every
source, `mapping.py` and `schema.py` start empty and `store.py` is wired to `NativeStoreBundle` but
raises `NotImplementedError` until reviewed table/mapping pairings are supplied.

Generated packs intentionally keep:

- `SOURCE_FOUND` maturity;
- `pipeline_ready=False`;
- `TODO_SOURCE_IDENTITY`;
- no guessed endpoint/auth/pagination rules;
- no guessed trademark fields or native schema;
- no current-state claim;
- no legal inference.

Use `--write` only when intentionally creating those files. Existing files are never overwritten.

## Audit and acceptance boundary

Run the read-only framework audit with:

```bash
python -m app.trademark_framework.cli audit
```

The framework, runtime, acquisition, mapping, native-store and virtual-country fixtures collectively
verify source registry consistency, pipeline routing, no-write scaffold behavior, HTTP/file source
patterns, deterministic replay and native-store construction mechanics.

None of these imply jurisdiction acceptance. The distinction remains:

`job complete != release accepted != jurisdiction current != trusted for silence != legal conclusion`

## Expected onboarding work for jurisdiction N+1

Once an official source is found, the desired work is mostly:

1. verify source/API access, license and representative payloads;
2. profile identity, schema, update semantics and coverage;
3. generate the V5 country package;
4. fill source-specific acquisition/auth/pagination details if remote;
5. implement the source-native parser and record identity;
6. declare reviewed native table specs and mapping contracts;
7. bind them with `NativeStoreBundle` and reuse common write/replay mechanics;
8. prove source ordering/current projection separately;
9. run adversarial fixtures and a bounded real-data pilot;
10. promote maturity only after acceptance evidence passes.

The framework is successful when jurisdiction N+1 requires substantially less infrastructure code
than jurisdiction N without sacrificing source fidelity.

## Current boundary

The framework intentionally does **not**:

- create a Global Trademark Index;
- force common legal statuses across offices;
- infer legal validity, ownership, renewal opportunities, brand families or customer intent;
- infer entity families from registry relationships;
- choose one universal PostgreSQL/ClickHouse schema for every future country;
- turn an unresolved source into a guessed API contract;
- declare CA/GB/AU/EU/NZ production-current merely because a CountryPack exists;
- perform live acquisition without a reviewed source adapter;
- rebuild/restart the live CN worker or enable QCC.

Further framework work should be driven by actual remaining duplication. The next high-value target is
the generic source-native ingest runner that can connect a parser iterator + source-native record key
+ `NativeStoreBundle` to durable checkpoints/bounded resume without making every new country rewrite
its record/batch transaction loop.
