# Trademark Country Factory

## Purpose

`TRADEMARK_COUNTRY_FACTORY_V1` is the orchestration layer for producing and auditing new
trademark jurisdiction adapters. It consumes the existing
`TRADEMARK_JURISDICTION_FRAMEWORK_V1` registry as its source of truth; it does **not** create a
second country/source catalog.

The design goal is:

`verified source/API -> CountryPack -> acquisition/runtime/parser -> reviewed mapping/native store -> durable native ingest -> current projection -> acceptance`

New jurisdictions should reuse acquisition, HTTP transport, pagination, raw-object evidence,
manifest/checkpoint/resume, runtime dispatch and native-store mechanics. Country-specific code should
be concentrated in source identity, native parsing/mapping/schema, source ordering/current
projection, assets and jurisdiction acceptance rules.

## Components

| Contract | Version | Purpose |
|---|---|---|
| Country factory | `TRADEMARK_COUNTRY_FACTORY_V1` | Top-level factory orchestration/audit contract. |
| Factory registry | `TRADEMARK_COUNTRY_FACTORY_REGISTRY_V1` | Read-only projection of framework `CountryPack` objects; supports isolated virtual-pack fixtures without mutating production registry. |
| Capability matrix | `TRADEMARK_SOURCE_CAPABILITY_MATRIX_V1` | Derives conservative country capabilities from explicit source/store contracts. |
| Mapping contract | `TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` | Validated declarative source-selector -> country-native observation-field mapping contract. |
| Mapped observation writer | `TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` | Applies safe declarative mappings into native observation rows while preserving caller-owned record identity and parser/mapping/source lineage. |
| Native store bundle | `TRADEMARK_NATIVE_STORE_BUNDLE_V1` | Binds one source's reviewed mapping contracts to multiple country-native observation tables and reuses explicit schema install plus multi-domain append mechanics. |
| Native ingest runner | `TRADEMARK_NATIVE_INGEST_RUNNER_V1` | Reuses deterministic logical-record sequencing, batch transactions, bounded resume/checkpoints and native-store bundle writes. |
| Readiness audit | `TRADEMARK_COUNTRY_FACTORY_READINESS_V1` | Projects declared engineering maturity and flags structural contradictions without auto-promoting a country. |
| Factory scaffold facade | `TRADEMARK_COUNTRY_FACTORY_SCAFFOLD_V1` | Delegates generation to the authoritative jurisdiction scaffold rather than maintaining a second template tree. |
| Jurisdiction scaffold | `TRADEMARK_COUNTRY_SCAFFOLD_V5` | Generates source-aware country packages with reusable acquisition plus native mapping/schema/store-bundle skeletons while keeping generated sources disabled. |

## Single source of truth

Production country profiles are derived from `CountryPack` and `SourceDescriptor`. The factory
registry is immutable/read-only and `app.global_trademarks.catalog` is a compatibility projection
from the same framework registry.

## Mapping and native store

`TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` executes only reviewed deterministic FIELD/COLUMN/JSON
Pointer selectors; XML/XPath namespace/cardinality behavior remains parser-owned.

`TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` preserves caller-owned record identity,
parser/mapping/source lineage and raw source payload. Required selectors fail closed, transforms must
be explicitly supplied and multiple rules cannot silently compete for one native target field.

`TRADEMARK_NATIVE_STORE_BUNDLE_V1` raises the reuse boundary from one mapped table to a reviewed
source-specific native store family. A bundle binds explicit `ObservationTableSpec` tables to
`MappingContract` objects and validates jurisdiction/source/schema/domain/native-column consistency
before mutation. `install_native_store_bundle()` is explicit migration-time DDL;
`append_native_record_bundle()` writes all configured native domains through the caller transaction.

This does not create a universal trademark record. Each jurisdiction keeps its native fields and
source vocabulary.

## Native ingest runner

`TRADEMARK_NATIVE_INGEST_RUNNER_V1` removes another repeated per-country loop:

`parser iterator -> source-native record key -> native store bundle -> batch transaction -> durable checkpoint/resume`

The parser emits `NativeRecordEnvelope` values with a contiguous 1-based logical `source_index`, a
source-native `record_key`, the parsed native object and optional raw payload. The runner:

- verifies the already-registered source object belongs to the same jurisdiction/source as the bundle;
- fingerprints parser version, durable pipeline identity, mapping rules and native-table contract;
- refuses to resume an existing source/pipeline when that reviewed contract changes;
- accepts either replay-from-start or exact checkpoint+1 parser resume, while requiring contiguous
  logical source indices;
- writes every configured native observation domain for a batch and advances the global ingest
  checkpoint in the **same PostgreSQL transaction**;
- supports `max_records` bounded runs that remain `PARTIAL` until source EOF is proven;
- resumes failed/partial runs from the durable source-record checkpoint;
- returns immediately for already `COMPLETE` source/pipeline runs;
- keeps `rows_committed` as logical source-record count, not observation-row count.

The contract hash deliberately excludes human notes/declaration order. Changes to parser/mapping or
named transform behavior must be represented by a reviewed versioned parser/mapping/pipeline change,
not silently resumed under old lineage.

The runner does **not** acquire raw source bytes, register manifests/source objects, install native
DDL, invent record identity, choose current-state winners or infer legal status. Those remain their
existing layers.

## Readiness

The factory maturity sequence remains:

`SOURCE_FOUND -> SOURCE_PROFILED -> PREFLIGHT_READY -> PARSER_READY -> COUNTRY_STORE_READY -> HISTORY_READY -> CURRENT_PROJECTION_READY -> ASSET_READY -> PILOT_VALIDATED -> RELEASE_ACCEPTED -> PRODUCTION_CURRENT`

The readiness audit never self-promotes a country. Engineering maturity is not equivalent to source
freshness, release acceptance, trusted-for-silence or a legal conclusion.

## Country scaffold V5

V5 generates 13 files. It preserves transport-aware acquisition and now creates:

- `mapping.py` with an empty reviewed `MappingContract` registry;
- `schema.py` with an empty `ObservationTableSpec` registry;
- `store.py` wired to `NativeStoreBundle`, `StoreBinding`, explicit install and bundle append.

The generated source remains `SOURCE_FOUND`, `pipeline_ready=False`, retains
`TODO_SOURCE_IDENTITY`, and contains no guessed endpoint, credential, pagination, native field,
mapping, current-state or legal rule.

## Regression evidence

The virtual `XX` country plus PostgreSQL fixtures now cover framework registry/capabilities,
HTTP/file scaffold generation, mapped observation writing, native-store bundles and the native ingest
runner. The runner fixture proves:

- `2 + 2 + 1` bounded runs reconstruct a five-record source and finish `COMPLETE`;
- record and party native observations advance together;
- a completed replay performs no new work;
- parser/contract drift under the same durable pipeline fails closed;
- non-contiguous source indices fail and leave the ingest run `FAILED` for explicit resume/repair.

## Boundaries

Country Factory V1 does not:

- create a Global Trademark Index or lowest-common-denominator country table;
- infer legal validity, ownership, renewal opportunities, brand families or customer intent;
- choose country current-state ordering automatically;
- automatically promote source data to `PRODUCTION_CURRENT`;
- activate a real country source;
- change live CN runtime behavior or QCC acquisition.
