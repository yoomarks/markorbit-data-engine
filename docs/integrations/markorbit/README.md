# MarkOrbit consumer integration

This directory is the Data Engine provider-side control surface for the MarkOrbit cross-repository integration.

Canonical contract ID: `MARKORBIT_DATA_ENGINE_INTEGRATION_V1`.

- `MARKORBIT_DATA_ENGINE_INTEGRATION_V1.json` — machine-readable provider contract.
- `provider-contract.md` — MO-DE-001 / MO-DE-002 freeze.
- `runtime-semantics.md` — MO-DE-003 / MO-DE-004 / MO-DE-005 freeze.
- `integration-status.yaml` — provider-side integration ledger.
- `MO-DE-G0-RESPONSE-2026-08-23.md` — formal G0 response to MarkOrbit.
- `MO-DE-G1-ACCEPTANCE-2026-08-23.md` — provider-side evidence for the real authenticated `MO-DE-006` G1 acceptance lane.

Runtime self-description is `GET /api/v1/contract`. Consumers must not depend on Data Engine PostgreSQL, ClickHouse, raw-file layout, or internal table names.

`MO-DE-006` has real cross-repository acceptance evidence against Data Engine runtime SHA `42637eec302b1e2feeb6825e4f7b5208f4d00b9e` and MarkOrbit G1 merge `20bd9710e4af02e92fcfaa737ef67a9e58479145`. The provider ledger records the exact acceptance workflow and matrix. Global G1 closeout still requires the MarkOrbit consumer ledger to reference the final provider evidence state.

`MO-DE-007` and `MO-DE-008` remain deferred decisions and are not implemented by G1. No production deployment or live worker operation is authorized by these integration records.
