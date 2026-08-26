# Windows ClickHouse Hot/Cold storage

## Host layout

The dedicated Data Engine storage layout is:

- `E:` — 1.86 TB SSD — ClickHouse **Hot** data and ClickHouse logs.
- `F:` — 3.63 TB SATA — ClickHouse **Cold** data.

Recommended directories:

```text
E:/MarkOrbitData/hot/clickhouse
E:/MarkOrbitData/hot/clickhouse-logs
F:/MarkOrbitData/cold/clickhouse
```

Set these only when using `docker-compose.hot-cold-storage.yml`:

```text
CLICKHOUSE_HOT_DATA_PATH=E:/MarkOrbitData/hot/clickhouse
CLICKHOUSE_COLD_DATA_PATH=F:/MarkOrbitData/cold/clickhouse
CLICKHOUSE_LOG_PATH=E:/MarkOrbitData/hot/clickhouse-logs
```

The default `docker-compose.yml` remains on Docker-managed volumes. Merely merging this repository change does not move or restart any live data.

## ClickHouse policy

The opt-in profile mounts the SSD-backed path as ClickHouse's normal `default` disk and the SATA-backed path as a separate `cold` disk. `hot-cold-storage.xml` exposes a `hot_cold` policy with:

1. `hot` volume -> `default` disk (E: SSD)
2. `cold` volume -> `cold` disk (F: SATA)
3. `move_factor=0.10`

No existing table is changed to this policy by this change. A table must be explicitly configured later before ClickHouse can place/move its parts through the policy.

## Safe preflight

Create the three directories manually, set the environment variables, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-hot-cold-storage.ps1
```

The preflight only checks path separation/free-space metadata and renders Compose configuration. It does not start, stop, restart, recreate, copy, delete, move, optimize, or alter live data.

## Cutover boundary

Do **not** enable the Hot/Cold Compose override against empty E:/F: directories while the current ClickHouse named volume is still authoritative. That would start ClickHouse against a different data root and make the existing corpus appear absent.

The later physical cutover is a separate operation and requires all of the following first:

- confirm the CN live worker and any long import are stopped at a safe checkpoint;
- record the current ClickHouse volume/table/part state;
- stop writers and ClickHouse for the copy window;
- copy the authoritative current ClickHouse data root to E: without rebuilding or rescanning source packages;
- validate the copied ClickHouse metadata/data before switching the Compose override;
- mount F: as the empty Cold disk and verify `system.disks`/`system.storage_policies`;
- only then authorize per-table storage-policy changes or partition moves.

`2023_5.zip` has already been validated and is not part of this storage cutover validation. Do not rescan or re-import it solely because the disks changed.

## Intended first tiering targets

The storage consumer contract remains authoritative: current serving tables are not candidates for blind deletion or wholesale movement. Start Cold-tier work with proven redundant/reconstructible history or baseline data, then apply table/partition policy changes from measured capacity evidence.
