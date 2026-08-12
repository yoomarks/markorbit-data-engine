# Storage Headroom Guard

## Why

Docker Desktop on Windows stores ClickHouse data inside a WSL VHDX. ClickHouse can report free space inside its filesystem while the Windows host volume backing the expanding VHDX is already close to full. Data Engine therefore treats host-volume free space and ClickHouse internal free space as separate safety signals.

## Default policy

Before a supported CN/US mutation command starts, both layers must pass:

- host volume: `max(128 GiB, 10% of total capacity) + 32 GiB reserve`;
- ClickHouse `default` disk: `max(128 GiB, 10% of total capacity) + 32 GiB reserve`.

The 32 GiB reserve is an operational safety reserve, not a prediction of corpus growth. Replay telemetry can later provide evidence-based growth estimates without weakening this minimum floor.

If either layer is below policy, mutation is blocked before ingestion starts.

## Host volume selection

`assert-storage-headroom.ps1` resolves the host volume in this order:

1. explicit `-HostStoragePath`;
2. `RAW_DATA_PATH` from the local `.env`;
3. repository root as a final fallback.

For the standard Windows deployment, keep `RAW_DATA_PATH` on the same host volume that backs Docker Desktop's Data Engine VHDX. If Docker data is deliberately moved to another volume, pass that volume/path explicitly when running the guard or update the deployment convention accordingly.

## Manual check

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\assert-storage-headroom.ps1
```

The command writes a JSON report under `reports/` containing host and ClickHouse free/total bytes, free percentages, effective required free bytes, and the final `safe_to_mutate` decision.

Policy thresholds may be made stricter for a one-off run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\assert-storage-headroom.ps1 `
  -MinimumHostFreeGiB 200 `
  -MinimumClickHouseFreeGiB 200 `
  -ReserveGiB 64
```

Do not lower the default floor merely to make a replay proceed. Resolve storage capacity first.

## Mutation coverage

The guard is mandatory for:

- CN full replay;
- CN guarded one-shot ingestion;
- CN retry;
- all US Application/Assignment/TTAB mutation entrypoints through `assert-domain-apply-gate.ps1`.

Read-only audits, readiness checks and API queries do not require storage headroom.
