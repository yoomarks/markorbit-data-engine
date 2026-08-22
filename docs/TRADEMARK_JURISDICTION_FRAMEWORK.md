# Trademark Jurisdiction Framework V1

## Purpose

`TRADEMARK_JURISDICTION_FRAMEWORK_V1` turns repeated country-by-country trademark ingestion work
into a reusable platform capability.

The target onboarding flow is:

`official source/API -> source profile -> CountryPack -> acquisition -> immutable raw evidence -> runtime -> parser/mapping -> native store -> current projection -> acceptance -> bounded pilot`

The framework deliberately **does not** make every country look the same. It standardizes mechanics
that should be reusable while preserving source-native legal/registry facts.

## Reusable platform layers

The current reusable stack includes:

- source role, transport, format, adapter kind and update semantics;
- source-declared record identity and pipeline routing;
- paginated/cursor acquisition and resumable raw-object materialization;
- resilient read-only HTTP transport and page/offset/cursor helpers;
- runtime request normalization, preflight and generic source dispatch;
- source-native observation-table primitives and provenance;
- declarative mapping contracts plus mapped observation writer;
- multi-domain native-store bundles;
- deterministic native-record ingest with bounded apply and durable checkpoint/resume;
- current-projection mode/order contracts;
- jurisdiction maturity/capability/readiness contracts;
- source-release acceptance and Data Trust boundaries;
- disabled-by-default country scaffolding;
- jurisdiction implementation plugin registration.

Existing Global Trademark infrastructure remains responsible for SHA-backed source objects, dataset
manifests, no-write planning, explicit apply, execution locks and source-release acceptance.

## Country-native boundary

The framework must not guess or flatten:

- source record identity;
- official status vocabulary;
- application/registration/extension relationships;
- parties and source-declared roles;
- goods/services and classification detail;
- procedural events and dates;
- related marks/applications;
- Update/Delete semantics;
- API authentication and response continuation semantics;
- image/document semantics;
- country-specific current-state and acceptance rules.

`No source observation != legal nonexistence` remains a platform invariant.

## CountryPack and source routing

A `CountryPack` declares jurisdiction/aliases, country store schema, engineering maturity, identity,
observation domains, current projection, asset mode, sources, native tables and optional extension
metadata.

The production registry currently describes six heterogeneous patterns: US existing mature
subsystems; GB historical baseline; EU/NZ historical multi-file seeds plus future official sources;
CA ST.96 snapshot + Update/Delete + ordered current; and AU six-table IPGOD. These packs prove that
one reusable framework can preserve materially different source models.

For multi-stream/table sources, `PipelineRoute` maps source metadata to exact durable pipeline ids.
The shared resolver is:

```python
resolve_pipeline_id(jurisdiction, source_id, metadata)
```

`GLOBAL_TM_OPERATOR_V4` uses this resolver instead of country-specific pipeline maps.

## Runtime adapter V2

`TRADEMARK_RUNTIME_ADAPTER_V2` is the boundary between a materialized source object and a
country-native loader.

A `RuntimeRequest` carries jurisdiction/source, exact materialized path, parser version, selector
metadata, optional `max_records` and optional compatibility command identity. A runtime adapter
provides source-specific preflight and execute hooks while shared orchestration remains outside.

V2 adds reusable `FunctionalRuntimeAdapter` and makes **source-only adapters** first-class. An
adapter may expose `commands=()` as long as it exposes at least one exact source key. New
jurisdictions therefore do not need bespoke top-level commands such as `ingest-jp` or `ingest-kr`;
they can use generic `ingest-source` dispatch. Existing GB/TM-Link/AU/CIPO compatibility commands
remain supported.

Generic invocation remains no-write by default:

```bash
python -m app.global_trademarks.cli ingest-source \
  --jurisdiction GB \
  --source-id UKIPO_OPEN_DATA_2018 \
  --path <file> \
  --selector source_stream=DOMESTIC
```

## Source acquisition and HTTP transport

`TRADEMARK_SOURCE_ACQUISITION_V1` owns bounded page execution, cursor resume, atomic raw-byte
materialization, SHA256 evidence, durable acquisition ledger, complete-session replay, tamper
checks, cursor-loop detection and fail-closed source drift.

Remote evidence is materialized before parsing:

`official API/remote source -> acquisition ledger + immutable raw objects -> runtime/parser/store`

`TRADEMARK_HTTP_TRANSPORT_V1`, `TRADEMARK_API_PAGINATION_V1` and
`TRADEMARK_HTTP_ACQUISITION_ADAPTER_V1` remove repeated GET/retry/429/5xx/pagination machinery while
keeping endpoint, authentication, page identity and continuation interpretation source-specific.
Credentials are runtime secrets, never provenance metadata.

