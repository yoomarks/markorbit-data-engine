# MarkOrbit Data Engine M1.5 Architecture

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
Per-package atomic publish
        |
        +--> CN current facts / scope / party relations (ClickHouse)
        +--> explainable observed events (ClickHouse)
        +--> case lineage / carve-out skeleton (ClickHouse)
        |
        v
archive/cn/original.zip
```

## Storage responsibilities

### PostgreSQL

- package registry and source precedence metadata;
- job runs and failures;
- source member profiles and data-quality issues;
- goods status mapping registry;
- global entity candidates, aliases and source mentions.

### ClickHouse

- China trademark current case facts;
- one current scope row per application number and class;
- current and historical case-party relations;
- agent, priority and Madrid facts;
- observed fact events;
- derived case relationships and scope carve-out evidence.

### File system

- original official ZIP packages exactly once;
- temporary extraction and audit reports;
- no permanent per-run full snapshots.

## Precedence

`source_rank` is source-semantic precedence, not ingestion time:

- `BASE_PARTITION`: filing-year partition, lower precedence;
- `MONTHLY_PATCH`: update-month patch, higher precedence;
- package sequence is only a deterministic tie-breaker.

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

Both participate in the same CN derived-case graph.
