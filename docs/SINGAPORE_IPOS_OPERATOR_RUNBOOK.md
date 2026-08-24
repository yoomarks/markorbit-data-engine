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

## One operator run

`./scripts/run-ipos-sg.ps1` launches one Docker Compose one-shot worker and keeps that same worker alive across the complete operator chain:

1. acquire the state-directory operator lease;
2. run a fast read-only lifecycle-state preflight;
3. authenticate the live datastore source and validate the authoritative 39-field contract;
4. perform exactly one authenticated whole-dataset initiate/poll sequence as part of the real full-corpus acquisition;
5. stream the current CSV into the lifecycle without buffering the corpus in memory;
6. validate critical and complete source schema before acceptance;
7. commit snapshot manifest/current pointer and any required generic/native evidence;
8. retry superseded full-snapshot cleanup;
9. write the full-corpus acceptance report atomically;
10. run a strict post-commit state audit;
11. write a combined operator acceptance report;
12. release the state-directory operator lease.

Keeping all phases in one worker removes the gap that previously existed between a live-source probe and a second full-corpus container. Other guarded worker operations can observe that the worker service is occupied for the complete Singapore run.

The live-source probe intentionally does **not** request a whole-dataset export URL. The full-corpus downloader owns the single authenticated materialization request, avoiding duplicate 3+ GB export initiations.

## State audit

`./scripts/check-ipos-sg.ps1` performs a fast read-only audit and does not hash or scan the multi-gigabyte CSV body.

Possible statuses:

- `EMPTY`: no accepted state exists; safe for first bootstrap.
- `READY`: the current pointer, canonical manifest, and retained full snapshot are internally consistent and there is no cleanup/transient residue.
- `RECOVERABLE`: no accepted-state corruption exists, but retryable residue such as a superseded full CSV, orphan pre-pointer snapshot, or `.part` file remains. A lifecycle run may recover it.
- `BLOCKED`: accepted-current integrity is invalid. Network acquisition must not start until the state is reviewed.

The postflight gate is stricter than the preflight gate: a successful authenticated operator run must end in `READY`, not merely `RECOVERABLE`.

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
- `acceptance/latest.json` — strict full-corpus acceptance evidence;
- `acceptance/operator_latest.json` — combined preflight/source/corpus/postflight success receipt;
- `acceptance/operator_failure_latest.json` — last handled operator failure, with the configured API key redacted from the error string.

## Production scheduling gate

Do not enable a recurring acquisition schedule merely because code/CI is green. Scheduling is a separate operational activation step and requires all of the following:

1. authenticated source probe passes on the target host;
2. full-corpus lifecycle passes on the target host;
3. postflight state is `READY`;
4. the operator success receipt contains no credential material;
5. a second controlled run proves `UNCHANGED` or `CHANGED` behavior against the retained current state;
6. runtime duration, disk usage, evidence growth, and provider rate-limit behavior are reviewed;
7. overlap policy with CN/US and other worker jobs is approved;
8. failure/retry alerting and stale-lock recovery ownership are assigned.

Until those gates are complete, Singapore acquisition remains explicit operator-driven execution.
