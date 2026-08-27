# Cognitive Platform Phase 0 — Data Engine Audit

Status: first evidence-backed baseline.

## Confirmed current strengths

The repository already has important factual/research substrate pieces:

- ClickHouse cutover work and storage/runtime validation;
- current and historical trademark/case/party/event models;
- explicit case relation and party-history work;
- status/event-oriented native pipelines;
- country stores and source-specific acquisition paths;
- Core-facing fact query integration already exists at platform level.

These are strong prerequisites for Brain Research but do not yet prove a unified research dataset contract.

## Boundary

Data Engine remains fact truth. It may provide objective aggregations, historical slices and reproducible datasets, but must not persist opportunity, risk, entity-group probability or recommendation policy as source truth.

Two downstream modes are required:

1. **Brain Research mode** — sampling, historical datasets, aggregation, feature-source extraction, training/validation/backtest.
2. **Capability Runtime mode** — high-throughput queries over current facts using an ACTIVE executable Brain method.

## Required capability matrix to finish #311

| Primitive | Current evidence | Audit state |
| --- | --- | --- |
| current fact lookup | strong | confirm contract |
| historical/event access | strong | confirm cross-domain consistency |
| party history | implemented work exists | verify API surface |
| case relations | implemented work exists | verify traversal semantics |
| ClickHouse analytical substrate | present | benchmark/query-contract audit required |
| filtering/projection | expected | verify public/internal contracts |
| deterministic pagination/streaming | not yet proven for research | audit |
| grouped aggregation | not yet proven as generic contract | audit |
| distinct counts | not yet proven as generic contract | audit |
| time bucketing/time series | not yet proven as generic contract | audit |
| factual duration calculations | not yet proven as reusable primitive | audit |
| status-transition extraction | source events exist; reusable primitive not proven | audit |
| bounded/stratified sampling | not proven | gap candidate |
| reproducible dataset/query identity | not proven | P0 gap candidate |
| train/validation/backtest partition contract | not proven | P1 research gap |
| recorded relationship traversal | partial evidence | normalize/contract audit |

## Likely P0 architectural gap

Brain Research cannot be reproducible at production quality unless every research/backtest can identify the exact factual scope used. Therefore the highest-priority likely addition is a `ResearchDatasetRef` / reproducible query identity that binds at least:

- source/fact schema version;
- jurisdiction/resource scope;
- query/filter definition;
- historical/as-of boundary;
- snapshot or change-feed watermark;
- sampling rule/seed if sampled;
- row/count summary;
- generated_at;
- integrity/digest where practical.

This is factual lineage, not intelligence policy.

## Next exact tasks

1. Inventory existing query modules/API endpoints across global, CN, US and Core-facing contracts.
2. Map ClickHouse/PostgreSQL responsibility and which aggregations are already materialized objectively.
3. Verify status/event history can support duration and transition calculations without reconstructing semantics in Brain.
4. Define the smallest reproducible research dataset/query contract.
5. Benchmark one real research workload: examination-time statistics or renewal-history cohort analysis.
6. Only after the dataset contract is proven, begin the first Brain data-driven method backtest.
