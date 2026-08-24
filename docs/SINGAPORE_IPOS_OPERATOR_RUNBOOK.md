# Singapore IPOS Authenticated Operator Runbook

## Purpose

This runbook defines the production-facing operator boundary for the Singapore IPOS current-snapshot source. It is intentionally separate from the root M1.7 release promotion gate and from the sovereign CN M1.6 replay.

The authenticated operator run is not a scheduler. Recurring production acquisition remains disabled until a real authenticated operator run passes on the target host and its retained state/report are reviewed.

## Secret boundary

`DATA_GOV_SG_API_KEY` is supplied only through the process environment.

The key must never be:

- committed to the repository;
- written into snapshot manifests, delta/native evidence, acceptance reports, or operator reports;
- forwarded to signed object-storage download URLs;
- embedded in command-line arguments that may be retained in shell history.

The data.gov.sg API key is attached only to data.gov.sg API requests. Whole-dataset signed storage requests are deliberately unauthenticated with respect to that key.

## Explicit task DAG

The operator is registered as `IPOS_SG_OPERATOR_DAG_V1` in the M1.7 platform contract. Its native task order is:

1. `STATE_PREFLIGHT`;
2. `RESOURCE_PREFLIGHT`;
3. `LIVE_SOURCE_AUTHENTICATION`;
4. `FULL_CORPUS_LIFECYCLE`;
5. `STATE_POSTFLIGHT`;
6. `ACCEPTANCE_RECEIPT`.

The operator records the DAG version and completed task sequence in success/failure evidence. Dependency order is checked with the generic M1.7 `WorkDagDefinition`; recurring scheduling remains explicitly disabled in the task contract.

## One operator run

`./scripts/run-ipos-sg.ps1` launches one Docker Compose one-shot worker and keeps that same worker alive across the complete operator chain:

1. acquire the state-directory operator lease;
2. run a fast read-only lifecycle-state preflight;
3. verify filesystem headroom before any provider request;
4. authenticate the live datastore source and validate the authoritative 39-field contract;
5. record the live datastore total row count as a full-corpus consistency reference;
6. perform one logical authenticated whole-dataset initiate/poll sequence as part of the real full-corpus acquisition;
7. stream the current CSV into the lifecycle without buffering the corpus in memory;
8. validate critical and complete source schema before acceptance;
9. compare the candidate manifest row count with the authenticated live total before any candidate snapshot/pointer persistence;
10. verify retained physical snapshot integrity before unchanged/changed delta handling;
11. commit snapshot manifest/current pointer and any required generic/native evidence;
12. retry superseded full-snapshot cleanup;
13. write the full-corpus acceptance report atomically;
14. run a strict post-commit state audit;
15. write a combined operator acceptance report;
16. release the state-directory operator lease.

Keeping all phases in one worker removes the gap that previously existed between a live-source probe and a second full-corpus container. Other guarded worker operations can observe that the worker service is occupied for the complete Singapore run.

The live-source probe intentionally does **not** request a whole-dataset export URL. The full-corpus downloader owns the authenticated materialization request, avoiding duplicate 3+ GB export initiations. Transient data.gov.sg control-plane failures (network errors, HTTP 429, and HTTP 5xx) use bounded exponential retries. Non-transient HTTP failures fail immediately. Signed object-storage streaming remains a single transfer attempt rather than inventing unverified byte-range resume semantics.

## State audit

`./scripts/check-ipos-sg.ps1` performs a fast read-only audit and does not hash or scan the multi-gigabyte CSV body.

Possible statuses:

- `EMPTY`: no accepted state exists; safe for first bootstrap.
- `READY`: the current pointer, canonical manifest, and retained full snapshot metadata are internally consistent and there is no cleanup/transient residue.
- `RECOVERABLE`: no accepted-state metadata corruption exists, but retryable residue such as a superseded full CSV, orphan pre-pointer snapshot, or `.part` file remains. A lifecycle run may recover it.
- `BLOCKED`: accepted-current metadata integrity is invalid. Network acquisition must not start until the state is reviewed.

`READY` is intentionally a fast metadata-state result, not a fresh checksum of the multi-gigabyte retained CSV. Physical content integrity is enforced by the lifecycle before it may rely on retained bytes for an unchanged or changed cycle.

The postflight gate is stricter than the preflight gate: a successful authenticated operator run must end in `READY`, not merely `RECOVERABLE`.

## Physical snapshot integrity

Every accepted manifest identifies its full CSV by SHA-256. Before the lifecycle treats retained current bytes as authoritative evidence, it hashes that physical CSV and compares the result with the accepted manifest identity.

