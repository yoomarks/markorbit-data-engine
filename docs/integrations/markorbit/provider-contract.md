# Provider Contract Freeze — MO-DE-001 / MO-DE-002

Contract: `MARKORBIT_DATA_ENGINE_INTEGRATION_V1`

## MO-DE-001 — Query Contract V1

Canonical machine contract: `docs/integrations/markorbit/MARKORBIT_DATA_ENGINE_INTEGRATION_V1.json`. Runtime self-description: `GET /api/v1/contract`.

V1 is GET-only and storage-independent. Exact routes, query parameter constraints and pagination/cursor behavior are published in the machine contract. Additive compatibility is the default. Removing/renaming a V1 route, changing request bounds, changing response/envelope meaning, auth behavior or error semantics requires explicit cross-repository migration/RFC or a new contract version.

## MO-DE-002 — Missing / Coverage / Tombstone semantics

Data Engine freezes distinct provider meanings:

- `observed`: provider returned an observed factual resource.
- `not_found`: requested key is absent from the current provider read model; this does **not** prove coverage.
- `not_covered`: explicit provider coverage statement only; never infer from 404.
- `no_observation`: explicit state for a provider-confirmed covered scope with no observation; never infer from transport emptiness.
- `tombstone`: explicit state only when durable deletion/supersession evidence exists.
- `service_unavailable`: provider/dependency runtime failure; never convert to a factual negative.

Current V1 explicitly represents `observed`, `not_found`, and `service_unavailable`. `not_covered`, `no_observation`, and `tombstone` are reserved distinct states but are not yet emitted generically because the current provider surface does not prove those states across all resources. Consumers must preserve `unknown` rather than invent them.
