# M1.6 Guarded CN Execution

Status: ENFORCED INGESTION ENTRY POLICY

M1.6 separates clean-replay bootstrap from registered replay continuation so a CN ingestion cycle cannot silently bypass the preflight and replay-plan safety gates.

## Manual one-shot path

`run-cn.ps1` executes:

```text
python -m app.cn.guarded_run_once
```

### First run after clean reset

When the CN package registry is empty, the execution guard requires:

1. all incoming CN source files to have known M1.6 package precedence;
2. ZIP-only M1.6 source inputs;
3. no ambiguous same-partition incoming filenames;
4. real-data preflight to permit replay;
5. preflight mode `CLEAN_RESET_READY_FOR_REPLAY`;
6. deterministic replay plan to pass.

Only then does the process call the existing scanner. That scanner registers all incoming packages in deterministic lexical order and the ingestion layer processes one package according to frozen source rank.

### Continuation runs

After the first scan, package registry rows freeze source rank. Later one-shot runs do not re-hash the entire corpus merely to re-create the clean plan. They still require:

- current engine marker `M1.6`;
- incoming-file precedence policy to remain unambiguous;
- no unresolved `FAILED` / `MISSING_FILE` package;
- required M1.6 durable-goods schema;
- M1.6 replay boundary (`cn_case_scope_current` cannot exist with an empty durable item store).

The ingestion path then independently acquires the PostgreSQL advisory lock and verifies the target source package SHA before publication.

## Retry barrier

Normal replay selects `REGISTERED` / `INTERRUPTED` packages. A `FAILED` or `MISSING_FILE` package therefore cannot be silently skipped while replay advances to a later source-rank package.

If either status exists, the execution guard enters:

```text
RETRY_REQUIRED
```

and blocks manual run, persistent worker continuation, and guarded API scan/run. The operator must first use:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-cn.ps1
```

The retry path selects failed/interrupted/missing registered packages in source-rank order, verifies the registered source SHA again, and re-applies the M1.6 schema/replay boundary inside `ingest_m16`.

If more than one package requires repair, normal replay remains blocked until the retry barrier is clear.

## Persistent worker

The persistent worker evaluates the same execution guard before every scheduled cycle.

If the guard reports `CLEAN_RESET_FIRST_RUN`, the worker deliberately does **not** bootstrap replay. It logs that manual first-run approval is required. This prevents accidentally starting a full clean replay merely by starting the worker service.

Once packages have been registered by the explicit manual bootstrap and no retry barrier is active, the worker may continue through `REGISTERED_REPLAY_CONTINUATION` mode.

## API mutation boundary

The API endpoints:

```text
POST /api/jobs/cn/scan
POST /api/jobs/cn/run
```

must pass the same M1.6 execution guard. They are allowed only in `REGISTERED_REPLAY_CONTINUATION` mode.

A clean reset cannot be bootstrapped through the API even when preflight and replay planning would otherwise pass. The API returns HTTP 409 with `CN_CLEAN_REPLAY_MANUAL_BOOTSTRAP_REQUIRED` and instructs the operator to use `scripts/run-cn.ps1`, because that wrapper also verifies the persistent worker is stopped.

A blocked guard (including `RETRY_REQUIRED`) returns HTTP 409. A guard infrastructure/database failure returns HTTP 503.

`POST /api/jobs/cn/retry` remains the explicit repair path for already registered failed/missing packages; it does not scan/register new incoming packages.

## Input policy

Before scanner registration, M1.6 rejects:

- `.xml` / `.gz` CN source inputs even though the generic scanner can discover those suffixes;
- ZIP filenames that cannot be classified into a known source-precedence contract;
- multiple incoming filenames representing the same filing-year/update-month partition;
- a new filename for a partition that is already frozen in the registry.

A same-partition revision must be handled by an explicit revision policy in a future model; it is never resolved implicitly by filename order.

## Failure behavior

A blocked guard exits before `scan_and_ingest_cn` or `scan_cn_incoming` is called. No new package is registered and no fact is published by that blocked cycle.

The persistent worker remains resilient: a blocked scheduled cycle is logged and retried on the next interval only after the operator fixes the input/state issue.
