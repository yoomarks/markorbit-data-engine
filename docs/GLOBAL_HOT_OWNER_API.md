# Data Engine Global Hot owner API — bounded operator contract

Issue: #847. Consumer: Knowledge #903. Existing DE fact-admission owner: #841.

## Boundary

The dedicated ASGI entrypoint is `app.global_trademarks.owner_api:app`. It
reuses the existing Global Hot fact-admission and read routers and the same
ClickHouse truth table. It **does not** add an acquisition worker, a second
schema, a second currentness model, or a privileged backdoor into
`app.main_core`. Its health endpoint is `GET /api/v1/health/global-hot`.
It does not expose CN, US, or other Data Engine routes and does not mutate
schema at startup.

CN C2b #843 continues to own its full API/target routing gate. Do not
restart or repoint the shared Data Engine API for LA; source-only CN routes
must remain on their accepted source until independently cut over.

## Production preflight (no implicit authority)

1. Resolve the exact deployment environment and approved internal network
   boundary. Never copy Docker `.env` credentials onto the WSL production
   target, and never read or print production secrets for diagnostics.
2. Supply `FACT_ADMISSION_API_KEYS` and `INTEGRATION_API_KEYS` from the
   approved secret store; each key must be at least 32 characters. Their
   key sets must not overlap. `INTEGRATION_AUTH_MODE` must be `required`.
3. Supply the correct ClickHouse host/HTTP port/database and existing
   authorized credentials for the accepted target. WSL production
   ClickHouse and Docker ClickHouse are distinct; a healthy Docker
   connection is not evidence of target residency.
4. Confirm `hot_global` exists with at least 20% free space,
   `hot_global_only` resolves exclusively to `hot_global`, and the
   existing `markorbit_facts.global_trademark_hot_observation` table uses
   that policy. The app enforces these gates before binding.
5. Only after the operator separately authorizes this service deployment,
   start the existing module on the approved internal/loopback interface:

   `python -m uvicorn app.global_trademarks.owner_api:app --host 127.0.0.1 --port 18081`

   Do not use `--host 0.0.0.0`, public ingress or shared compose changes
   without a separately reviewed deployment and network policy.

## Acceptance

- Health returns `ready` only if credential configuration and target
  storage still pass. A transient failure returns 503 without revealing
  credentials, source facts or internal database error strings.
- Unauthenticated `POST /api/admin/v2/fact-admissions/global/observations`
  and unauthenticated Global Hot reads return 401. Fact-admission and
  read keys cannot be used interchangeably.
- The same DE owner contract enforces the LA pilot bounds (50 + 50 list
  IDs and one explicit detail), source evidence identity, field semantics
  and idempotent readback. This API entrypoint grants no full-crawl,
  legal-currentness, acquisition or schema-migration authority.
- Knowledge must first persist source evidence and prepared requests as
  authorized Workspace RawArtifacts. A governed publisher Job needs
  the exact cross-Source parent grant and active lease. Keep Knowledge
  #903 open until its authenticated admission receipt is stored and
  physical `hot_global` readback and replay are independently verified.

## Failure and rollback

Failed auth configuration or absent disk/policy/table/reserve prevents
startup. Stop only this owner API if health or auth fails; leave CN
services, worker processes, source databases and VHDX mounts unchanged.
No CN schema reset or global API cutover is part of this work.
