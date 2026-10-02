# GB Domestic historical full resume (#855)

> **V1 PLANS r1/r2 ARE SUPERSEDED / DO NOT APPLY.** They point at the legacy D
> stage. D is `hot_cn` only. This V2 operator accepts only the independently
> audited E structured stage; GB structured/query data stays on E / `hot_global`.

This operator resumes the accepted UKIPO Domestic historical source-row ingest
after the independently verified first-1,000-row production pilot.

## Frozen boundaries

The source is the immutable 2018-era Domestic ZIP retained on F and its
byte-identical, independently accepted structured stage on E. The operator pins
the source audit, relocation plan, relocation receipt, independent E audit,
E-stage manifest and every accepted/quarantine JSONL hash. It rejects partial,
unexpected, symlinked or junction-backed stage state.

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

The frozen plan records only the E: reserve floor, not observed free bytes.
Observed free bytes are intentionally dynamic and may change between plan freeze
and Apply. Apply re-runs the live disk-reserve check, requires the same frozen
reserve floors and requires current free bytes to remain at or above them.
This keeps the authority plan deterministic without weakening the capacity gate.

The final expected counts are 1,188,992 physical source rows, 1,188,886
accepted rows and 106 quarantined malformed-width rows. Completion marks only
the historical source ingest run COMPLETE. It does not claim current UKIPO
register status.

## Production authority

Preflight and plan freeze are read-only. The E-stage reader and V2 continuation
must first be independently reviewed and merged. Only then may the final plan
be frozen from clean, latest main and its SHA reviewed. Any pre-merge output is
diagnostic only and cannot be presented as the production authority plan.
The operator enforces this in code by requiring a clean worktree whose HEAD
equals live `origin/main` at plan freeze and again immediately before Apply; the
frozen plan and receipt bind that exact 40-character execution commit.
Apply requires the exact post-merge token:

GO #855 GB-DOMESTIC-FULL-RESUME <plan-sha> CHECKPOINT-1000-TO-1188992

This token does not authorize Madrid-IR, weekly journals, E: logo binding,
API/ClickHouse serving cutover, current-register assertions or source cleanup.
