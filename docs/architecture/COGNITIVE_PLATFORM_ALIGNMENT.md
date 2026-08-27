# MarkOrbit Data Engine — Cognitive Platform Alignment

Status: Proposed alignment baseline

This document aligns Data Engine with the shared cognitive-platform architecture used by Brain, Capability, Knowledge, and product runtimes.

## 1. Data Engine responsibility

Data Engine owns objective structured facts and historical factual state.

The governing model is:

> Knowledge owns documents. Data Engine owns facts. Brain Research consumes both. Brain publishes reusable methods. Capability executes ACTIVE methods. Products own business state.

Data Engine is the large factual substrate. It serves both research and production execution while remaining conservative about interpretation.

## 2. Facts Data Engine should own

Examples include:

- trademark application/registration records;
- applicant/owner/entity records;
- prosecution/case/proceeding records;
- assignment/ownership events;
- trademark status/lifecycle events;
- dates/event timelines;
- factual entity identifiers;
- recorded addresses/contact attributes where legitimately sourced;
- historical snapshots/change feeds;
- source-backed factual relationships.

## 3. Dual consumer model

Data Engine has two first-class consumers.

### 3.1 Brain Research

Brain Research uses Data Engine to form and validate methods through:

- bounded sampling;
- population statistics;
- hypothesis testing;
- feature discovery;
- segment discovery;
- clustering where justified;
- training/calibration;
- historical backtesting;
- status-transition analysis;
- duration/time-series analysis;
- entity/relationship research.

Every research run should retain reproducible lineage to the exact Data Engine query, snapshot, partition, or dataset definition used.

### 3.2 Capability Runtime

Capability Runtime uses Data Engine for current factual execution of ACTIVE methods, including:

- risk scoring;
- entity resolution;
- relationship inference;
- opportunity discovery;
- ranking;
- current portfolio/status analysis;
- live or bounded statistical queries.

Data Engine should support efficient current execution without absorbing Brain method policy.

## 4. What Data Engine should not own

Data Engine must not persist inferred intelligence as source truth merely because Brain or Capability computes it.

Examples outside Data Engine factual truth:

- opportunity classification;
- renewal/expansion propensity;
- dead-mark commercial attractiveness;
- application risk score;
- inferred same-group probability;
- inferred holding-company classification;
- recommendation/ranking decisions;
- product campaign/workflow state.

If an inferred relation later becomes independently sourced fact, it may enter through normal factual-ingestion contracts.

## 5. Required research/query substrate

To support Brain Research without copying large source populations, Data Engine should evolve toward:

- scoped filtering/projection;
- deterministic pagination/streaming;
- joins across trademarks, entities, cases, assignments, and events;
- grouped aggregation and distinct counts;
- time-window/time-series queries;
- factual duration calculations over event timelines;
- status-transition extraction;
- historical snapshot/change-feed access;
- bounded/reproducible sampling;
- deterministic train/validation/backtest partitioning or equivalent reproducible split definitions;
- reproducible query/dataset identity;
- factual relationship traversal;
- feature-source extraction without embedding business interpretation;
- objective pre-aggregations/materialized factual views where scale requires them.

## 6. Reproducible dataset contract

A Brain Research dataset definition should be reproducible from explicit inputs such as:

- resource/fact families;
- jurisdiction/scope filters;
- temporal window;
- historical snapshot/as-of identity;
- selected fields;
- joins/traversals;
- sampling/partition rule;
- ordering/pagination semantics;
- query/dataset fingerprint.

The dataset contract exists to make methods and evaluations repeatable, not to move the dataset into Brain ownership.

## 7. Production execution efficiency

Capability should execute compiled Brain methods directly against current Data Engine facts where appropriate.

Example:

```text
ACTIVE RenewalOpportunityMethod + Data Engine current facts -> Discovery Capability -> transient candidates -> MarkReg pool
```

The candidate population does not become a Data Engine intelligence table or Capability cache.

For repeated expensive objective calculations, Data Engine may maintain factual/materialized primitives if their semantics remain neutral and reusable.

## 8. Long-term development obligations

### DE-CG-A — analytical primitive inventory

Audit current APIs for time-series, duration, status transition, joins/traversal, aggregation, historical access, sampling, streaming, and reproducible query identity.

### DE-CG-B — Brain Research dataset contract

Define a read-only reproducible research contract supporting large-scale method research/backtesting without population copy.

### DE-CG-C — Capability execution contract

Define efficient bounded query/streaming primitives for ACTIVE method execution against current facts.

### DE-CG-D — objective aggregation support

Add only objective reusable primitives that materially reduce repeated large scans, such as event durations, grouped counts, transition matrices, portfolio lookup, and recorded ownership traversal.

Do not encode opportunity/risk/recommendation policy into these primitives.

### DE-CG-E — historical/backtest readiness

Ensure research can reproduce historical conditions rather than evaluating only against present-state records.

### DE-CG-F — source-truth boundary tests

Add architecture documentation/tests ensuring inferred intelligence does not silently become factual source truth.

## 9. Exit criteria for Data Engine cognitive readiness

Data Engine is ready when:

- Brain Research can perform real statistical research, training, and backtests without bulk-copying source populations;
- every research/evaluation dataset can be reproduced from query/snapshot/partition identity;
- Capability can execute scoped ACTIVE methods efficiently against current facts;
- high-cost factual workloads have reusable objective query support;
- historical evaluation is possible where required;
- inferred intelligence remains outside factual source truth.
