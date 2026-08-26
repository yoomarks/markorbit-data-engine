# CN bounded sample acceptance

Routine CN release regression must not reprocess the production `2023_5.zip` corpus when the cost is comparable to a full replay.

The bounded sample gate is functional evidence, not full-corpus scale evidence. `app.cn.validate_sample_package_e2e` generates deterministic small CN ZIP packages inside an isolated CI database and executes the real package-control and ingestion path: package registration, ZIP/CSV parsing, staging, publish, current/history projection, a later monthly observation, and duplicate-SHA registration protection.

The sample deliberately exercises direct CN cases, a multi-class case, Madrid G-number root/derived cases, owner/co-owner relations, goods scopes, priority data, agent data, owner supersession, lineage, relation, and scope-carve-out behavior. It must remain small enough to finish in minutes.

Large-corpus resource risks are separate evidence. Grace-hash JOIN behavior, spillable exact uniqueness, deterministic party uniqueness buckets, and other memory-safety contracts remain covered by their dedicated CI regressions. A passing bounded sample must never be described as proof that multi-billion-row queries are safe at production scale.

Persisted target-host acceptance evidence is also separate. `scripts/check-cn-acceptance-receipt.ps1` validates a saved receipt locally without Docker or database connections. It does not create missing full-corpus evidence and fails closed when a receipt is only `READY_TO_CONTINUE`, lacks a final checkpoint, or otherwise does not represent accepted runtime evidence.
