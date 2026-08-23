# MO-DE-006 — G1 Authenticated Cross-Repository Acceptance

Status: **ACCEPTANCE PROVEN — PROVIDER EVIDENCE RECORDED**

Date: 2026-08-23

This record is the Data Engine provider-side evidence for the real MarkOrbit ↔ Data Engine `MO-DE-006` G1 Protected Query Runtime acceptance lane. It does not authorize production deployment or any `MO-DE-007/008` work.

## Frozen inputs

- Data Engine provider runtime baseline: `42637eec302b1e2feeb6825e4f7b5208f4d00b9e`
- Data Engine G0 provider PR: `yoomarks/markorbit-data-engine#209`
- MarkOrbit G0 consumer acceptance merge: `a8035efff46a2e71a4613abd1927b18dadff086b`
- MarkOrbit G1 consumer PR: `yoomarks/markorbit#177`
- MarkOrbit final G1 PR head: `75cae91b88fc1b3587076f3e8b10cadd7e2e6d90`
- MarkOrbit G1 squash merge: `20bd9710e4af02e92fcfaa737ef67a9e58479145`

## Real acceptance lane

Workflow: `MO-DE-006 Cross-Repo Acceptance`

- Run ID: `32617003421`
- Run number: `23`
- Result: `success`
- Provider checkout: exact Data Engine SHA `42637eec302b1e2feeb6825e4f7b5208f4d00b9e`
- Consumer checkout: MarkOrbit PR #177 final head/merge candidate
- Authentication mode: isolated non-production `required`
- Credential: ephemeral runtime-generated Bearer service key; no credential committed
- Provider profiles: normal authenticated runtime, rate-limited runtime, intentionally invalid required-mode configuration
- Consumer path: MarkOrbit Gateway -> real Data Engine runtime -> frozen V1 descriptor/envelope validation
- Gateway acceptance tests: `16 passed`
- Teardown: isolated runner containers removed without `docker compose down -v`

The lane starts the provider from the frozen repository baseline and provider-owned database schema rather than substituting a mock provider. Repository-local fixtures remain supporting evidence only.

## Acceptance matrix

| G1 case | Evidence result | Contract interpretation |
| --- | --- | --- |
| authenticated 200 | **PROVEN** | Bearer-authenticated Gateway request reaches real Data Engine runtime and validates the frozen V1 response |
| unauthenticated 401 | **PROVEN** | missing/invalid service credential fails as unauthenticated |
| forbidden 403 | **NOT APPLICABLE / RESERVED** | frozen V1 has no scope/role authorization layer; no fabricated 403 behavior |
| not-found 404 | **PROVEN** | `not_found` remains distinct and does not imply coverage absence |
| `not_covered` | **RESERVED / NOT CURRENTLY EMITTED** | consumer preserves unknown until provider can emit an evidence-backed state |
| `no_observation` | **RESERVED / NOT CURRENTLY EMITTED** | consumer preserves unknown until provider can emit an evidence-backed state |
| `tombstone` | **RESERVED / NOT CURRENTLY EMITTED** | no removal/supersession state is invented without durable provider evidence |
| 429 / Retry-After | **PROVEN** | provider backpressure is machine-readable and retryable |
| timeout | **PROVEN** | real stalled transport is handled as retryable runtime failure, never factual absence |
| provider 5xx | **PROVEN** | invalid required-mode provider configuration produces the frozen 503 runtime failure semantics |
| schema/version mismatch | **PROVEN FAIL-CLOSED** | consumer rejects incompatible contract metadata rather than interpreting the payload |
| request/correlation tracing | **PROVEN** | `x-correlation-id` remains end-to-end; `X-Request-ID` remains provider hop/request trace |

## Consumer exact-head gates

The final MarkOrbit PR head `75cae91b88fc1b3587076f3e8b10cadd7e2e6d90` was merged only after its relevant checks completed successfully:

- `validation` run `32617003361` — success
- `MO-DE-006 Cross-Repo Acceptance` run `32617003421` — success
- `M8 WP-06 Commercial Runtime Reliability` run `32617003386` — success

MarkOrbit PR #177 was then squash-merged as `20bd9710e4af02e92fcfaa737ef67a9e58479145`.

## Frozen decisions preserved

1. MarkOrbit preserves `unknown` unless Data Engine explicitly emits an evidence-backed `not_covered`, `no_observation`, or `tombstone` state.
2. `x-correlation-id` is the end-to-end correlation identifier; `X-Request-ID` is the Data Engine provider hop/request and current provider trace identifier.
3. Runtime failure, timeout, rate limiting, provider 5xx, schema drift or transport failure are never converted into factual negatives.
4. MarkOrbit does not depend on Data Engine PostgreSQL, ClickHouse, raw-file layout or internal tables.

## Scope and closeout

Provider-side `MO-DE-006` evidence is now recorded. Global G1 closeout should occur only after the MarkOrbit consumer ledger references this final provider evidence state. `MO-DE-007` and `MO-DE-008` remain `deferred_no_implementation`.

No production credentials, production deployment, live provider action, Data Engine live worker rebuild/restart, CN replay mutation, change-feed implementation or cursor implementation is authorized by this record.
