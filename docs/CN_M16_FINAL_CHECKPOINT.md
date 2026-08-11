# CN M1.6 Final Checkpoint

`CN_M16_FINAL_CHECKPOINT_V1` is the read-only gate between completion of the China M1.6 corpus replay and any later domain replay or final four-domain acceptance.

## Order of checks

1. Run the CN replay readiness core.
2. Require readiness status `COMPLETE` before running the expensive integrity acceptance scan.
3. Require Storage V2 invariants already enforced by readiness:
   - no `FIRST_OBSERVED` / `REOBSERVED` goods baseline history;
   - no reconstructible baseline-only observed events;
   - no legacy party relation history rows;
   - no active CN stage rows between packages;
   - no Storage V2 shadow tables;
   - no pending ClickHouse mutations;
   - required M1.6 schema present.
4. Run the existing `CN_M16_ACCEPTANCE_INTEGRITY` audit only after the replay is complete.
5. Return `PASS` or `PASS_WITH_WARNINGS` only when both the replay/storage gate and the integrity acceptance pass.

## Status semantics

- `NOT_READY`: replay still has registered/interrupted work but no hard blocker.
- `BLOCKED`: readiness found a hard issue or retry-required package state.
- `FAIL`: replay/storage readiness was complete, but integrity acceptance failed or no acceptance report was available.
- `PASS_WITH_WARNINGS`: integrity acceptance passed under its source-backed incomplete-record warning policy.
- `PASS`: replay, Storage V2, and integrity acceptance all passed without warnings.

`ready_for_next_domain` is true only for `PASS` and `PASS_WITH_WARNINGS`.

## Operator entrypoint

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-cn-final-checkpoint.ps1
```

The wrapper requires PostgreSQL and ClickHouse to be running and refuses to run while the persistent worker is active. It uses `docker compose run --rm --no-deps` and mounts the checked-out `app` directory read-only so it does not rebuild or start the persistent worker.

The generated JSON report is written under `reports/cn_m16_final_checkpoint_<timestamp>.json` unless `-OutputPath` is supplied.

## Safety boundary

The checkpoint performs database reads only. It does not recover packages, acquire the ingestion advisory lock, change package status, clean stage tables, run replay, mutate ClickHouse/PostgreSQL, run `OPTIMIZE`, compact VHDX files, or start any US replay.
