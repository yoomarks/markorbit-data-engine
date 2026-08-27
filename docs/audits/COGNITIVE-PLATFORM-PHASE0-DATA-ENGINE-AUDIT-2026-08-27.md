# Cognitive Platform Phase 0 — Data Engine Audit

Status: evidence-backed baseline; research-contract gap now proven.

## Confirmed current strengths

The repository already has important factual/research substrate pieces:

- ClickHouse cutover work and storage/runtime validation;
- current and historical trademark/case/party/event models;
- explicit case relation and party-history work;
- status/event-oriented native pipelines;
- country stores and source-specific acquisition paths;
- Core-facing authenticated fact query integration already exists.

These are strong prerequisites for Brain Research.

## Proven current cross-repository contract

Core's accepted Data Engine integration contract already establishes:

- service role `SOURCE_FACT_SERVICE`;
- read-only query plane;
- storage independence;
- fact envelopes with engine version, jurisdiction, resource kind, authority and explicit fact state;
- stable resources including trademark case/current/history/change-feed/assignment/TTAB fact families;
- query-resource descriptors with path, query shape and pagination semantics;
- request/correlation tracing;
- no consumer writeback and business-state ownership outside Data Engine.

This contract is correct for fact consumption and must be preserved.

However, it does **not** define a Brain Research dataset identity. There is currently no cross-repository contract carrying a deterministic research query definition together with snapshot/watermark, sampling rule/seed, schema identity, row/count summary and integrity lineage.

Therefore the `ResearchDatasetRefV1` gap is now proven at contract level, not merely suspected.

## Boundary

Data Engine remains fact truth. It may provide objective aggregations, historical slices and reproducible datasets, but must not persist opportunity, risk, entity-group probability or recommendation policy as source truth.

Two downstream modes are required:

1. **Brain Research mode** — sampling, historical datasets, aggregation, feature-source extraction, training/validation/backtest.
2. **Capability Runtime mode** — high-throughput queries over current facts using an ACTIVE executable Brain method.

## Capability matrix

| Primitive | Current evidence | Phase 0 decision |
| --- | --- | --- |
| current fact lookup | implemented contract | preserve |
| explicit fact-state semantics | implemented contract | preserve |
| authenticated read-only query plane | implemented contract | preserve |
| resource query descriptors + pagination declaration | implemented contract | preserve |
| request/correlation tracing | implemented | preserve; not sufficient as dataset identity |
| historical/event access | strong repository evidence | verify normalized research surface |
| party history | implemented work exists | verify API/read model surface |
| case relations | implemented work exists | verify traversal semantics |
| ClickHouse analytical substrate | implemented substrate | benchmark/query-contract audit required |
| filtering/projection | resource-specific support likely | normalize only where Brain research needs it |
| deterministic pagination/streaming for research | partial/contract-specific | research completeness semantics required |
| grouped aggregation | not proven as generic cross-repo contract | add objective primitives only if workload requires |
| distinct counts | not proven as generic contract | gap candidate |
| time bucketing/time series | not proven as generic contract | gap candidate |
| factual duration calculations | not proven as reusable contract | high-value pilot primitive |
| status-transition extraction | source events exist; generic primitive not proven | high-value pilot primitive |
| bounded/stratified sampling | not present in accepted contract | required Brain Research gap |
| reproducible dataset/query identity | absent from accepted contract | **P0 proven gap** |
| train/validation/backtest partition contract | absent | Phase 1 research feature after dataset identity |
| recorded relationship traversal | partial repository evidence | normalize as factual traversal, not inferred intelligence |

## Minimum `ResearchDatasetRefV1` now justified

A Brain research/backtest must be replayable without copying the population into Brain. The smallest contract should bind:

- `dataset_ref_id` / contract version;
- Data Engine engine/fact schema version;
- jurisdiction/resource kinds;
- canonical query/filter/projection definition;
- historical `as_of` boundary or explicit event/change-feed watermark;
- completeness/pagination semantics;
- optional objective aggregation definition;
- optional sampling strategy and deterministic seed;
- optional train/validation/test partition definition;
- row/count summary;
- generated-at timestamp;
- integrity digest or deterministic fingerprint where practical.

The ref points to a reproducible factual scope. It does not store the research population in Brain and does not encode risk/opportunity policy.

## Data Engine research execution principle

For small/bounded studies Brain may request rows/pages through the fact query plane. For large studies, the preferred path is Data Engine-side objective filtering/aggregation/sampling with a reproducible dataset reference returned to Brain. This prevents copying tens of millions of source rows merely to perform method research.

Capability production execution remains separate: it uses ACTIVE executable methods against current facts and should not depend on research-dataset construction for every request.

## Next exact tasks for #311

1. Inventory actual current endpoints/read models for case history, party history, relations, change feed and ClickHouse query surfaces; mark IMPLEMENTED/PARTIAL/MISSING against this matrix.
2. Define `ResearchDatasetRefV1` as an additive read-only contract; do not change existing FactEnvelope semantics.
3. Prove deterministic completeness/replay semantics for one existing history query.
4. Add/normalize only the minimum objective primitives required by the first research workload: examination-time duration + status-transition extraction is preferred because both are factual and easy to validate.
5. Run one bounded real cohort query and record query identity, time boundary, count and replay result.
6. Only after that evidence exists should Brain begin the first Data Engine-backed method backtest.
