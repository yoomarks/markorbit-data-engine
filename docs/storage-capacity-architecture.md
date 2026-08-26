# Storage Capacity Architecture

Status: P0 foundation. This document does **not** authorize a live-volume migration.

## Why this exists

The accepted CN corpus has reached production scale. Operator evidence on the current target host showed roughly 2.95 billion active ClickHouse fact rows and roughly 679 GB of active ClickHouse data, while Docker Desktop storage was already close to 0.8 TB against a roughly 1 TB virtual-disk ceiling. Continuing US and additional jurisdiction imports with the database data plane inside Docker Desktop's managed virtual disk would leave insufficient headroom for ClickHouse merges, temporary spill, and normal growth.

The immediate goal is therefore to separate container lifecycle from persistent data placement before global rollout.

## Storage tiers

### Hot: serving data

Hot storage is the ClickHouse/PostgreSQL data required by current MarkOrbit query, runtime, control-plane, and accepted integrity contracts. It should live on storage with predictable free-space headroom and database-appropriate latency.

Hot does not mean that every source artifact must remain represented at maximum row-level granularity forever. Decisions about compaction or materialized serving representations require separate evidence and must preserve reconstructibility and accepted source truth.

### Warm: canonical/history data

Warm storage contains complete canonical/history material required for rebuild, audit, historical analysis, or re-materialization but not necessarily for every online request. Movement from Hot to Warm is a later storage-model decision and is not implemented by this foundation PR.

### Cold/raw: source archive

Official ZIP/XML/JSON, images, acquisition payloads, snapshots, and other source artifacts belong on ordinary host storage or object storage rather than consuming the database/container virtual disk by default. Raw retention remains jurisdiction/source specific.

## Compose model

`docker-compose.yml` remains unchanged and continues to use the existing Docker-managed volumes. This is intentional: pulling a new revision on a live target host must not silently point PostgreSQL or ClickHouse at empty directories.

`docker-compose.external-storage.yml` is an explicit opt-in override. It requires three host paths:

- `POSTGRES_DATA_PATH`
- `CLICKHOUSE_DATA_PATH`
- `CLICKHOUSE_LOG_PATH`

Render and validate the proposed configuration without starting or recreating anything:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\check-external-storage-config.ps1 `
  -PostgresDataPath E:\MarkOrbitData\postgres `
  -ClickHouseDataPath E:\MarkOrbitData\clickhouse `
  -ClickHouseLogPath E:\MarkOrbitData\clickhouse-logs `
  -RequireExistingDirectories
```

The preflight only renders `docker compose config` and verifies that the three database targets resolve to bind mounts with the requested sources.

## Capacity gate

Use the existing read-only headroom gate before storage-heavy mutations/imports:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\assert-storage-headroom.ps1
```

The current policy requires both host and ClickHouse free-space thresholds plus reserve. The policy is deliberately a gate, not an automatic cleanup mechanism. A blocked result must not be worked around with volume pruning, `OPTIMIZE FINAL`, deletion, or blind migration.

Use the existing inventory command to identify table-level footprint:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\audit-storage.ps1
```

## Migration phases

### Phase 0 — inventory and destination preparation

1. Capture `audit-storage.ps1` and `assert-storage-headroom.ps1` reports.
2. Record current Compose volume identities and database service state.
3. Provision destination storage with enough capacity for the current corpus, copy overhead, ClickHouse merge/spill headroom, and expected growth.
4. Validate the proposed external-storage override with `check-external-storage-config.ps1`.
5. Confirm backup/rollback media and destination filesystem health.

No database service is changed in this phase.

### Phase 1 — migration rehearsal

Rehearse the copy and verification procedure against disposable/test volumes or a snapshot copy. Verify PostgreSQL startup, ClickHouse metadata/parts, table row/byte inventory, and application read paths. Do not use the live CN volumes as the first rehearsal.

### Phase 2 — explicit live cutover

Live cutover requires a separate operator-approved task. The cutover must:

1. establish a maintenance window and prevent new writes;
2. stop database writers cleanly;
3. create/verify a rollback point;
4. copy persistent data using a filesystem/database-safe method;
5. start the database services with both Compose files and the external path variables;
6. run read-only database/inventory checks before allowing writers;
7. retain the original Docker-managed volumes until post-cutover acceptance is complete.

This foundation does not automate Phase 2 because an automatic live-volume move would be unsafe on the current production-scale CN corpus.

### Rollback

If any cutover verification fails, stop writers/services using the external mounts, restore the original Compose invocation and original managed-volume data plane, and verify read-only inventory before resuming work. Do not delete the failed destination or original volumes until the incident is understood.

## Platform guidance

Windows bind mounts are a practical capacity escape for the current Docker Desktop development host, but high-volume production ClickHouse should ultimately use native Linux/block storage or an equivalent database-appropriate data plane. The Compose override is intentionally path-based so the same separation of container lifecycle and persistent data placement can be preserved when moving hosts.

## Global rollout gate

Large new jurisdiction imports remain blocked until there is enough verified storage headroom and an approved persistent-data path. In particular, do not begin target-host SG two-cycle acceptance or a full US corpus expansion merely by increasing Docker Desktop's virtual-disk cap.

The next architecture decision after this foundation is to profile CN table families—especially goods and observed events—and decide which representations must remain Hot versus which can be served through compact/materialized or Warm forms without losing accepted source truth.
