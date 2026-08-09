# M1.6 Real-Data Preflight

Status: NON-DESTRUCTIVE SAFETY GATE

The M1.6 real-data preflight is the required safety check before replaying authoritative CN ZIP packages or running downstream case-status inference audits on a loaded database.

It does **not** reset databases, change package status, start the persistent worker, publish facts, or run ingestion.

## Run

Keep the persistent worker stopped. PostgreSQL and ClickHouse must already be running.

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\preflight-m16-real-data.ps1
```

The script rebuilds the current worker image without starting it, runs the read-only preflight in a one-shot container, and writes a JSON report under:

```text
reports\m16_real_data_preflight_<timestamp>.json
```

## What it checks

### Runtime and databases

- repository/runtime engine marker is `M1.6`;
- PostgreSQL is reachable;
- ClickHouse is reachable;
- required M1.6 durable-goods columns exist.

### Ingestion exclusivity

- the CN advisory ingestion lock is available;
- no CN source package remains `PROCESSING`;
- no CN ingestion job remains `RUNNING`.

The PowerShell wrapper separately refuses to run while the persistent worker service is running.

### Authoritative source files

For every registered CN package, the preflight searches the mounted raw-data tree for an authoritative file and verifies the registered SHA-256. A file with the correct filename but the wrong hash is a hard failure.

It also inventories CN ZIP files currently visible under `incoming/cn` and `archive/cn`, classifies base partitions versus monthly patches from their filenames, and reports unknown filename patterns.

A temporary copy of the same package in both incoming and archive can occur during a clean replay preparation. That is a warning, not automatic corruption; registered packages still have to pass SHA-256 verification.

### M1.6 replay boundary

The preflight rejects the unsafe mixed-model state where:

```text
cn_case_scope_current has rows
but
cn_goods_item_current is empty
```

That state means M1.5 class aggregates exist without the durable item universe required by M1.6 and must not accept new monthly packages.

It also rejects durable goods items with no lifecycle scope rows.

## Modes

The result identifies the current operating mode.

### `CLEAN_RESET_READY_FOR_REPLAY`

Typical immediately after `reset-m16.ps1`:

- package registry is empty;
- current fact/item tables are empty;
- authoritative ZIP files are queued in `incoming/cn`.

This mode may return `PASS_WITH_WARNINGS` because there is no successful monthly coverage clock yet and archive/incoming may temporarily contain duplicate copies.

### `PARTIAL_OR_PENDING_REPLAY`

Some packages are registered but the complete M1.6 durable-data state has not yet been reached. Review warnings and package status before continuing.

### `M16_DATA_PRESENT_STABLE_SNAPSHOT`

Successful packages and durable M1.6 goods state are present. If a successful monthly coverage date also exists, the result may allow the historical inference audit.

## Output gates

The JSON exposes two explicit decisions:

- `safe_to_run_replay_command`
- `safe_to_run_inference_audit`

A hard failure always makes both unsafe. The inference audit additionally requires a successful monthly source coverage clock plus durable goods/lifecycle data.

Warnings never become silent success: they remain listed in `warning_reasons` for operator review.

## Hard failures

Examples include:

- wrong engine version;
- PostgreSQL or ClickHouse unavailable;
- incomplete M1.6 schema;
- busy ingestion lock;
- `PROCESSING` packages or `RUNNING` CN ingestion jobs;
- registered source ZIP missing;
- registered SHA-256 mismatch;
- M1.5 scope rows without M1.6 durable goods items;
- durable goods items without lifecycle scopes;
- no raw package source available for a clean replay.

If the PowerShell script reports a hard failure, do not run `run-cn.ps1` or `retry-cn.ps1` until the cause is resolved.
