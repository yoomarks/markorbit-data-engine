# Windows ClickHouse named-volume to E: Hot cutover

This runbook moves the authoritative ClickHouse data root from the current Docker-managed named volume to the dedicated E: SSD Hot path. It does not rebuild, re-import, or revalidate source packages.

## Safety model

The operator is deliberately fail-closed:

- the current `/var/lib/clickhouse` named volume is discovered from the actual Compose container, never guessed;
- readiness blocks while any `control.job_run` is `RUNNING` or any CN source package is `PROCESSING`;
- the E: Hot target must be empty and have the measured source-volume size plus 128 GiB reserve by default;
- source size is measured from a disposable container with the source volume mounted read-only;
- ClickHouse table/row/byte baseline comes only from `system.parts` metadata;
- migration execution requires the explicit `-Execute` switch;
- writers are stopped first and control state is checked again before ClickHouse is stopped;
- the source named volume is mounted read-only for the copy and is never deleted;
- after Hot activation, active table/part/row/byte metadata must exactly match the pre-cutover baseline and the `cold` disk must be registered;
- any failure after ClickHouse is stopped attempts to restore the original default Compose ClickHouse against the untouched named volume;
- the E: copy is retained on rollback for diagnosis and is never automatically deleted.

Never use `docker compose down -v` or remove the original ClickHouse volume as part of this cutover.

## 1. Read-only readiness

Set the already-preflighted paths in the current PowerShell session:

```powershell
$env:CLICKHOUSE_HOT_DATA_PATH="E:/MarkOrbitData/hot/clickhouse"
$env:CLICKHOUSE_COLD_DATA_PATH="F:/MarkOrbitData/cold/clickhouse"
$env:CLICKHOUSE_LOG_PATH="E:/MarkOrbitData/hot/clickhouse-logs"
```

Then run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\check-clickhouse-hot-cutover-readiness.ps1
```

This may take some time because it measures the stopped-copy source directory size through filesystem metadata. It does not read trademark rows or alter the source volume.

Do not proceed unless `safe_to_cutover` is `true`. Keep the JSON as the pre-cutover receipt.

## 2. Approved cutover window

Only after the readiness receipt has been reviewed, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\migrate-clickhouse-volume-to-hot.ps1 `
  -Execute
```

The operator captures which API/worker services were running, stops only those services, performs a second task/package check, probes E:/F: bind-path writability, stops ClickHouse, copies the stopped named volume to E:, verifies copy size, activates the Hot/Cold Compose profile, compares ClickHouse metadata, verifies the `cold` disk, and restarts the services that were running before the cutover.

For a corpus of hundreds of GB, the copy window can be long. Do not terminate Docker Desktop or the host while the copy is running.

## 3. After successful cutover

The active ClickHouse container now uses:

- `/var/lib/clickhouse` -> E: Hot bind path
- `/var/lib/clickhouse-cold` -> F: Cold bind path
- `/var/log/clickhouse-server` -> E: log bind path

The original Docker named volume remains intact as rollback media. Do not delete it during the stabilization period.

When explicitly recreating ClickHouse after cutover, include the Hot/Cold override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.hot-cold-storage.yml up -d --wait clickhouse
```

A plain `docker compose up` describes the original named-volume topology and must not be used to recreate ClickHouse after the Hot cutover.

## 4. Rollback principle

If the migration operator detects a post-stop/copy/activation error, it attempts an automatic return to the original named volume. If manual recovery is ever required, keep both the original named volume and E: copy untouched, stop writers, and restore ClickHouse with the default `docker-compose.yml` only.

Do not delete or empty either copy while diagnosing a failed cutover.

## 5. Cold tier remains a separate decision

This cutover only moves the existing authoritative ClickHouse data root to E: and registers F: as a Cold disk. It does **not** alter existing table storage policies or move any parts to F:.

Per-table/partition tiering is a later operation governed by the storage consumer/reconstructibility inventory and capacity evidence. Current serving tables must not be blindly moved or deleted.
