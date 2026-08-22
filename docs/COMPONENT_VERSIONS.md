# Component Version Matrix

MarkOrbit Data Engine uses an independent component-version model. The root `VERSION` file is the **engine release marker**; it must not be used to infer the version of every jurisdiction/domain component.

The authoritative machine-readable matrix is `app.component_versions.component_versions()` and is also exposed by `GET /api/v1/contract` under `component_versions`.

## Current matrix

| Component | Version | Meaning |
|---|---|---|
| Data Engine release | `M1.6` | Root release/runtime marker from `VERSION`. |
| CN fact model | `CN_M1.6` | China trademark durable fact model. |
| CN final checkpoint | `CN_M16_FINAL_CHECKPOINT_V1` | Read-only CN replay + Storage V2 + acceptance completion gate. |
| US Application schema | `US_M1.4` | USPTO application/current facts + durable change history. |
| US Assignment schema | `US_ASSIGNMENT_M1.0` | USPTO recorded assignment/interest facts; not title conclusion. |
| US TTAB schema | `US_TTAB_M1.2` | USPTO TTAB procedural facts; not outcome/substantive-rights conclusion. |
| US Alert Engine | `US_ALERT_ENGINE_M1.0` | Read-only normalized alert/event projection. |
| Trademark jurisdiction framework | `TRADEMARK_JURISDICTION_FRAMEWORK_V1` | Reusable source, identity, pipeline-routing, country-store, maturity, current-projection and scaffold contracts; the legacy Global Trademark source catalog is derived from this registry. |
| Trademark runtime adapter | `TRADEMARK_RUNTIME_ADAPTER_V2` | Shared runtime request/adapter registry plus generic source dispatch; V2 adds reusable functional adapters and permits source-only adapters without forcing a bespoke country CLI command. |
| Trademark source acquisition | `TRADEMARK_SOURCE_ACQUISITION_V1` | Shared page/cursor acquisition executor with bounded resume, atomic raw-object materialization, SHA256 evidence, cursor-loop detection and a durable local acquisition ledger. |
| Trademark HTTP transport | `TRADEMARK_HTTP_TRANSPORT_V1` | Read-only HTTPS transport with timeout/response-size gates, bounded 429/5xx/network retry, `Retry-After`, query redaction and secret-safe errors. |
| Trademark API pagination | `TRADEMARK_API_PAGINATION_V1` | Deterministic page-number, offset/limit and opaque-cursor request-position helpers without guessing source termination semantics. |
| Trademark HTTP acquisition adapter | `TRADEMARK_HTTP_ACQUISITION_ADAPTER_V1` | Bridges verified source-specific response interpretation into the shared resilient HTTP + pagination + raw-materialization stack, so country adapters need not reimplement fetch loops. |
| Trademark country scaffold | `TRADEMARK_COUNTRY_SCAFFOLD_V5` | Generates framework-aligned country packages with transport-aware acquisition plus reusable native-store, schema-install and durable-loader skeletons while generated sources remain disabled. |
| Trademark native store primitives | `TRADEMARK_NATIVE_STORE_PRIMITIVES_V1` | Reusable source-native append-only observation DDL/writer mechanics with deterministic provenance and replay drift protection while jurisdictions retain native columns. |
| Trademark country factory | `TRADEMARK_COUNTRY_FACTORY_V1` | Orchestrates framework-backed country profiles, capability/readiness audits, mapping contracts and scaffold generation without creating a second source registry. |
| Trademark factory registry | `TRADEMARK_COUNTRY_FACTORY_REGISTRY_V1` | Read-only projection of authoritative CountryPacks plus isolated virtual-pack support for deterministic tests. |
| Trademark jurisdiction plugin | `TRADEMARK_JURISDICTION_PLUGIN_V1` | Packages a CountryPack with source runtime, acquisition and native-store implementations behind one validated registration boundary without auto-discovery or side effects. |
| Trademark source capability matrix | `TRADEMARK_SOURCE_CAPABILITY_MATRIX_V1` | Conservatively derives transport/update/domain/current/asset readiness from explicit CountryPack contracts, retaining UNKNOWN when evidence is unresolved. |
| Trademark source mapping contract | `TRADEMARK_SOURCE_MAPPING_CONTRACT_V1` | Validated declarative selector-to-country-native observation mapping contract; simple field/JSON-pointer extraction only, with XML semantics left source-parser-owned. |
| Trademark mapped observation writer | `TRADEMARK_MAPPED_OBSERVATION_WRITER_V1` | Connects reviewed declarative mappings to native append-only observations while preserving source identity, parser/mapping lineage, payload and deterministic replay behavior. |
| Trademark native store bundle | `TRADEMARK_NATIVE_STORE_BUNDLE_V1` | Binds one source's mapping contracts to multiple native observation tables and reuses explicit install plus multi-domain append/rollback mechanics without defining a global common schema. |
| Trademark native ingest executor | `TRADEMARK_NATIVE_INGEST_EXECUTOR_V1` | Executes deterministic source-native record streams through native store bundles with source-identity checks, versioned lineage, bounded apply, durable checkpoint/resume and interruption recovery. |
| Trademark country factory readiness | `TRADEMARK_COUNTRY_FACTORY_READINESS_V1` | Reports declared engineering maturity and structural contradictions without auto-promoting a jurisdiction. |
| Trademark country factory scaffold facade | `TRADEMARK_COUNTRY_FACTORY_SCAFFOLD_V1` | Delegates factory scaffolding to the authoritative jurisdiction scaffold so templates cannot drift. |
| Global trademark schema | `GLOBAL_TM_SCHEMA_V2` | Versioned country-store control plane, manifests/ingestion ledger, and ordered Canada current-source ledger/views. |
| Global trademark operator | `GLOBAL_TM_OPERATOR_V4` | Retains V3 safe plan/apply/pin/bounded-resume behavior while moving GB/TM-Link/AU/CA intended-pipeline routing out of command branches and into jurisdiction/source descriptors. |
| Global trademark acceptance | `GLOBAL_TM_ACCEPTANCE_V2` | Fail-closed source-release acceptance requiring the exact operator-declared pipeline run for every manifest object; never jurisdiction-current or legal acceptance. |
| CIPO ST.96 rich observations | `CIPO_ST96_RICH_OBSERVATION_V1` | Immutable source-object observations for current owner/agent/service representative, goods/services, office events and source-declared registry relationships. |
| CIPO ST.96 current projection | `CIPO_ST96_CURRENT_PROJECTION_V1` | Monotonic source-current record/child projection ordered by manifest coverage date, precedence and sequence; stale ingestion remains history and cannot regress current state. |
| Contact ingestion | `CONTACT_INGEST_V1.6` | Multi-format contact ingestion including legacy XLS, historical `.josn` JSON exports, official HTML card directories, inline/multilingual public agent lists, scanned-PDF OCR fallback, known public-register table normalization, unresolved case-linked contact evidence, and serialized/retried Postgres apply. |
| Contact task control | `CONTACT_TASK_CONTROL_V1.1` | Incoming-folder discovery, parser-version re-evaluation, interrupted-task recovery, background admin execution, and archive workflow. |
| Storage policy | `DATA_ENGINE_STORAGE_V2` | Current-fact + true-delta history policy. |
| Storage headroom | `DATA_ENGINE_STORAGE_HEADROOM_V1` | Host + ClickHouse pre-mutation free-space gate. |
| Replay telemetry | `DATA_ENGINE_REPLAY_TELEMETRY_V1` | Read-only before/after replay storage/package telemetry with local append-only ledger. |
| Integration contract | `MARKORBIT_DATA_ENGINE_INTEGRATION_V1` | Stable read-only source-fact service contract. |
| Domain lifecycle | `MARKORBIT_DOMAIN_LIFECYCLE_V1` | Frozen CN → Application → Assignment → TTAB → final acceptance sequencing. |
| Four-domain acceptance | `MARKORBIT_FOUR_DOMAIN_ACCEPTANCE_V1` | Formal final evidence gate over four accepted domain reports. |

