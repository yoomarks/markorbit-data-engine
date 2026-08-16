# MarkOrbit Domain Adapter Contract V1

`MARKORBIT_DOMAIN_ADAPTER_V1`

A jurisdiction/domain adapter is the only place that may encode source-specific parsing, source identity, trademark identity rules, mapping, semantics guards, and jurisdiction-specific acceptance rules.

The Data Engine platform owns the reusable execution substrate: durable work units, checkpoint/resume, bounded execution, progress/failure telemetry, provenance contracts, and consumer service boundaries.

## Lifecycle

```text
DISCOVER
  -> REGISTER_SOURCE
  -> VERIFY_SOURCE
  -> PARSE
  -> STAGE
  -> NORMALIZE
  -> PUBLISH
  -> EMIT_EVENTS
  -> AUDIT
  -> ACCEPT
```

The lifecycle is ordered. An adapter must not publish before source verification, accept before audit, or treat a successfully executed parser as corpus acceptance.

## Required invariants

- Source identity exists before source-fact mutation.
- Source verification happens before parsing/publishing.
- Replay is deterministic for the same authoritative source set and component version.
- Large work uses durable resume rather than process-memory progress.
- Provenance survives normalization and publication.
- Observation/history timestamps do not invent legal event time.
- Acceptance fails closed.
- Consumers cannot write back into source-fact tables.
- Adapter normalization does not create legal conclusions.

## Adapter-owned concerns

- source discovery;
- source identity and verification;
- source-specific parser;
- source-to-fact mapping;
- jurisdiction identity rules;
- jurisdiction semantics guards;
- jurisdiction acceptance rules.

## Platform-owned concerns

- durable work units;
- checkpoint and resume;
- bounded execution;
- progress and failure telemetry;
- provenance contract;
- consumer service boundary.

## Design rule

Adding WIPO/EUIPO/JPO/KIPO/UKIPO must not require copying the complete CN or USPTO ingestion engine. A new adapter should implement only the source- and jurisdiction-specific pieces and compose the platform-owned lifecycle and work primitives.
