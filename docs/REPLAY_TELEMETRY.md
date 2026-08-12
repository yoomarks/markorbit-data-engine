# Replay Telemetry / Storage Growth Ledger

## Purpose

`DATA_ENGINE_REPLAY_TELEMETRY_V1` records operational evidence around real corpus replay commands without writing telemetry back into PostgreSQL or ClickHouse source-fact tables.

It is designed to answer practical questions after a large replay:

- which Git SHA and component versions ran;
- which domain/jurisdiction was mutated;
- when the command started and finished;
- how package status counts changed;
- how ClickHouse active bytes/rows changed;
- how stage bytes changed;
- how much free space changed on the Windows host volume;
- what ClickHouse reported for its internal disk before and after;
- which replay report was produced and its SHA-256, when a report exists.

Telemetry is **operational evidence**, not trademark source facts and not legal evidence about a trademark case.

## Recorded commands

Automatic ledger recording applies to the main mutating replay paths:

- `replay-cn-full.ps1`;
- `replay-us-deterministic.ps1 -Apply`;
- `replay-us-assignment-deterministic.ps1 -Apply`;
- `replay-us-ttab-deterministic.ps1 -Apply`.

US dry runs do not create ledger records because they do not mutate corpus facts.

Legacy one-shot/retry wrappers remain protected by domain and storage gates, but they are not the preferred telemetry-backed bulk replay path.

## Files

Per-run evidence:

```text
reports/replay_runs/<run_id>.start.json
reports/replay_runs/<run_id>.json
```

Append-only local ledger:

```text
reports/replay_ledger.jsonl
```

`reports/` is gitignored. The ledger is intentionally local operational state and must not be treated as a source-data artifact that belongs in Git history.

## Snapshot model

The Python collector `app.replay_telemetry` performs only SELECT queries against:

- PostgreSQL `control.source_package` for package status counts and latest successful package;
- ClickHouse `system.parts` for active/stage storage rows and bytes;
- ClickHouse `system.disks` for internal free/total space.

The PowerShell helper separately reads the Windows host volume through `System.IO.DriveInfo`.

The runtime snapshot also embeds the authoritative `component_versions()` matrix.

## Delta semantics

Deltas are **before/after observations**, not forecasts and not peak-usage measurements.

For example:

```text
clickhouse_active_bytes = end.active_bytes - start.active_bytes
host_free_space          = end.host_free_space - start.host_free_space
SUCCESS package delta    = end.SUCCESS - start.SUCCESS
```

A negative host-free-space delta means host free space decreased during the command. A positive ClickHouse-active-byte delta means active ClickHouse storage grew.

The system does **not** claim to measure peak temporary disk usage because it does not continuously sample during the replay.

## Failure semantics

Replay telemetry is best effort:

- start-snapshot failure emits a warning and does not block a replay that already passed its mandatory safety gates;
- end-snapshot/finalization failure emits a warning;
- telemetry must never replace or hide the original replay error;
- a failed replay still attempts to write an end record with `COMMAND_FAILED` and the original error text;
- the mandatory storage headroom and domain-transition gates remain authoritative safety gates and are not weakened by telemetry behavior.

## Source-fact boundary

Telemetry does not INSERT, UPDATE or DELETE PostgreSQL/ClickHouse source facts. Each final run record states:

```text
OBSERVED_ONLY_NOT_TELEMETRY_WRITTEN_TO_FACT_DATABASES
```

The ledger can be deleted without changing Data Engine source facts. Raw authority, current facts, true-delta history and formal acceptance reports remain governed by their existing contracts.