## Versioning rules

1. **Do not bump the root release just because one component changes.** A component can evolve independently when its compatibility boundary is explicit.
2. **Do not infer component versions from README prose.** Code constants and `component_versions()` are authoritative; documentation is tested against them.
3. **Schema/model versions describe fact semantics, not source freshness.** Coverage dates and source-package manifests are separate evidence.
4. **Integration contract version changes only for contract compatibility changes.** Internal schema increments do not automatically require a new external contract version.
5. **No legal-conclusion version is implied.** Assignment and TTAB versions continue to represent recorded/procedural source facts only.
6. **Operational telemetry versions do not redefine source facts.** Replay telemetry is an observability contract over runs, not a new fact-model authority.
7. **Contact ingestion versions describe the external-contact ingestion contract.** They do not authorize marketing execution or redefine trademark source facts.
8. **Contact task control only automates discovery and task management.** A file reaching `READY` never implies permission to import; apply remains an explicit operator action.
9. **The jurisdiction framework standardizes mechanics, not legal semantics.** Country-native identity, source fields, events, relationships and status remain source-faithful; the framework must not force them into a lowest-common-denominator global schema.
10. **Framework maturity is an engineering-state label, not live data acceptance.** `SOURCE_FOUND` through `PRODUCTION_CURRENT` describes implemented evidence gates; it never makes stale, unpiloted, or unaccepted data current by declaration.
11. **A generated country scaffold is intentionally disabled.** New source packs start `pipeline_ready=false`, `SOURCE_FOUND`, and keep placeholder identity/acquisition/current/runtime rules until official schema/sample evidence is reviewed.
12. **Runtime adapters standardize execution mechanics, not source interpretation.** V2 allows a source-only runtime adapter with zero bespoke commands because generic `ingest-source` is the scalable entrypoint; every adapter must still declare at least one exact jurisdiction/source key.
13. **Source acquisition materializes raw evidence before parsing.** Remote/API pagination belongs before the runtime parser: each acquired page is atomically written as immutable raw bytes with SHA256 and resumable cursor lineage so parsing can be replayed without re-contacting the authority.
14. **Acquisition credentials are not provenance metadata.** API keys, authorization headers and passwords remain inside source-specific transport/runtime configuration; the generic acquisition ledger accepts no credential fields and must not serialize them.
15. **HTTP transport is read-only and fail-closed by default.** V1 accepts only GET/HEAD, requires HTTPS unless explicitly overridden, bounds timeout/body size/retry count, redacts query strings from errors, and never treats transport success as source acceptance.
16. **Pagination helpers never decide source completeness.** Page-number/offset helpers advance only when the source adapter says more data exists, and opaque cursors remain source-declared values.
17. **The HTTP acquisition adapter reuses mechanics without guessing authority semantics.** A source-specific interpreter still owns stable page identity and whether a response declares `has_more` or a next opaque cursor; the shared adapter owns request assembly, resilient transport and conversion into raw acquisition pages.
18. **Country Scaffold V5 is transport- and native-store-aware but evidence-conservative.** It wires reusable acquisition, native-store install, durable source-record loader and runtime execution mechanics, but still guesses no endpoint, identity, parser, mapping, native column, current-state or acceptance semantics.
19. **Native-store primitives standardize provenance, not country fields.** Jurisdictions declare their own observation tables and native columns; the shared writer supplies deterministic source identity, parser/mapping lineage, source payload and replay protection.
20. **The Country Factory is an orchestration layer, not a competing registry.** Production profiles and capability/readiness reports are derived from framework CountryPacks; virtual packs exist only in isolated fixtures.
21. **Jurisdiction plugins are registration units, not auto-enable mechanisms.** A plugin may bind runtime/acquisition/store implementations to its CountryPack, but plugin construction performs no network, DDL, ingestion or maturity promotion and cannot make a disabled source production-ready.
22. **Plugin references must stay inside the declared country/source boundary.** Runtime source keys, acquisition bindings and native-store bundles are validated against the plugin CountryPack; cross-country or undeclared-source bindings fail closed.
23. **Capability reports distinguish UNKNOWN from NO.** Unresolved transport/update/asset contracts must not be silently treated as supported or unsupported.
24. **Declarative mappings stay source-native.** Mapping rules may extract source fields into declared country observation domains, but do not infer legal validity, owner history completeness, renewal opportunities, brand families, lead scores or other business semantics.
25. **Mapped observation writing preserves reviewed lineage.** The same source position under the same parser/mapping version must replay identically; conflicting evidence fails closed, while a reviewed parser/mapping version change may append distinct history.
26. **Native store bundles reuse multi-domain mechanics, not a common trademark schema.** Each bundle binds one source to explicit country-native tables/mappings; installation is an explicit migration action and one record bundle writes through a caller-owned transaction so partial domain families can be rolled back together.
27. **Native ingest resumes only the same reviewed pipeline lineage.** The executor hashes parser version, native table definitions and mapping contracts into the ingest-run metadata; a changed lineage cannot resume the same source-object/pipeline run and must use a new versioned pipeline id.
28. **Native source checkpoints are source-order evidence, not ingestion time.** `source_index` must be deterministic and contiguous; parsers may replay from index 1 or resume from checkpoint+1, while gaps/reordering fail closed.
29. **Factory readiness never self-promotes a jurisdiction.** Structural signals may identify contradictions, but maturity advancement remains an explicit reviewed decision backed by source-specific fixtures and acceptance evidence.
30. **Generic `ingest-source` does not weaken mutation safety.** It uses the same no-write default, explicit `--apply`, SHA-backed source object, manifest, execution lock, durable checkpoint/resume and release-acceptance boundaries as compatibility commands.
31. **Global trademark operator versions describe mutation safety and routing semantics.** V4 retains V3 path preflight/source pin/SHA re-verification and V2 bounded/resumable execution, but resolves the intended durable pipeline from the reusable country/source registry rather than hardcoded command-specific maps.
32. **Global trademark release acceptance is narrower than jurisdiction acceptance.** V2 additionally requires the exact intended pipeline declared on each source object; a release can still pass while the jurisdiction is stale, historically seeded, non-authoritative, or not trusted for silence.
33. **CIPO rich observations remain history.** They preserve each source object's evidence and are never destroyed merely because a later source wins current state.
34. **CIPO current state is ordered by source evidence, not execution time.** Manifest-backed observations compare `(source_period_end, source_precedence, source_sequence)`; stale observations remain history, equal-rank conflicts fail closed, and an unmanifested legacy source cannot overwrite an already ordered current record.

## Release process

When changing a component version:

1. change the owning component constant;
2. update migrations/fixtures/acceptance contract as needed;
3. verify `component_versions()` exposes the new value;
4. update this matrix/README in the same PR;
5. run Linux domain fixtures and Windows PowerShell CI;
6. bump root `VERSION` only when the Data Engine release itself is intentionally advanced.
