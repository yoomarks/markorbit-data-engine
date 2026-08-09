# MarkOrbit Data Engine M1.6 Architecture

```text
Windows raw_data
  incoming/cn/*.zip
        |
        v
Package scanner + SHA-256 registry (PostgreSQL)
        |
        v
ZIP/member/encoding/schema adapter
        |
        v
ClickHouse staging (TTL 7 days)
        |
        +--> quality issues / package profiles (PostgreSQL)
        +--> deterministic entity candidates and mentions (PostgreSQL)
        |
        v
Per-package publish
        |
        +--> CN case / party / priority / Madrid facts
        +--> durable goods item current state
        +--> goods item observations / transitions
        +--> reconstructed class lifecycle scope
        +--> explainable observed events
        +--> case lineage / carve-out skeleton
        |
        v
archive/cn/original.zip

Official fact + durable evidence layer
        |
        v
EMPIRICAL case-status inference (R1-R7, read-only)
        |
        v
Historical audit + deterministic per-rule sample
        |
        v
File-based manual ground-truth review
```

## Storage responsibilities

### PostgreSQL

- package registry, package kind, source period and source precedence metadata;
- job runs, failures and recovery state;
- source member profiles and data-quality issues;
- goods status mapping registry;
- global entity candidates, aliases and source mentions.

### ClickHouse — official / durable evidence

Core CN tables include:

- `cn_case_current` — one current case row per complete application number;
- `cn_case_scope_current` — reconstructed current class scope;
- `cn_case_party_current` / relation history;
- agent, priority and Madrid current facts;
- `cn_observed_event` — explainable source-backed observations;
- case relationship and carve-out evidence.

M1.6 durable goods tables add:

- `cn_goods_item_current` — one durable current item state per strict goods identity;
- `cn_goods_item_observation` — source-backed item observations and state transitions;
- `cn_goods_scope_lifecycle_current` — class-level lifecycle rebuilt from the complete durable item universe.

### File system

- original official ZIP packages retained as authoritative source material;
- temporary extraction and audit reports;
- inference review JSON/CSV outputs under `reports`;
- no permanent per-run full snapshots.

## Source precedence

`source_rank` expresses source-semantic precedence, not ingestion time:

- `BASE_PARTITION`: filing-year partition, lower precedence;
- `MONTHLY_PATCH`: update-month patch, higher precedence;
- package sequence is only a deterministic tie-breaker.

A later ingest timestamp does not make a base partition outrank a monthly patch.

## Case identity

All cases remain within `jurisdiction = CN`:

```text
12345678A
  root: 12345678
  route: CN_DIRECT

G602365A
  root: G602365
  route: MADRID_DESIGNATION_CN
  WIPO IR: 602365
```

Both participate in the same CN derived-case graph. Suffix structure is evidence of derivation, not proof of the legal reason for derivation.

## Goods identity and lifecycle

M1.6 does not treat a monthly CSV row as the full legal scope. A durable goods item identity uses the strict source fields:

```text
application_number
+ class_no
+ goods_sequence
+ similar_group
+ normalized goods_name
```

For a monthly patch:

1. stage rows identify which application/class scopes are touched;
2. incoming rows update durable item current state according to source precedence;
3. omitted durable items remain present unless there is affirmative source evidence changing them;
4. the touched class scope is rebuilt from **all** current durable goods items;
5. lifecycle counters and scope hashes are recomputed from that full universe.

This is the core M1.6 invariant: **monthly omission is never deletion**.

## Goods status evidence

Raw goods status codes are preserved. Their operational mapping is evidence-layer semantics, not a case-level legal cause.

The lifecycle layer distinguishes, among other states:

- effective unless contradicted;
- effective at risk;
- inactive with high confidence;
- final inactive;
- unknown.

`FIRST_OBSERVED` means only that an item was first visible in the loaded source history. It is not used as the date on which a legal loss occurred. Temporal loss evidence requires a real `STATUS_CHANGED` observation with a source effective date.

## Case-status inference boundary

`CN_CASE_STATUS_INFERENCE_V1_EMPIRICAL` is downstream of official facts and durable goods evidence. It is not an official-fact table and does not mutate `cn_case_current`.

The inference model keeps separate:

1. official observed facts;
2. reconstructed goods-scope state;
3. inferred procedural/status candidate;
4. inferred cause candidate.

Every heuristic candidate carries a rule ID, confidence, model version and evidence references. Later official evidence can supersede an earlier heuristic without rewriting the source facts.

Historical validation uses the latest successfully loaded CN monthly data coverage date as its clock, never the wall clock. Audit V2 uses deterministic SHA-256 bottom-k sampling per rule so manual review is independent of database scan order.

## Ground-truth review boundary

Manual review is deliberately file-based:

```text
historical audit JSON
    -> deterministic review CSV
    -> reviewer checks official CNIPA evidence
    -> score JSON
```

Reviewer labels are not written into official fact tables. Decisive `CONFIRMED` or `REJECTED` labels require an `official_source_ref`. A score never automatically promotes an empirical rule; promotion remains an explicit model-governance decision.

## Runtime/version invariant

The repository root `VERSION` file is the single engine release marker. Docker copies it into `/app/VERSION`, and the API health/summary surfaces read the same value. CI contract tests must fail if the runtime, README, architecture or M1.6 validation tooling drifts from that marker.
