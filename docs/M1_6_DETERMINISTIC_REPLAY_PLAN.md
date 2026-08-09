# M1.6 Deterministic Clean Replay Plan

Status: READ-ONLY CLEAN-REPLAY PLANNER

After a clean M1.6 reset and real-data preflight, generate a replay plan before running any CN ingestion:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\plan-m16-replay.ps1
```

The planner does not register packages, change package status, publish facts, or start the persistent worker. It is valid only when the preflight mode is `CLEAN_RESET_READY_FOR_REPLAY`.

## Two orders

The report records both:

- `scanner_registration_order` — lexical filename order used by the current package scanner;
- `expected_processing_order` — order produced by the same `PackageDescriptor.source_rank` contract used at registration.

Base partitions remain lower precedence than monthly patches. Monthly patches are processed by semantic `YYYYMM` sequence even when lexical filename order differs, e.g. `2023_10.zip` sorts before `2023_2.zip` lexically but February has the earlier monthly source sequence.

## Hard failures

The planner refuses to produce an executable-safe plan when:

- real-data preflight fails or does not permit replay;
- the database is not in clean-reset mode;
- no incoming CN ZIP exists;
- a filename has unknown package precedence;
- two incoming files have identical SHA-256 content;
- multiple different ZIPs represent the same semantic filing-year/update-month partition.

The last rule is intentional: a same-partition revision requires explicit official revision evidence. Filename order is not allowed to silently decide which revision wins.

## Output

The JSON report is written under:

```text
reports\m16_replay_plan_<timestamp>.json
```

Review `expected_processing_order` before the first `run-cn.ps1`. The planner itself never performs replay.
