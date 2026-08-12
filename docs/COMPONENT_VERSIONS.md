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

## Release process

When changing a component version:

1. change the owning component constant;
2. update migrations/fixtures/acceptance contract as needed;
3. verify `component_versions()` exposes the new value;
4. update this matrix/README in the same PR;
5. run Linux domain fixtures and Windows PowerShell CI;
6. bump root `VERSION` only when the Data Engine release itself is intentionally advanced.
