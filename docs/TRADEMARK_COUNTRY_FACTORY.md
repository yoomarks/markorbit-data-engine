# Trademark Country Factory

## Purpose

`TRADEMARK_COUNTRY_FACTORY_V1` is the orchestration layer for producing and auditing new
trademark jurisdiction adapters. It consumes the existing
`TRADEMARK_JURISDICTION_FRAMEWORK_V1` registry as its source of truth; it does **not** create a
second country/source catalog.

The design goal is:

`verified source/API -> CountryPack -> acquisition/runtime/parser/native store -> current projection -> acceptance`

New jurisdictions should reuse acquisition, HTTP transport, pagination, raw-object evidence,
manifest/checkpoint/resume, runtime dispatch and acceptance mechanics. Country-specific code should
be concentrated in source identity, native parsing/mapping, native schema, source ordering/current
projection, assets and jurisdiction acceptance rules.

## Components

| Contract | Version | Purpose |
|---|---|---|
| Country factory | `TRADEMARK_COUNTRY_FACTORY_V1` | Top-level factory orchestration/audit contract. |
| Factory registry | `TRADEMARK_COUNTRY_FACTORY_REGISTRY_V1` | Read-only projection of framework `CountryPack` objects; supports isolated virtual-pack fixtures without mutating production registry. |
| Capability matrix | `TRADEMARK_SOURCE_CAPABILITY_MATRIX_V1` | Derives conservative country capabilities from explicit source/store contracts. |
| Mapping contract | `TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` | Validated declarative source-selector -> country-native observation-field mapping contract. |
| Mapped observation writer | `TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` | Applies safe declarative mappings into native observation rows while preserving caller-owned record identity and parser/mapping/source lineage. |
| Native store bundle | `TRADEMARK_NATIVE_STORE_BUNDLE_V1` | Binds one source's reviewed mapping contracts to multiple country-native observation tables and reuses explicit schema install plus atomic-cursor multi-domain append mechanics. |
| Readiness audit | `TRADEMARK_COUNTRY_FACTORY_READINESS_V1` | Projects declared engineering maturity and flags structural contradictions without auto-promoting a country. |
| Factory scaffold facade | `TRADEMARK_COUNTRY_FACTORY_SCAFFOLD_V1` | Delegates generation to the authoritative jurisdiction scaffold rather than maintaining a second template tree. |
| Jurisdiction scaffold | `TRADEMARK_COUNTRY_SCAFFOLD_V5` | Generates source-aware country packages with reusable acquisition plus native mapping/schema/store-bundle skeletons while keeping generated sources disabled. |

## Single source of truth

The factory must not recreate the definitions already owned by
`app.trademark_framework.contracts` and `app.trademark_framework.registry`.

Production country profiles are derived from `CountryPack` and `SourceDescriptor` objects. The
factory registry is immutable and read-only. `app.global_trademarks.catalog` remains a compatibility
projection from the same framework registry.

This prevents drift such as:

`catalog says source A is ready` while `runtime/factory says source A is not ready`.

## Capability matrix

The capability matrix derives only facts that existing contracts can prove. V1 includes:

- file / HTTP API / SOAP API / SFTP source presence;
- bulk snapshot / historical seed / incremental update / Update+Delete semantics;
- record, party, goods/services, classification, event and relationship observation domains;
- implemented/unknown asset support;
- current projection, manifest ordering and tombstone support;
- presence of at least one pipeline-ready source.

`UNKNOWN` is intentional. An unresolved future source or unimplemented asset contract must not be
reported as unsupported or supported merely by assumption. V1 deliberately does not claim owner
history completeness, agent history completeness or API pagination unless an explicit contract
proves those semantics.

Example:

```bash
python -m app.trademark_factory.cli capabilities --jurisdiction CA
```

## Mapping contract

`TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` provides reusable mapping metadata without flattening native
country schemas.

A rule identifies:

- selector kind (`FIELD`, `COLUMN`, `JSON_POINTER`, `XPATH`, `XML_LOCAL_PATH`);
- source selector;
- declared observation domain;
- country-native target field;
- whether the source field is required/repeated;
- optional named transform identifier.

The contract validates that the source exists in the target `CountryPack` and that mapped domains
are declared by that pack. V1 provides deterministic extraction only for simple field/column and
JSON Pointer selectors. XML/XPath extraction remains parser-owned because namespace, cardinality and
ordering semantics must be verified against each authority schema.

V1 also fails closed when two rules target the same observation-domain field. Fallback, merge and
coalesce semantics are not implicit; a later framework version must model them explicitly before
multiple selectors may write one target.

The mapping layer is not a legal/semantic normalizer. It must not create generic validity statuses,
renewal opportunities, inferred brand families, lead scores or legal conclusions.

## Mapped observation writer

`TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` connects reviewed declarative JSON/tabular mappings to the
reusable native-store primitives.

The writer:

- executes only selectors supported by the mapping contract;
- treats missing required selectors as errors and skips missing optional selectors;
- requires named transforms to be explicitly supplied by the jurisdiction adapter;
- preserves repeated values as source-native lists rather than inventing joins/flattening;
- requires the jurisdiction adapter to supply `record_key` instead of constructing a generic key;
- persists the mapping contract version and caller-supplied parser version on every observation;
- preserves the source payload by default;
- refuses mapped fields that do not exist in the declared native observation table;
- delegates replay identity and nondeterministic replay protection to
  `TRADEMARK_NATIVE_STORE_PRIMITIVES_V1`.

