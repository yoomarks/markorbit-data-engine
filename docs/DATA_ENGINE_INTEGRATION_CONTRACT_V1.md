# MarkOrbit Data Engine Integration Contract V1

Contract ID: `MARKORBIT_DATA_ENGINE_INTEGRATION_V1`

The canonical G0 machine-readable contract is `docs/integrations/markorbit/MARKORBIT_DATA_ENGINE_INTEGRATION_V1.json`; runtime self-description is `GET /api/v1/contract`.

## Service role and ownership

Data Engine is a **Source Fact Service**. It owns source acquisition, observations, normalized factual read models, provider query runtime and provider-side factual change detection. MarkOrbit consumers own product interpretation, business state and product degradation.

Consumers may use versioned HTTP contracts only. Direct SQL, direct database-volume/file access and mutation of Data Engine source-fact tables by Core/Gateway/Lite/Brain are prohibited.

## V1 query plane — MO-DE-001

The stable prefix is `/api/v1`. V1 is read-only GET. Canonical resources are:

- `GET /api/v1/contract`
- `GET /api/v1/health`
- `GET /api/v1/cn/cases/{application_number}`
- `GET /api/v1/us/cases/{serial_number}`
- `GET /api/v1/us/cases/{serial_number}/360`
- `GET /api/v1/us/cases/{serial_number}/history`
- `GET /api/v1/us/cases/{serial_number}/assignments`
- `GET /api/v1/us/cases/{serial_number}/ttab`
- `GET /api/v1/us/changes`

Exact query fields, bounds and pagination/cursor behavior are frozen in the machine contract. Additive compatibility is the V1 default. Breaking changes require cross-repository migration/RFC or a new contract version.

## Fact-state semantics — MO-DE-002

`observed`, `not_found`, `not_covered`, `no_observation`, `tombstone`, and `service_unavailable` are distinct. Current V1 explicitly represents `observed`, `not_found` and `service_unavailable`. The other states are reserved and must not be inferred until Data Engine has evidence sufficient to emit them. In particular, 404 does not prove coverage and 5xx/timeout never means factual absence.

See `docs/integrations/markorbit/provider-contract.md`.

## Service authentication — MO-DE-003

Actual mechanism is service-to-service bearer API key:

```text
INTEGRATION_AUTH_MODE=disabled|required
INTEGRATION_API_KEYS=<key>[,<rotation-key>...]
Authorization: Bearer <key>
```

G1 target is `required`. Keys are at least 32 characters and may overlap for rotation. Invalid request credentials are 401; invalid required-mode provider configuration is 503. 403 is reserved for a future authenticated-but-forbidden condition; V1 currently has no scope/role authorization layer. Secrets remain environment-isolated and out of source control. TLS is required across non-loopback/shared service boundaries.

## Correlation — MO-DE-004

`X-Request-ID` is the provider hop/request identifier and current provider trace identifier. `x-correlation-id` is the end-to-end operation identifier. Valid incoming values are preserved; missing request ID is generated, and missing correlation ID defaults to the request ID. Both are echoed on `/api/v1` responses along with contract/source-owner provenance headers.

## Runtime failures/backpressure — MO-DE-005

V1 error responses use stable top-level `code`, `message`, `retryable`, and optional `detail` / `fact_state`. Query validation maps to 400. 404 is `not_found` with coverage still unknown. 429 is retryable and includes `Retry-After`. 5xx/503 and network timeouts are retryable and never become factual negatives.

Rate-limit enforcement is opt-in. When enabled, defaults are 120 requests per 60 seconds per source IP per provider process. Consumers must honor 429/`Retry-After` rather than hard-code those defaults.

See `docs/integrations/markorbit/runtime-semantics.md`.

## Response authority

Fact responses use the owner envelope:

```json
{
  "contract_version": "MARKORBIT_DATA_ENGINE_INTEGRATION_V1",
  "engine_version": "M1.6",
  "source_owner": "MARKORBIT_DATA_ENGINE",
  "jurisdiction": "US",
  "resource_kind": "TRADEMARK_CASE",
  "authority": "DATA_ENGINE_FACT_READ_MODEL",
  "legal_conclusion": false,
  "fact_state": "observed",
  "payload": {}
}
```

Raw/normalized Data Engine facts are not legal conclusions, do not authorize filing/execution, and do not transfer business-state ownership to Data Engine.

## Change feed and deferred scope

`GET /api/v1/us/changes` preserves lossless factual observation cursor semantics. G0 does not implement or freeze the later `MO-DE-007` / `MO-DE-008` ownership decisions beyond recording them as deferred.

## Control plane and writeback

`/api/admin`, `/api/jobs`, ingestion, replay, retry, reset, repair and source-package mutation are outside the consumer contract. There is no consumer writeback into Data Engine source facts.

## Storage independence

Provider storage topology is an implementation detail. Moving PostgreSQL/ClickHouse/raw storage must not require MarkOrbit business-semantic changes.
