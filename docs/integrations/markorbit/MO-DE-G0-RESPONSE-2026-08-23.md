# DATA ENGINE RESPONSE — MO-DE G0

Provider baseline SHA: `e1776dcceaef571e7e4ffc9cbb22688c04bc5015`  
Provider branch: `feat/mo-de-g0-provider-freeze`  
Provider PR(s): `yoomarks/markorbit-data-engine#209`

## MO-DE-001: FROZEN

Current behavior was GET-only `/api/v1` with runtime self-description, but exact query bounds/pagination were not published as one canonical machine contract and `/api/v1/contract` was missing from its own stable-resource list.

Canonical evidence: `app/integration_g0_contract.py`, `app/integration_api.py`, `docs/integrations/markorbit/MARKORBIT_DATA_ENGINE_INTEGRATION_V1.json`, `docs/integrations/markorbit/provider-contract.md`, `tests/test_mo_de_g0_contract.py`.

Frozen behavior: exact V1 routes/query constraints/pagination, storage independence, additive compatibility and breaking-change policy are machine-readable. Classification: additive provider-contract hardening; a breaking change requires cross-repo migration/RFC or a new version.

## MO-DE-002: FROZEN

Current behavior has factual observations, 404 for absent current read-model keys, and 5xx/503 for runtime/dependency failure. Current V1 does not generically prove `not_covered`, `no_observation`, or `tombstone`.

Frozen behavior: `observed`, `not_found`, `not_covered`, `no_observation`, `tombstone`, and `service_unavailable` are distinct. Current explicit states are `observed`, `not_found`, and `service_unavailable`; the other states are reserved until provider evidence exists. Consumers preserve unknown and never infer factual absence from 404, timeout, empty transport response or provider failure. Classification: additive semantic freeze.

## MO-DE-003: FROZEN

Current mechanism already uses Bearer service keys, minimum key length 32, comma-separated overlap rotation, `disabled|required`, 401 for bad credentials and 503 for invalid required-mode configuration.

Frozen behavior: G1 target `auth=required`; provider/consumer secrets stay in their own environment secret stores; credentials are environment-isolated; rotation/revocation does not redefine the application contract; TLS is required across non-loopback shared-service boundaries. 403 is reserved because V1 has no scope/role authorization layer. Classification: freeze of existing mechanism.

## MO-DE-004: FROZEN

Current behavior generated/echoed `X-Request-ID` but did not freeze its relationship to `x-correlation-id`.

Frozen behavior: `x-correlation-id` is end-to-end; `X-Request-ID` is provider hop/request and current provider trace identifier. Valid caller IDs are preserved; missing correlation defaults to request ID; both are echoed on `/api/v1`. Classification: additive header support.

## MO-DE-005: FROZEN

Current behavior had heterogeneous FastAPI/legacy error detail and no integration-specific rate limiter.

Frozen behavior: `/api/v1` errors normalize to stable `code/message/retryable` with optional `detail/fact_state`; validation is 400; 429 is retryable with `Retry-After`; 5xx/503 and network timeouts are retryable and never factual negatives; unsupported `contract_version` fails closed at the consumer. Rate limiting is opt-in and defaults, when enabled, to 120 requests/60 seconds/source-IP/provider-process. Classification: additive runtime hardening.

## MO-DE-006: READY AFTER G0 CONSUMER ACCEPTANCE

Prerequisites: MarkOrbit accepts the G0 freeze; Data Engine exposes a non-production runtime with `INTEGRATION_AUTH_MODE=required`; MarkOrbit Gateway uses its environment credential and validates the machine contract. Then run the real cross-repository matrix. Fixtures alone do not complete G1.

## MO-DE-007: DEFERRED — decision comments only

No implementation in G0. Data Engine continues to own factual change detection pending the later joint ownership freeze.

## MO-DE-008: DEFERRED — decision comments only

No feed cursor/consumer checkpoint implementation in G0. The provider-side G0 integration ledger is not the deferred change-feed checkpoint ledger.

## Provider-side integration ledger

`docs/integrations/markorbit/integration-status.yaml`

## Cross-repo decisions required from MarkOrbit

1. Accept the safe MO-DE-002 capability rule: current V1 does not generically emit `not_covered`, `no_observation`, or `tombstone`; MarkOrbit preserves unknown until Data Engine explicitly emits an evidence-backed state.
2. Accept `x-correlation-id` as end-to-end correlation and `X-Request-ID` as provider hop/request plus current provider trace identifier.

Provider PR/head/CI evidence is recorded in GitHub and should be copied into the MarkOrbit consumer ledger when this provider response is accepted.