A source adapter can therefore move from raw JSON to a native append-only observation without
reimplementing common extraction/provenance/write glue:

`raw source -> MappingContract -> mapped native values -> ObservationRow -> native observation table`

XML/XPath execution remains parser-owned in V1. The writer does not guess namespaces, cardinality,
ordering, source identity, legal status or current-state semantics.

## Native store bundle

`TRADEMARK_NATIVE_STORE_BUNDLE_V1` raises the reuse boundary from one mapped table to one reviewed
source-specific native store family.

A bundle contains one or more `StoreBinding` objects. Each binding joins an
`ObservationTableSpec` to a `MappingContract` while keeping the table's native columns and source
vocabulary intact. The bundle validates, before mutation, that:

- binding ids and target tables are unique;
- every table belongs to the declared country store schema;
- every mapping contract belongs to the same jurisdiction/source;
- mapped domains and native columns are compatible;
- every non-null native column has a mapping rule;
- mapped targets not declared by the table fail closed.

`install_native_store_bundle()` is an explicit migration-time operation. It does not run implicitly
inside ingestion. `append_native_record_bundle()` maps one source-native record through every
binding using one caller-owned database cursor/transaction, so record/party/goods/event families can
be committed or rolled back together without rebuilding provenance/replay logic per jurisdiction.

The bundle does not invent a universal trademark record shape. A Canada source can still choose
Canada-native tables/fields, an Australia source can retain its own relational vocabulary, and a
future Japan/Korea source can declare different native columns. The reusable layer is the binding,
validation, provenance, replay and transaction mechanics—not the legal/data model semantics.

## Readiness

The factory reuses the framework maturity states:

`SOURCE_FOUND -> SOURCE_PROFILED -> PREFLIGHT_READY -> PARSER_READY -> COUNTRY_STORE_READY -> HISTORY_READY -> CURRENT_PROJECTION_READY -> ASSET_READY -> PILOT_VALIDATED -> RELEASE_ACCEPTED -> PRODUCTION_CURRENT`

The readiness audit never promotes a country automatically. It reports the declared stage and flags
obvious structural contradictions such as a current-ready country whose current projection remains
`NOT_IMPLEMENTED`.

Engineering maturity is not equivalent to source freshness, release acceptance, trusted-for-silence
or any legal conclusion.

## Country scaffold V5

V5 keeps the V4 transport-aware acquisition behavior and adds the native-store construction path to
every generated country package.

For file/bulk sources, the scaffold generates the generic acquisition adapter skeleton. For
`TransportKind.HTTP_API`, it generates the shared `HttpPaginatedAcquisitionAdapter` and
page/offset/cursor hooks. In both cases it now also generates:

- `mapping.py` with an empty reviewed `MappingContract` registry rather than guessed mappings;
- `schema.py` with an empty `ObservationTableSpec` registry rather than ad-hoc DDL;
- `store.py` wired to `NativeStoreBundle`, `StoreBinding`, `install_native_store_bundle()` and
  `append_native_record_bundle()`.

`store.py` deliberately raises `NotImplementedError` until the table/mapping pairings are reviewed.
The generated source remains `pipeline_ready=False`, retains `TODO_SOURCE_IDENTITY`, and contains no
guessed endpoint, credential, pagination, native-field, mapping or current-state rule.

Example no-write plan:

```bash
python -m app.trademark_factory.cli scaffold \
  --jurisdiction JP \
  --source-id JPO_OFFICIAL \
  --adapter-kind REST_API \
  --data-format JSON \
  --update-semantics API_CURRENT \
  --transport HTTP_API
```

Add `--write` only after the source contract is verified. Existing files are never overwritten.

## Virtual-country regression

`app.trademark_factory.validate_fixture` creates an in-memory `XX` jurisdiction only for CI. It
proves that the factory can:

- register/resolve a CountryPack without changing production registry;
- derive API, Update/Delete, observation-domain, asset and current-projection capabilities;
- validate and execute simple declarative JSON mappings;
- generate HTTP and file country scaffolds through one factory interface;
- generate native mapping/schema/store-bundle skeletons without guessing source fields;
- keep generated packs disabled by default;
- run without DB writes or network calls.

The mapped-writer PostgreSQL fixture extends that proof through a real native observation table: it
verifies identical replay, same-lineage drift rejection, source-object lineage, and an intentional
mapping-version change producing distinct historical evidence.

The native-store-bundle fixture extends the proof again across record, party and goods/service
native tables in one transaction boundary, verifying multi-domain insert, idempotent bundle replay
and fail-closed same-lineage drift.

The virtual country is test evidence only and must never appear in the production jurisdiction
registry.

## Boundaries

Country Factory V1 does not:

- create a Global Trademark Index;
- flatten jurisdiction-native schemas into one lowest-common-denominator table;
- infer legal validity, ownership, renewal opportunities, brand families or customer intent;
- automatically promote source data to `PRODUCTION_CURRENT`;
- activate a real country source;
- change live CN runtime behavior or QCC acquisition.