## Native store and mapping reuse

`TRADEMARK_NATIVE_STORE_PRIMITIVES_V1` standardizes append-only provenance mechanics without
standardizing country fields. Jurisdictions still declare their own observation tables/columns.

`TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` describes reviewed source selectors to country-native fields.
Simple field/column/JSON-pointer extraction is reusable; XML namespace/cardinality semantics remain
parser-owned when generic extraction would be unsafe.

`TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` persists reviewed mappings with parser/mapping/source
lineage. `TRADEMARK_NATIVE_STORE_BUNDLE_V1` groups multiple native observation tables for one source
and writes one source record through the bundle in one caller-owned transaction.

`TRADEMARK_NATIVE_INGEST_EXECUTOR_V1` then owns the ordinary durable source-record loop: exact source
identity validation, parser/mapping/schema lineage hash, deterministic 1-based `source_index`,
bounded `max_records`, checkpoint/resume, interruption recovery, and fail-closed gaps/reordering or
truncated full replay. It does not download data, install DDL, parse authority formats or decide
current state.

## Current projection

History and current state are separate. Supported modes include existing subsystem,
source-native current, manifest-ordered current, historical-only and not-implemented.

Canada proves manifest ordering by explicit source evidence:

`(source_period_end, source_precedence, source_sequence)`

No other jurisdiction inherits that order unless its source contract proves it.

## Jurisdiction plugin

`TRADEMARK_JURISDICTION_PLUGIN_V1` packages one CountryPack with its optional source runtime,
acquisition and native-store implementations behind a validated registration boundary. Runtime
source keys, acquisition bindings and store bundles must refer only to sources declared by that
CountryPack.

Plugin construction/audit is side-effect free: it does not discover arbitrary modules, call the
network, install schema, ingest data or advance maturity. See
`docs/TRADEMARK_JURISDICTION_PLUGIN.md`.

## Country scaffold V5

The scaffold is no-write by default. `TRADEMARK_COUNTRY_SCAFFOLD_V5` generates a 14-file country
package covering:

- `country.py` source/profile contract;
- `acquisition.py` transport-aware acquisition skeleton;
- `adapter.py` deterministic native parser boundary;
- `mapping.py` reviewed MappingContract skeleton;
- `store.py` NativeStoreBundle skeleton;
- `schema.py` explicit native-store install boundary;
- `preflight.py` no-write source validation;
- `loader.py` NativeRecordEnvelope + durable native-ingest wiring;
- `runtime.py` generic runtime execution bridge;
- `current.py`, `assets.py`, `acceptance.py`;
- package/fixture guidance.

Example:

```bash
python -m app.trademark_framework.cli scaffold \
  --jurisdiction JP \
  --source-id JPO_OFFICIAL_BULK \
  --adapter-kind ZIP_XML \
  --transport FILE \
  --data-format XML \
  --update-semantics SNAPSHOT
```

Generated sources remain `pipeline_ready=False`, start at `SOURCE_FOUND`, retain
`TODO_SOURCE_IDENTITY`, and contain no guessed endpoint, parser, mapping, native column, current
state or acceptance rule. Existing files are never overwritten.

## Framework audit and acceptance boundary

`python -m app.trademark_framework.cli audit` validates source/alias/pipeline contracts. CI also
validates runtime, acquisition, HTTP, Country Factory, native-store, native-ingest and virtual
jurisdiction contracts.

Engineering layers remain distinct:

`source found != pipeline ready != ingest complete != release accepted != jurisdiction current != trusted for silence != legal conclusion`

## Expected N+1 onboarding

For a new jurisdiction the intended work is now mostly:

1. verify official source/API and access/license terms;
2. profile schema, representative payloads, authentication/pagination and update semantics;
3. complete the generated CountryPack/source identity;
4. implement the small source acquisition/parser/preflight boundaries;
5. declare reviewed mapping contracts and native-store bundle;
6. reuse generic source registration, manifest, runtime, native ingest and acceptance mechanics;
7. prove interruption/replay/current semantics with source-specific fixtures;
8. run a bounded real-data pilot;
9. explicitly promote maturity only after evidence passes.

The framework is successful when jurisdiction N+1 requires mainly source research and source-native
mapping rather than another ingestion platform implementation.

## Current boundary

This framework does **not** create a Global Trademark Index, force common legal statuses, infer
brand/entity families, declare historical data current, auto-enable real acquisition, or rebuild the
live CN worker. Automatic filesystem plugin discovery is also deferred; V1 plugin registration is
explicit until import trust/deployment rules are proven.
