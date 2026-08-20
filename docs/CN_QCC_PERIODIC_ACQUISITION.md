# CN QCC Periodic Acquisition

The CN QCC enrichment pipeline is opt-in and isolated from the existing long-running CN/contact worker.

## Runtime contract

Set `CN_QCC_ACQUISITION_ENABLED=true` only when the QCC collector workflow is ready for production use. Defaults remain fail-safe:

- capacity: `500` companies per bounded batch;
- refresh horizon: `180` days;
- cycle interval: `3600` seconds;
- outgoing tasks: `${RAW_DATA_ROOT}/outgoing/cn_qcc/<batch_key>.tasks.csv`;
- returned results: `${RAW_DATA_ROOT}/incoming/cn_qcc/<batch_key>.result.csv`.

A cycle is idempotent and performs at most one external-boundary transition:

1. no open batch -> plan and export one bounded batch;
2. planned batch -> export it;
3. exported batch without a matching result file -> wait without changing data;
4. matching result file present -> ingest it transactionally;
5. zero-task batch -> complete it without export and advance the durable scan cursor.

A wrong-company successful result remains fail-closed and rolls back the transaction.

## Operator commands

Readiness can be inspected without mutating the acquisition state:

```powershell
docker compose run --rm --no-deps worker python -m app.cn_qcc.cli state
```

Run one cycle manually:

```powershell
docker compose run --rm --no-deps worker python -m app.cn_qcc.cli cycle
```

For continuous isolated operation, enable the profile after configuration:

```powershell
docker compose --profile qcc up -d qcc-acquisition
```

The QCC acquisition service is separate from `worker`; starting, stopping, or rebuilding it does not require recreating the existing long-running worker.

## Collector hand-off

When `state.readiness` is `WAITING_RESULT`, send the exported `.tasks.csv` file to the collector. Return exactly one CSV using the `result_expected_path` reported by `state`. The next cycle will detect and ingest it.

Do not rename an exported batch or alter task/entity IDs. Identity validation, task membership, content snapshots, Contact Hub writes, and planner cursor updates are committed in the same PostgreSQL transaction.
