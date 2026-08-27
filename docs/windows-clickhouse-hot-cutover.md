# Windows ClickHouse named-volume to E: Hot cutover

This runbook moves the authoritative ClickHouse data root from the current Docker-managed named volume to the dedicated E: SSD Hot path. It does not rebuild, re-import, or revalidate source packages.

## Safety model

The operator is deliberately fail-closed:

- the current `/var/lib/clickhouse` named volume is discovered from the actual Compose container, never guessed;
- readiness blocks while any `control.job_run` is `RUNNING` or any CN source package is `PROCESSING`;
- the E: Hot target must be empty and have the measured source-volume size plus 128 GiB reserve by default;
- source size is measured from a disposable container with the source volume mounted read-only;
- ClickHouse table/row/part/byte baseline comes only from `system.parts` metadata;
- migration execution requires the explicit `-Execute` switch;
- writers are stopped first and control state is checked again before ClickHouse is stopped;
- before ClickHouse is stopped, the actual Docker bind is probed for mkdir/rename, hardlink, symlink, ownership/mode, ordinary I/O, and **two distinct files whose names differ only by case**;
- if the Hot bind cannot preserve case-distinct paths, cutover fails while the authoritative ClickHouse is still running;
- the source named volume is mounted read-only for the copy and is never deleted;
- after Hot activation, the merge-stable logical guards `active_table_count` and `active_rows` must match the pre-cutover baseline; active part count and bytes remain observations because background MergeTree merges can change them without changing logical data;
- the `cold` disk must be registered after activation;
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

This may take some time because it measures the source directory size through filesystem metadata. It does not read trademark rows or alter the source volume.

Do not proceed unless `safe_to_cutover` is `true`. Keep the JSON as the pre-cutover receipt.

## 2. Windows Hot path must preserve Linux case semantics

ClickHouse's Linux data root can contain paths that differ only by letter case. A default Windows NTFS directory is case-insensitive, so copying such a tree can collapse two legal Linux paths into one Windows path. The operator therefore proves case-distinct file behavior through the **actual Docker bind mount before stopping ClickHouse**.

For a new Windows Hot directory, enable per-directory case sensitivity while that directory is still empty. Run these commands from an elevated PowerShell session:

```powershell
New-Item -ItemType Directory -Path E:\MarkOrbitData\hot\clickhouse-cs -Force
fsutil file setCaseSensitiveInfo E:\MarkOrbitData\hot\clickhouse-cs enable
fsutil file queryCaseSensitiveInfo E:\MarkOrbitData\hot\clickhouse-cs
```

Use this new directory as `-HotPath`. The host `fsutil` flag is necessary but not sufficient: the migration operator's container-level bind probe remains the authoritative gate.

### Recovery after a case-collision copy failure

If a stopped-volume copy has already failed with a message such as:

```text
cp: cannot create regular file '/target/...': File exists
```

and the Hot directory is now non-empty:

1. **Do not delete, empty, rename into service, or reuse that partial Hot copy.** Keep it as failure evidence until rollback/source state has been recorded and a later cleanup is explicitly approved.
2. Confirm the original ClickHouse container has been restored against the original named volume before any new cutover attempt.
3. Create a **new empty** Hot directory such as `E:\MarkOrbitData\hot\clickhouse-cs`.
4. Enable and query per-directory case sensitivity on that new empty directory with `fsutil` as shown above.
5. Re-run readiness against the new Hot path.
6. Run the migration operator against the new Hot path. Its case-distinct Docker bind probe runs before `docker compose stop clickhouse`; if Docker Desktop does not preserve the required semantics, the operator fails closed before the database is stopped.

Never enable case sensitivity by mutating a partial ClickHouse copy in place, and never use the failed partial copy as a source of truth.

## 3. Approved cutover window

Only after the readiness receipt has been reviewed, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\migrate-clickhouse-volume-to-hot.ps1 `
  -HotPath E:\MarkOrbitData\hot\clickhouse-cs `
  -ColdPath F:\MarkOrbitData\cold\clickhouse `
  -LogPath E:\MarkOrbitData\hot\clickhouse-logs `
  -Execute
```

The operator captures which API/worker services were running, stops only those services, performs a second task/package check, probes E:/F: bind-path filesystem semantics including case-distinct names, stops ClickHouse only after that probe passes, copies the stopped named volume to E:, verifies the structural manifest, activates the Hot/Cold Compose profile, compares the logical metadata guards, verifies the `cold` disk, and restarts the services that were running before the cutover.

For a corpus of hundreds of GB, the copy window can be long. Do not terminate Docker Desktop or the host while the copy is running.

## 4. After successful cutover

The active ClickHouse container now uses:

- `/var/lib/clickhouse` -> E: Hot bind path
- `/var/lib/clickhouse-cold` -> F: Cold bind path
- `/var/log/clickhouse-server` -> E: log bind path

The original Docker named volume remains intact as rollback media. Do not delete it during the stabilization period.

A successful receipt includes both:

```text
bind_filesystem_capabilities_verified = true
bind_case_sensitive_semantics_verified = true
```

When explicitly recreating ClickHouse after cutover, include the Hot/Cold override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.hot-cold-storage.yml up -d --wait clickhouse
```

A plain `docker compose up` describes the original named-volume topology and must not be used to recreate ClickHouse after the Hot cutover.

## 5. Rollback principle

If the migration operator detects a post-stop/copy/activation error, it attempts an automatic return to the original named volume. If manual recovery is ever required, keep both the original named volume and E: copy untouched, stop writers, and restore ClickHouse with the default `docker-compose.yml` only.

Do not delete or empty either copy while diagnosing a failed cutover.

## 6. Cold tier remains a separate decision

This cutover only moves the existing authoritative ClickHouse data root to E: and registers F: as a Cold disk. It does **not** alter existing table storage policies or move any parts to F:.

Per-table/partition tiering is a later operation governed by the storage consumer/reconstructibility inventory and capacity evidence. Current serving tables must not be blindly moved or deleted.
