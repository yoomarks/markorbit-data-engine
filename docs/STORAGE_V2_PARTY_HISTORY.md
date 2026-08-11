# Storage V2 CN PARTY history

Storage V2 uses `cn_observed_event` as the canonical durable history for CN OWNER, CO_OWNER, and AGENT relation observations and supersessions.

The legacy M1.5 publisher writes each PARTY relation event before writing the parallel `cn_case_party_relation_history` row. Retry cleanup removes both families by `source_package_id`. Before Storage V2, `OBSERVED_CURRENT` history also accumulated unchanged package-level repeats that the relation-event predicate correctly did not emit.

M1.6 therefore keeps:

- current relation state in `cn_case_party_current`;
- canonical relation observations and supersessions in `cn_observed_event`;
- authoritative source packages in raw storage.

M1.6 no longer persists the parallel wide rows in `cn_case_party_relation_history`. The table remains present but empty for schema compatibility after compaction.

The guarded compactor validates coverage per role before removing legacy rows:

- history `OBSERVED_CURRENT` count must be greater than or equal to canonical `*_RELATION_OBSERVED` event count; any excess is legacy no-op observation history;
- history `SUPERSEDED` count must exactly equal canonical `*_RELATION_SUPERSEDED_OBSERVED` event count;
- only OWNER, CO_OWNER, and AGENT roles and the known actions are accepted;
- any unknown role/action or event-coverage deficit fails closed.

Read-only plan:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-party-history.ps1 -Mode Plan
```

Single-process commit:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-party-history.ps1 -Mode Commit
```

Read-only status:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\compact-cn-party-history.ps1 -Mode Status
```

Commit creates an empty schema-compatible shadow, revalidates canonical event coverage, atomically exchanges the tables, verifies that the active history table is empty and the PARTY event profile is unchanged, then drops only the validated legacy shadow. The final DROP uses a query-scoped `max_table_size_to_drop=0`; global ClickHouse configuration is not changed.