- If the newly acquired corpus has the same content identity and the retained current CSV is intact, the cycle returns `UNCHANGED`.
- If the newly acquired corpus has the same content identity but the retained current CSV is corrupted or truncated, the fresh authoritative bytes replace only the damaged retained file and the lifecycle returns `REPAIRED`. No synthetic create/update/delete or native-family evidence is emitted.
- If the provider corpus has changed while the retained prior CSV no longer matches its accepted SHA-256, the lifecycle fails closed before delta generation. The fresh candidate cannot reconstruct the lost prior evidence, so `current.json` is not advanced and no delta/native evidence is written.
- An orphan pre-pointer snapshot may be reused only when its persisted manifest identity agrees with the fresh candidate and its physical CSV hashes to that identity.

`REPAIRED` is an integrity-recovery signal, not a normal production-scheduling success condition. Preserve the operator report, investigate the cause of the physical corruption, and perform a later clean controlled cycle before considering recurring scheduling.

## Storage headroom

The full-corpus lifecycle keeps the previously accepted full snapshot while the next corpus is downloaded and compared. `IPOS_SG_STORAGE_PREFLIGHT_V1` therefore runs before network activity.

The default minimum is the greater of:

- 8 GiB free space; or
- twice the size of the largest retained full snapshot.

Insufficient headroom fails before the live provider probe or whole-dataset materialization request. The calculated free/required byte counts are included in the operator success report; a failure is recorded at the `RESOURCE_PREFLIGHT` task.

## Live/export corpus consistency

A syntactically valid CSV is not enough evidence that a multi-gigabyte transfer is complete. The authenticated live datastore probe returns `total_rows`; the full-corpus lifecycle carries that value into a pre-commit candidate validator.

The default drift tolerance is the greater of:

- 1,000 rows; or
- 0.5% of the authenticated live total.

This tolerance permits normal source movement between the lightweight live query and asynchronous export materialization while rejecting materially truncated or wrong-corpus downloads. A mismatch fails before `_persist_version` and before `current.json` can advance. The unaccepted incoming file is removed, and an existing accepted snapshot remains unchanged.

The full-corpus acceptance report records `live_total_rows`, `live_row_count_delta`, and `allowed_live_row_drift` so this gate is auditable rather than implicit.

## Lease and interruption recovery

The state directory contains `.operator.lock` while an authenticated Singapore run is active. A second Singapore run fails closed rather than overlapping the first.

Normal exits and handled failures remove the lock. An abrupt host/container termination may leave it behind. Automatic stale deletion is intentionally forbidden. `-RecoverStaleLock` may recover the lease only when the recorded lock is at least 12 hours old; malformed or younger locks remain blocking.

Accepted-current pointer publication remains the lifecycle commit boundary. If a later post-commit check fails, the already accepted source state is not silently rolled back. Failure evidence is retained under `acceptance/operator_failure_latest.json` for review and rerun.

## Durable evidence

The default state directory is `raw_data/ipos_sg`, which is ignored by Git.

Important files include:

- `current.json` — accepted-current pointer;
- `snapshots/<sha256>.csv` — the one retained full current snapshot;
- `snapshots/<sha256>.manifest.json` — durable source/provenance manifest;
- `events/*.jsonl` — generic create/update/delete evidence for changed snapshots;
- `native_changes/*.jsonl` — neutral IPOS source-family evidence for updates;
- `acceptance/latest.json` — strict full-corpus acceptance evidence, including live/export row consistency;
- `acceptance/operator_latest.json` — combined task/state/resource/source/corpus/postflight success receipt;
- `acceptance/operator_failure_latest.json` — last handled operator failure, including task progress and with the configured API key redacted from the error string.

## Production scheduling gate

Do not enable a recurring acquisition schedule merely because code/CI is green. Scheduling is a separate operational activation step and requires all of the following:

1. authenticated source probe passes on the target host;
2. full-corpus lifecycle passes on the target host;
3. live/export row consistency passes and is recorded;
4. postflight state is `READY`;
5. storage headroom evidence is acceptable for sustained operation;
6. the operator success receipt contains no credential material;
7. a second controlled run proves normal retained-state behavior with `UNCHANGED` or a valid `CHANGED` cycle; `REPAIRED` requires integrity incident review and another clean controlled cycle;
8. runtime duration, disk usage, evidence growth, and provider rate-limit behavior are reviewed;
9. overlap policy with CN/US and other worker jobs is approved;
10. failure/retry alerting and stale-lock recovery ownership are assigned.

Until those gates are complete, Singapore acquisition remains explicit operator-driven execution.
