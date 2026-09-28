# CN Hot C2b follow-on plans (#843, parent #837)

## Evidence and current state

R9 six-table migration is accepted. PR #844 added the independent, fail-closed
C2b readiness gate. The separate follow-on plan-only operator is
scripts/freeze_cn_hot_c2b_next_plans.py. It re-runs the frozen auditor, checks
the source PostgreSQL CN serving epoch before and after two bounded, exact-key
source/target probes, and writes a new, immutable plan-only JSON receipt.

Accepted read-only plan receipt:
D:\yoomarks\governed-plans\843\cn-hot-c2b-next-plans-r2.json
SHA-256: bc230462d12748240d08318f6b5066281303165cffd4833abe721de166bfddd9.
Status: FROZEN_READ_ONLY_PLANS_NO_APPLY.

No API or writer was started; no target FINAL query, START MERGES, OPTIMIZE,
VHDX/Docker operation or source deletion was performed by the planner.

## Read-canary proposal (separate future authority)

Only the preliminary-publication discovery GET currently depends exclusively
on an already migrated target table, cn_case_current. The existing source API
remains the serving endpoint. The plan freezes two distinct exact original
application-number keys, 10002014 and "10002014" (literal quote characters).
They must never be silently normalized together.

The raw source has two versions of the unquoted key; the accepted target has
the same latest source version only. Both sides also retain the separate
quoted key. These are narrow non-FINAL point-read proofs, not an API/route
FINAL, snapshot, cursor, pagination, performance or currentness acceptance.

A later separately authorized canary must reuse the actual
app.cn.discovery_preliminary_publication.build_page_sql contract on both
backends with identical bounded ranges and SQL execution limits, verify the
frozen CN serving epoch before/after both reads, and compare candidates,
source references, query identity, snapshot, cursor and provenance. Any
mismatch or resource-limit overflow must fail closed without promoting the
target. No global ClickHouse endpoint change is permitted.

## Bounded compaction proposal (separate future authority)

hot_cn total/free: 1,081,101,176,832 / 521,296,957,440 bytes.
30% filesystem reserve floor: 324,330,353,050 bytes.
Available above floor: 196,966,604,390 bytes. Reserve an additional 64 GiB
safety buffer; propose at most 16 GiB input parts and at most 32 GiB peak
temporary scratch allocation per serial operation. These are conservative
planning caps, not enforceable ClickHouse commands.

Goods current and observed events each use a single tuple() partition.
A full goods partition rewrite exceeds the reserve envelope; no whole-table
OPTIMIZE FINAL for either table. Exact candidate part sets, bounded execution
controls, peak scratch/RSS proof, post-merge logical-currentness evidence
and rollback must be reviewed before any controlled Apply. Do not enable
background merges globally to substitute for an exact bounded operator.

## Governance

No new execution authority is implied. Read-canary Apply and merge Apply need
different frozen hashes and independent admission. Keep both API writers
stopped, source/target six-table merges stopped, source six tables retained,
and issue #837 open until serving and writing are separately accepted;
reclaim source storage only in the final separately authorized phase.
