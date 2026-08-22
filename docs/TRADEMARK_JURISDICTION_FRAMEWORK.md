# Trademark Jurisdiction Framework V1

## Purpose

`TRADEMARK_JURISDICTION_FRAMEWORK_V1` turns repeated country-by-country trademark ingestion work
into a reusable platform capability while preserving jurisdiction-native source facts.

The target onboarding flow is:

`official source/API -> source profile -> CountryPack -> acquisition -> immutable raw evidence -> runtime/parser -> reviewed mapping/native store -> durable native ingest -> current projection -> acceptance -> bounded pilot`

The framework standardizes mechanics. It does **not** make every office use the same trademark
record, legal status vocabulary, identity, event model or current-state algorithm.

## Reusable platform layers

The reusable stack now includes:

1. `CountryPack` / `SourceDescriptor` for source role, transport, format, update semantics, identity,
   observation domains, current projection, assets, maturity and durable pipeline routes.
2. `TRADEMARK_SOURCE_ACQUISITION_V1` for bounded/resumable raw-object acquisition with SHA256 and
   durable cursor/page lineage.
3. `TRADEMARK_HTTP_TRANSPORT_V1`, `TRADEMARK_API_PAGINATION_V1` and
   `TRADEMARK_HTTP_ACQUISITION_ADAPTER_V1` for resilient remote acquisition.
4. `TRADEMARK_RUNTIME_ADAPTER_V1` for generic source dispatch into verified source-native parsers.
5. `TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` + `TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` for reviewed
   declarative mapping into country-native observation fields.
6. `TRADEMARK_NATIVE_STORE_PRIMITIVES_V1` for append-only observations with standardized provenance,
   parser/mapping lineage, deterministic replay and fail-closed replay drift detection.
7. `TRADEMARK_NATIVE_STORE_BUNDLE_V1` for binding one source to multiple reviewed native tables and
   writing a record/party/goods/event family through one caller-owned transaction.
8. `TRADEMARK_NATIVE_INGEST_RUNNER_V1` for reusable logical-record batching, checkpoint/resume,
   bounded execution and source-store contract pinning.
9. `GLOBAL_TM_SCHEMA_V2`, manifests, source objects, execution locks and
   `GLOBAL_TM_ACCEPTANCE_V2` for the shared control plane.

Source-specific code should increasingly be limited to verified source details, record identity,
parser behavior, reviewed mappings/native columns, update/order semantics, current projection,
assets and jurisdiction acceptance rules.

`No source observation != legal nonexistence` remains a platform invariant.

## Country Pack and source registry

A `CountryPack` declares jurisdiction aliases, country store schema, source-native identity,
observation domains, current projection, asset mode, source descriptors and engineering maturity.
The registry currently reverse-validates six materially different patterns: US, GB, EU, CA, AU and
NZ. `app.global_trademarks.catalog` is a compatibility projection from this registry rather than a
second manually maintained source catalog.

Maturity labels describe engineering evidence only. They do not make a source release current or
accepted.

## Pipeline routing and runtime

Multi-table/multi-stream sources use `PipelineRoute` rather than command-specific routing branches.
The shared resolver is:

```python
resolve_pipeline_id(jurisdiction, source_id, metadata)
```

`GLOBAL_TM_OPERATOR_V4` uses this registry, and `TRADEMARK_RUNTIME_ADAPTER_V1` normalizes materialized
source objects into source-specific preflight/execute hooks. Source-object SHA registration,
manifest attachment, execution lock and release acceptance remain outside the parser.

The generic command remains no-write by default:

```bash
python -m app.global_trademarks.cli ingest-source \
  --jurisdiction GB \
  --source-id UKIPO_OPEN_DATA_2018 \
  --path <file> \
  --selector source_stream=DOMESTIC
```

`--apply` enters the same protected operator boundary as compatibility commands.

## Acquisition and raw evidence

Remote/API sources are acquired before parsing. The shared acquisition stack owns bounded page
execution, raw response materialization, SHA256, resume/replay, cursor-loop protection, tamper
checking and secret-safe HTTP transport. The country adapter still owns the verified endpoint,
authentication, pagination parameters and official response continuation semantics.

The intended order is:

`authority -> immutable raw evidence -> source object -> parser -> reviewed mapping -> native store`

## Native stores and durable native ingest

Country-native stores are not a lowest-common-denominator global schema.

