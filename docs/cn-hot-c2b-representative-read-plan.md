# CN Hot C2b representative-read plan (#843, parent #837)

The accepted exact-key canary proved full route-response parity for only `10002014` and the
separate literal-quoted `"10002014"` key. Its receipt status is
`READ_ONLY_EXACT_KEY_PARITY_ACCEPTED_NOT_SERVING`; it does not justify route activation.

`scripts/freeze_cn_hot_c2b_representative_read_plan.py` defines the next plan-only gate. It binds
the accepted prior plan and exact-key receipt by SHA-256, pins the reviewed route and canary code,
and freezes five lexical route intervals covering the quote sentinel, three numeric intervals and
the `G` Madrid-designation family. Each future request uses the real preliminary-publication route,
one page of at most 50 results, a single thread, bounded rows/bytes/memory/time and serial execution.

The future read-only run must record warm-up and measured query statistics, keep the CN serving
epoch unchanged around every source/target pair, and prove complete route-response equality,
including query identity, snapshot, cursor and provenance. Every interval must be non-empty; any
empty range, mismatch, limit overflow, timeout, epoch drift or backend error fails closed.

This change freezes no production plan and performs no production query. After merge, a clean
authoritative-main run may create an immutable plan. Executing that plan requires its exact SHA
token in this form:

`GO #843 CN-HOT-REPRESENTATIVE-READ <plan-sha256> BOUNDED-RANGES-NO-SERVING`

Even a successful future run leaves the source as the only serving endpoint. The read canary has
no serving mutation to roll back: failure means `KEEP_SOURCE_ONLY`. A later per-route activation
needs a separate reviewed implementation, current source health/epoch proof, explicit rollback and
separate authority. Global endpoint changes, API or worker startup, writer cutover, merges,
`OPTIMIZE`, source reclamation, and Docker/WSL/VHDX operations remain prohibited.
