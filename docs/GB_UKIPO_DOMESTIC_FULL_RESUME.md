# GB Domestic historical full resume (#855)

This operator resumes the accepted UKIPO Domestic historical source-row ingest
after the independently verified first-1,000-row production pilot.

## Frozen boundaries

The source is the immutable 2018-era Domestic ZIP already staged and audited.
The production checkpoint must be exactly source ordinal 1,000 with 1,000
accepted rows, zero quarantine rows, no rows beyond the checkpoint and no
current-state assertions. The operator also pins the first-1,000 pilot receipt,
its independent row-for-row database audit, the merged pilot operator and the
source-stage manifest/JSONL hashes.

The continuation target is exact source ordinal 1,188,992. All remaining
accepted and quarantined physical rows are merged in original source order.
Rows are committed in 5,000-row transactions with an atomic checkpoint update.
A duplicate/partial insert, checkpoint mismatch, unexpected current-state flag,
disk-reserve failure or source-evidence drift fails closed. A crash can only
leave the last committed checkpoint; a new frozen plan is then required before
resume.

The final expected counts are 1,188,992 physical source rows, 1,188,886
accepted rows and 106 quarantined malformed-width rows. Completion marks only
the historical source ingest run COMPLETE. It does not claim current UKIPO
register status.

## Production authority

Preflight and plan freeze are read-only. After the code is merged, freeze the
plan from clean merged main and review its SHA. Apply requires the exact token:

GO #855 GB-DOMESTIC-FULL-RESUME <plan-sha> CHECKPOINT-1000-TO-1188992

This token does not authorize Madrid-IR, weekly journals, E: logo binding,
API/ClickHouse serving cutover, current-register assertions or source cleanup.