`TRADEMARK_NATIVE_STORE_PRIMITIVES_V1` standardizes provenance/replay around jurisdiction-chosen
columns. `TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` connects reviewed mappings to those tables and
`TRADEMARK_NATIVE_STORE_BUNDLE_V1` groups multiple native domains into one transaction boundary.

`TRADEMARK_NATIVE_INGEST_RUNNER_V1` then removes the repeated per-country record loop. A parser emits
`NativeRecordEnvelope` values containing:

- a contiguous 1-based logical `source_index`;
- the jurisdiction/source-native `record_key`;
- the parsed native object;
- optional original source payload.

The runner verifies source-object jurisdiction/source identity, pins the reviewed parser/mapping/table
contract to the durable source/pipeline run, batches bundle writes, and advances the global ingest
checkpoint in the same PostgreSQL transaction as the native observation writes. It supports bounded
`max_records` runs and exact resume.

Resume accepts either a parser replaying from logical record 1 or a parser starting exactly at
`checkpoint + 1`; any gap or non-monotonic logical sequence fails closed. A parser/mapping/table
contract change under the same durable source/pipeline also fails closed rather than mixing
semantics. Reviewed changes require a versioned pipeline/parser/mapping identity.

The runner does not acquire source bytes, create source objects/manifests, install DDL, invent record
identity or choose current-state winners.

## Current projection

History and current state remain separate. Supported modes are `EXISTING_SUBSYSTEM`,
`SOURCE_NATIVE_CURRENT`, `MANIFEST_ORDERED`, `HISTORICAL_ONLY` and `NOT_IMPLEMENTED`.

Canada proves one `MANIFEST_ORDERED` pattern using:

`(source_period_end, source_precedence, source_sequence)`

That ordering is not automatically copied to another office. Each jurisdiction must prove its own
snapshot/delta/API ordering semantics.

## New-country scaffold V5

The scaffold is no-write by default:

```bash
python -m app.trademark_framework.cli scaffold \
  --jurisdiction JP \
  --source-id JPO_OFFICIAL_BULK \
  --adapter-kind ZIP_XML \
  --transport FILE \
  --data-format XML \
  --update-semantics SNAPSHOT
```

`TRADEMARK_COUNTRY_SCAFFOLD_V5` generates a 13-file package covering country declaration,
acquisition, parser adapter, reviewed mapping registry, native-table registry, native-store bundle,
preflight, runtime, current projection, assets, acceptance and fixture guidance.

HTTP sources are wired to the reusable HTTP/pagination stack. Mapping/table registries start empty
and the generated store bundle raises `NotImplementedError` until reviewed source evidence exists.
Generated packs intentionally remain `SOURCE_FOUND`, `pipeline_ready=False`, keep
`TODO_SOURCE_IDENTITY`, and contain no guessed endpoint, schema, mapping, current-state or legal rule.

## Audit and acceptance boundary

Run:

```bash
python -m app.trademark_framework.cli audit
```

Framework/runtime/acquisition/mapping/native-store/native-ingest fixtures verify reusable mechanics.
They do not imply jurisdiction acceptance. The distinction remains:

`job complete != release accepted != jurisdiction current != trusted for silence != legal conclusion`

## Expected onboarding work for jurisdiction N+1

Once an official source is found, the desired work is mostly:

1. verify source/API access, license and representative payloads;
2. profile identity, schema, update semantics and coverage;
3. generate the V5 package;
4. fill source-specific acquisition/auth/pagination details if remote;
5. implement the source-native parser and record key;
6. declare reviewed table specs and mapping contracts;
7. bind them with `NativeStoreBundle`;
8. feed deterministic `NativeRecordEnvelope` records through the reusable native ingest runner;
9. prove source ordering/current projection separately;
10. run adversarial fixtures and bounded real-data pilot before maturity promotion.

## Current boundary

The framework intentionally does **not**:

- create a Global Trademark Index or common legal-status model;
- infer legal validity, ownership, renewal opportunities, brand families or customer intent;
- infer entity families from registry relationships;
- choose one universal PostgreSQL/ClickHouse schema for every country;
- turn an unresolved source into a guessed API contract;
- declare CA/GB/AU/EU/NZ production-current merely because reusable plumbing exists;
- perform live acquisition without a reviewed source adapter;
- rebuild/restart the live CN worker or enable QCC.

Further framework work should be driven by real duplication exposed by the first new-country pilot,
not by adding abstraction for its own sake.
