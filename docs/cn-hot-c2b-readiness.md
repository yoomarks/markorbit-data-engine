# CN Hot C2b readiness and next gates (#843, parent #837)

R9 accepted six hot_cn_only tables (1,908,623,840 active physical rows).
This review does not authorize serving/writer cutover, target FINAL,
START MERGES, OPTIMIZE, source cleanup, or Docker/WSL mutation.

## Reproducible evidence

Run scripts/audit_cn_hot_c2b_readiness.py from an isolated clean worktree
on the accepted production host with --repo, --evidence-root, and a new
--output path. It checks frozen R9 receipt hashes, invokes the existing
independent R9 auditor, uses only system-table metadata SELECT queries,
guards the CN GET and admission POST route inventories, and will not
overwrite an existing evidence file.

Accepted initial live evidence:
D:\yoomarks\governed-plans\843\cn-hot-c2b-readiness-r2.json
SHA-256: b23bdf8441a20d9c222ca6b9dde288f146facd527b97604d948aac4a62ff04ab.
Its result is CUTOVER_NOT_AUTHORIZED, not migration failure.

## Serving route

Compose API and worker CLICKHOUSE_HOST=clickhouse points to the source.
Source has 68 tables; target has 38. Do not change the shared address:
several US and CN serving tables still exist only on the source.

Nine CN GET paths are inventoried. The CN case route requires
cn_case_relation_current in addition to the six migrated tables.
CN agent routes require cn_agent_current; name lookup also requires
cn_agent_name_candidate_lookup. These tables are absent on the target.
Summary, relationship and entity portfolio paths have other source-only
dependencies. Keep these routes source-bound.

The preliminary-publication discovery route currently uses only
cn_case_current (present on target), but also remains source-bound until
a separately frozen bounded point-read parity and serving-epoch proof.
Each future read must have explicit row/byte/time limits and matching
snapshot/cursor semantics; fail closed to the source on any mismatch.
Never mix source and target epochs silently. Do not resume CN writes
before approved currentness, replication or dual-write semantics.

The three CN fact-admission POST routes remain source-write-only and
disabled in the stopped API; they are not part of a read-only canary.

## Controlled merge capacity

Post-R9 hot_cn free: 521,296,957,440 bytes; ext4 total:
1,081,101,176,832 bytes. At a 30% filesystem reserve, the approximate
temporary space above reserve is 196,966,604,390 bytes.
cn_goods_item_current has 1,842 active parts / 233,881,500,902 bytes
in one tuple() partition: rewriting that entire partition exceeds
the temporary envelope before amplification. cn_observed_event has
740 active parts / 131,919,887,426 bytes in one tuple() partition;
its raw size alone is not proof of safe compaction.

Freeze a separate merge operator with measured candidate input/output
part sizes, scratch allocation, memory admission, serial execution,
live abort thresholds, stopped-merge recovery, D/E reserves and an
independent post-merge logical-currentness proof. No unbounded whole-
table OPTIMIZE FINAL or global target merge restart under this issue.

## Approval boundary

#843 is read-only. R9 accepted copies and source data remain intact.
Future route/canary and compaction Apply stages need separate reviewed
plan SHAs, exact authority and rollback/proof. Source reclamation is a
later phase after accepted serving and writer cutover, never a shortcut
to create temporary compaction space.
