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

## Capability matrix

The capability matrix derives only facts that existing contracts can prove. V1 includes:

- file / HTTP API / SOAP API / SFTP source presence;
- bulk snapshot / historical seed / incremental update / Update+Delete semantics;
- record, party, goods/services, classification, event and relationship observation domains;
- implemented/unknown asset support;
- current projection, manifest ordering and tombstone support;
- presence of at least one pipeline-ready source.

`UNKNOWN` is intentional. An unresolved future source or unimplemented asset contract must not be
reported as unsupported or supported merely by assumption.

## Mapping contract and mapped writer

`TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` provides reusable mapping metadata without flattening native
country schemas. V1 executes deterministic simple field/column and JSON Pointer selectors. XML/XPath
namespace/cardinality semantics remain parser-owned.

`TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` connects reviewed declarative mappings to reusable native
observation tables. It preserves caller-owned record identity, parser/mapping/source lineage and raw
source payload. Required selectors fail closed, optional selectors may be absent, transforms must be
explicitly supplied by the jurisdiction adapter, and multiple rules cannot silently compete for the
same domain/target field.

## Native store bundle

`TRADEMARK_NATIVE_STORE_BUNDLE_V1` raises the reuse boundary from one mapped table to one reviewed
source-specific native store family.

A bundle contains one or more `StoreBinding` objects. Each binding joins an
`ObservationTableSpec` to a `MappingContract` while keeping the table's native columns and source
vocabulary intact. The bundle validates before mutation that:

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
Canada-native tables/fields, Australia can retain its own relational vocabulary, and future Japan or
Korea sources can declare different native columns.

## Readiness

The factory reuses the framework maturity states:

`SOURCE_FOUND -> SOURCE_PROFILED -> PREFLIGHT_READY -> PARSER_READY -> COUNTRY_STORE_READY -> HISTORY_READY -> CURRENT_PROJECTION_READY -> ASSET_READY -> PILOT_VALIDATED -> RELEASE_ACCEPTED -> PRODUCTION_CURRENT`

The readiness audit never promotes a country automatically. Engineering maturity is not equivalent
to source freshness, release acceptance, trusted-for-silence or any legal conclusion.

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
- derive source/update/domain/current/asset capabilities;
- validate simple declarative JSON mappings;
- generate HTTP and file country scaffolds through one factory interface;
- generate native mapping/schema/store-bundle skeletons without guessing source fields;
- keep generated packs disabled by default;
- run without DB writes or network calls.

PostgreSQL fixtures separately prove mapped writer and native-store-bundle behavior, including
identical replay, same-lineage drift rejection, source-object lineage and multi-domain transaction
boundaries.

## Boundaries

Country Factory V1 does not:

- create a Global Trademark Index;
- flatten jurisdiction-native schemas into one lowest-common-denominator table;
- infer legal validity, ownership, renewal opportunities, brand families or customer intent;
- automatically promote source data to `PRODUCTION_CURRENT`;
- activate a real country source;
- change live CN runtime behavior or QCC acquisition.
