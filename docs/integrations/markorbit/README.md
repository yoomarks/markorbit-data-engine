# MarkOrbit consumer integration

This directory is the Data Engine provider-side control surface for the MarkOrbit cross-repository integration.

Canonical contract ID: `MARKORBIT_DATA_ENGINE_INTEGRATION_V1`.

- `MARKORBIT_DATA_ENGINE_INTEGRATION_V1.json` — machine-readable provider contract.
- `provider-contract.md` — MO-DE-001 / MO-DE-002 freeze.
- `runtime-semantics.md` — MO-DE-003 / MO-DE-004 / MO-DE-005 freeze.
- `integration-status.yaml` — provider-side integration ledger.
- `MO-DE-G0-RESPONSE-2026-08-23.md` — formal response to MarkOrbit.

Runtime self-description is `GET /api/v1/contract`. Consumers must not depend on Data Engine PostgreSQL, ClickHouse, raw-file layout, or internal table names. `MO-DE-006` is the later real authenticated acceptance gate. `MO-DE-007` and `MO-DE-008` remain deferred decisions and are not implemented by G0.
