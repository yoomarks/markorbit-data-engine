# GB historical source-row PostgreSQL pilot (#855)

> **Storage-topology supersession:** new Apply authority from this D-resident
> stage is disabled. The accepted first-1,000 Domestic pilot remains valid
> historical evidence, but any further database admission must first consume an
> independently accepted E-resident structured stage.

This follow-on operator is intentionally separate from the accepted UKIPO
source staging in PR #856. It consumes only the independently audited
Domestic or Madrid-IR stage under D:\yoomarks\governed-plans\855 and never
claims that the 2018-era source status is current UK registry truth.

The pilot creates only additive trademark_gb tables dedicated to immutable
physical source rows. Identity is the frozen source ZIP SHA plus physical
source-row ordinal, not application number, so co-owner rows and legacy
A/B/AA/AB suffixes cannot overwrite each other. Quarantined malformed
physical rows occupy their original ordinal with record_kind=QUARANTINED.
Accepted rows retain applicant/status text, Nice-class flags, source-cell
fingerprint and the complete staged source payload.

The first Apply is limited to exactly the first 1,000 original physical rows
of one reviewed source stream. Accepted and quarantine JSONL are merged by
source ordinal, every ordinal must be contiguous, and commits/checkpoints
advance in bounded 100-row batches. The operator refuses duplicate or partial
row inserts, unexpected database name, stage/audit/operator/schema drift,
insufficient D:/E: disk reserve, or replay after an immutable success receipt.

Preflight and plan freezing do not connect to PostgreSQL. Apply requires an
exact frozen plan SHA and the token:

GO #855 GB-SOURCE-ROW-PILOT <plan-sha> <DOMESTIC|MADRID_IR> FIRST-1000-ONLY

The token authorizes only the bounded historical-row pilot. It does not
authorize the remaining ~1.3M source rows, UKIPO journal ingestion, API
activation, current-state assertions, ClickHouse serving, source cleanup or
changes to E: logo evidence. Full ingestion needs a separate post-pilot
residency/currentness audit and separately frozen authority.
