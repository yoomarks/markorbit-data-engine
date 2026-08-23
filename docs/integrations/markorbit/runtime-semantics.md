# Runtime Semantics Freeze — MO-DE-003 / MO-DE-004 / MO-DE-005

## MO-DE-003 — Service authentication/security

Actual mechanism: `Authorization: Bearer <key>`.

Configuration: `INTEGRATION_AUTH_MODE=disabled|required` and `INTEGRATION_API_KEYS=<key>[,<rotation-key>...]`. G1 target is `required`. Keys are at least 32 characters; multiple keys permit overlap during rotation. Provider credentials belong to the Data Engine environment secret store and matching consumer credentials to the MarkOrbit environment secret store. Secrets are never committed and are isolated by environment. Rotation adds/validates a replacement key before revoking the retired key. TLS is required across non-loopback/shared service boundaries.

401 means missing/invalid bearer credential. 403 is reserved for authenticated-but-forbidden behavior; current V1 has no scope/role authorization layer and does not invent a 403 condition. Invalid required-mode provider configuration is 503.

## MO-DE-004 — Request/correlation tracing

`X-Request-ID` is the provider hop/request identifier and current provider trace identifier. `x-correlation-id` is the end-to-end operation identifier. Valid incoming values are preserved; otherwise Data Engine generates a request ID and defaults correlation ID to the resolved request ID. Both are echoed on `/api/v1` responses. Consumers forward `x-correlation-id` across service hops and may create a new request ID per hop. Integration evidence/logging should retain both IDs when available.

## MO-DE-005 — Error, timeout, retry and backpressure

Integration errors use stable top-level `code`, `message`, `retryable`, with optional `detail` and `fact_state`.

- 400 invalid/malformed query: non-retryable until request corrected.
- 401 missing/invalid credential: non-retryable until credential corrected.
- 403 authenticated but forbidden: reserved; non-retryable.
- 404 current read-model key absent: non-retryable; coverage remains unknown unless separately proven.
- 409 contract/version conflict: non-retryable until negotiation corrected.
- 429 throttled: retryable; obey `Retry-After`.
- 5xx/503 provider/dependency unavailable: retryable with bounded exponential backoff/jitter; never a factual negative.

Rate-limit enforcement is opt-in. When enabled, default provider settings are 120 requests per 60 seconds per source IP per provider process using `INTEGRATION_RATE_LIMIT_ENABLED`, `INTEGRATION_RATE_LIMIT_MAX_REQUESTS`, and `INTEGRATION_RATE_LIMIT_WINDOW_SECONDS`. Consumers must honor 429/`Retry-After` even if a deployment uses a different negotiated envelope.

A client/network timeout is retryable and must never be converted to a factual negative. An unsupported response `contract_version` fails closed at the consumer.
