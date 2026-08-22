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
| Global trademark schema | `GLOBAL_TM_SCHEMA_V2` | Versioned country-store control plane, manifests/ingestion ledger, and ordered Canada current-source ledger/views. |
| Global trademark operator | `GLOBAL_TM_OPERATOR_V3` | No-write planning by default, explicit `--apply`, bounded `--max-records`, durable resume, and a one-shot source-object pin that re-verifies the preflight SHA immediately before loader mutation. |
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
9. **Global trademark operator versions describe mutation safety and manifest semantics.** V3 binds the plan path to its preflight, pins the registered source object, re-verifies its SHA immediately before loader execution, and retains V2 bounded/resumable execution semantics.
10. **Global trademark release acceptance is narrower than jurisdiction acceptance.** V2 additionally requires the exact intended pipeline declared on each source object; a release can still pass while the jurisdiction is stale, historically seeded, non-authoritative, or not trusted for silence.
11. **CIPO rich observations remain history.** They preserve each source object's evidence and are never destroyed merely because a later source wins current state.
12. **CIPO current state is ordered by source evidence, not execution time.** Manifest-backed observations compare `(source_period_end, source_precedence, source_sequence)`; stale observations remain history, equal-rank conflicts fail closed, and an unmanifested legacy source cannot overwrite an already ordered current record.

## Release process

When changing a component version:

1. change the owning component constant;
2. update migrations/fixtures/acceptance contract as needed;
3. verify `component_versions()` exposes the new value;
4. update this matrix/README in the same PR;
5. run Linux domain fixtures and Windows PowerShell CI;
6. bump root `VERSION` only when the Data Engine release itself is intentionally advanced.
